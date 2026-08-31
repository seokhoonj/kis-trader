"""국내주식 TWAP 분할주문의 순수 스케줄 플래너 -- I/O 0, 주입 시각으로 결정적.

TWAP(Time Weighted Average Price) = 총 수량을 시간에 걸쳐 균등 분할해 여러 번 발주한다. 이 모듈은
발주 시각·수량의 스케줄(:class:`TWAPSchedule`)만 계산하고, 실제 발주는 :mod:`.runner` 가 맡는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .._internal._datetime import _KST
from ..errors import KISUsageError
from ..order import Side

#: KRX 정규장 시간대(09:00~15:30 KST). 09:00~15:20 은 연속매매, 15:20~15:30 은 종가 단일가다 --
#: 후자 구간에 걸친 슬라이스도 유효한 주문이되 15:30 단일가로 체결돼 TWAP 의미가 흐려질 수 있다.
#: 모든 슬라이스가 이 창 안이어야 한다(시간외/NXT 는 MVP 밖).
_SESSION_OPEN = time(9, 0)
_SESSION_CLOSE = time(15, 30)
#: "1h30m"/"30m"/"90s" 형식 파서. 세 그룹 모두 선택이지만 최소 하나는 있어야 한다.
_DURATION_RE = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


@dataclass(frozen=True, slots=True)
class TWAPSlice:
    """한 분할 슬라이스 -- 발주 예정 시각(KST tz-aware)과 수량(주)."""

    at: datetime
    quantity: int


@dataclass(frozen=True, slots=True)
class TWAPSchedule:
    """TWAP 분할 스케줄(불변) -- 종목·방향과 슬라이스 목록. 순수 플래너의 산출물."""

    symbol: str
    side: Side
    slices: tuple[TWAPSlice, ...]

    @property
    def total_quantity(self) -> int:
        return sum(s.quantity for s in self.slices)


def split_quantity(total: int, parts: int) -> list[int]:
    """``total`` 을 ``parts`` 개로 균등 분할한다 -- 나머지 r 은 앞쪽 r 개 슬라이스에 +1 씩. 슬라이스당
    최소 1주라 ``total < parts`` 는 거부한다(0주 슬라이스는 발주 불가)."""
    if total <= 0:
        raise KISUsageError(f"총 수량은 0보다 커야 한다: {total}")
    if parts <= 0:
        raise KISUsageError(f"슬라이스 수는 0보다 커야 한다: {parts}")
    if total < parts:
        raise KISUsageError(f"총 수량({total})이 슬라이스 수({parts})보다 작다 -- 슬라이스당 최소 1주.")
    base, remainder = divmod(total, parts)
    return [base + 1 if i < remainder else base for i in range(parts)]


def parse_duration(value: str | timedelta) -> timedelta:
    """총 소요시간을 :class:`~datetime.timedelta` 로 -- ``timedelta`` 는 그대로, 문자열은 "1h30m"/"30m"/
    "90s" 형식으로 파싱한다. 0 이하·형식오류·과대값은 :class:`~kis_trader.errors.KISUsageError`."""
    if isinstance(value, timedelta):
        if value <= timedelta(0):
            raise KISUsageError(f"총 소요시간은 0보다 커야 한다: {value}")
        return value
    match = _DURATION_RE.fullmatch(value.strip())
    if match is None or not any(match.groups()):
        raise KISUsageError(f"소요시간 형식이 잘못됐다(예: 30m/1h/1h30m/90s): {value!r}")
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    try:
        duration = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    except OverflowError as err:
        raise KISUsageError(f"소요시간이 너무 크다: {value!r}") from err
    if duration <= timedelta(0):
        raise KISUsageError(f"총 소요시간은 0보다 커야 한다: {value!r}")
    return duration


def make_twap_schedule(
    *, symbol: str, side: Side, quantity: int, duration: str | timedelta, slices: int,
    start: datetime | None = None, now: datetime | None = None,
) -> TWAPSchedule:
    """TWAP 스케줄을 만든다(순수). ``interval = duration/slices``, 슬라이스 k 의 시각 = base + k*interval
    (base = ``start`` 또는 ``now`` 또는 현재 KST), 수량 = :func:`split_quantity`.

    fail-closed 로 거부(:class:`~kis_trader.errors.KISUsageError`)하는 경우:
    ``slices`` <= 0 / ``quantity`` <= 0 / ``quantity`` < ``slices``; ``start``/``now`` 가 naive
    (tz 없음 -- 호스트 시간대로 오해될 수 있어 KST tz-aware 를 요구); base 가 ``now`` 보다 과거
    (과거 시작은 모든 슬라이스가 즉시 발주돼 연속 시장가 덤프가 되므로); 어느 슬라이스라도 base 당일의
    KRX 정규장(09:00~15:30 KST) 창 밖(시각뿐 아니라 날짜까지 -- 다음 날로 넘어가는 슬라이스도 거부)."""
    if slices <= 0:
        raise KISUsageError(f"슬라이스 수는 0보다 커야 한다: {slices}")
    span = parse_duration(duration)
    resolved_now = now if now is not None else datetime.now(_KST)
    base = start if start is not None else resolved_now
    if base.tzinfo is None:
        raise KISUsageError("start/now 는 KST tz-aware datetime 이어야 한다(naive 는 호스트 시간대로 오해됨).")
    if base < resolved_now:
        raise KISUsageError(
            f"TWAP 시작 시각이 과거다({base.isoformat()}) -- 과거 시작은 모든 슬라이스를 즉시 연속 발주"
            "(버스트)해 TWAP 를 무의미하게 만든다. 미래 시각을 주거나 start 를 생략하라(지금부터)."
        )
    interval = span / slices
    quantities = split_quantity(quantity, slices)
    built = tuple(
        TWAPSlice(at=base + interval * index, quantity=qty)
        for index, qty in enumerate(quantities)
    )
    session_open = datetime.combine(base.date(), _SESSION_OPEN, tzinfo=_KST)
    session_close = datetime.combine(base.date(), _SESSION_CLOSE, tzinfo=_KST)
    for entry in built:
        if not (session_open <= entry.at <= session_close):
            raise KISUsageError(
                f"TWAP 슬라이스가 base 당일의 KRX 정규장(09:00~15:30 KST) 밖이다: {entry.at.isoformat()} "
                f"-- 시작시각/소요시간/슬라이스 수를 조정하라(시간외/NXT/익일 이월은 미지원)."
            )
    return TWAPSchedule(symbol=symbol, side=side, slices=built)
