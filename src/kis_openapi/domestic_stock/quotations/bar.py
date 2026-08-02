"""국내주식 기간별 OHLCV 바(DATA) -- :class:`Bar`, :type:`Interval`, :func:`parse_bars`.

한 개의 시간 버킷(일/주/월봉)을 :class:`Bar` 로 표현한다(OHLCV + 버킷 시작 시각). ``interval``
어휘는 Yahoo 스타일 합성 토큰(``m``=분, ``mo``=월)으로, 분/월 충돌을 피한다. ``Interval`` 은
현재 지원하는 토큰만 담아 정직하게 유지한다 -- 지금은 일/주/월(``1d``/``1wk``/``1mo``). 분봉
(``1m``/``5m``/``15m``/``30m``/``1h``)은 전용 시간앵커 페이지네이션·세션 처리가 있어 다음
슬라이스에서 ``Interval`` 을 확장하며 추가한다(그때까지 문자열로 넘기면 명확히 거부).

KIS URL/TR-id:
- 국내주식 기간별시세(일/주/월/년): ``GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice``
  실전/모의 공통 ``FHKST03010100`` (모의투자 지원). 응답 ``output2`` 가 바 배열.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal

from ..._wire import required_decimal, required_int
from ...errors import KisError, KisUsageError

#: 바 간격 -- 현재 지원하는 토큰만(Yahoo 스타일; mo=월). 분봉은 다음 슬라이스에서 확장.
Interval = Literal["1d", "1wk", "1mo"]

#: interval -> KIS 기간분류코드(FID_PERIOD_DIV_CODE).
_PERIOD_BY_INTERVAL = {"1d": "D", "1wk": "W", "1mo": "M"}

#: 아직 미구현인 분봉 토큰(문자열로 넘어오면 "다음 슬라이스" 안내로 거부; 전용 시간앵커 필요).
_MINUTE_INTERVALS = frozenset(("1m", "5m", "15m", "30m", "1h"))

_KST = timezone(timedelta(hours=9))


@dataclass(frozen=True, slots=True)
class Bar:
    """한 시간 버킷의 OHLCV(불변). ``timestamp`` 는 버킷 시작 시각(KST-aware).

    일/주/월봉의 ``timestamp`` 는 해당 날짜의 자정(KST)이다.
    """

    symbol: str
    timestamp: datetime               # 버킷 시작 시각(KST-aware)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    # raw 는 원본 와이어 뷰(출처 보존). 동등성/해시/repr 제외 -- 값 동일성은 OHLCV+시각으로
    # 정하고, dict 는 unhashable 이라 포함하면 frozen 인데도 hash(bar) 가 TypeError 를 낸다.
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


def period_code_for(interval: Interval) -> str:
    """interval 토큰을 KIS 기간분류코드로. 분봉은 아직 미구현, 미지 토큰은 사용자 오류."""
    if interval in _PERIOD_BY_INTERVAL:
        return _PERIOD_BY_INTERVAL[interval]
    if interval in _MINUTE_INTERVALS:
        raise NotImplementedError(
            f"분봉({interval})은 아직 미구현이다 -- 다음 슬라이스. 현재는 1d/1wk/1mo(일/주/월)만."
        )
    raise KisUsageError(
        f"지원하지 않는 interval: {interval!r} (1m/5m/15m/30m/1h/1d/1wk/1mo 중 하나여야 한다)."
    )


def parse_bars(rows: Sequence[Mapping[str, Any]], *, symbol: str) -> list[Bar]:
    """KIS 기간별시세 ``output2`` 행들을 :class:`Bar` 리스트로(순수, 정렬은 호출자 몫).

    날짜나 종가가 빈 행은 건너뛴다 -- KIS는 아직 체결이 없는 세션(장 시작 전 당일 등)에
    빈 바를 하나 돌려줄 수 있는데, 이를 fail-closed 로 예외 내면 정상 조회가 통째로 깨진다.
    그 외의 필수 수치가 비거나 파싱 실패면 예외(잘못된 가격/거래량을 지어내지 않는다).
    """
    bars: list[Bar] = []
    for row in rows:
        date_text = str(row.get("stck_bsop_date", "")).strip()
        close_text = str(row.get("stck_clpr", "")).strip()
        if not date_text or not close_text:  # 미체결 세션의 빈 바 -- 건너뛴다
            continue
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=_bar_timestamp(date_text),
                open=required_decimal(row.get("stck_oprc"), "stck_oprc"),
                high=required_decimal(row.get("stck_hgpr"), "stck_hgpr"),
                low=required_decimal(row.get("stck_lwpr"), "stck_lwpr"),
                close=required_decimal(close_text, "stck_clpr"),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                raw=row,
            )
        )
    return bars


def _bar_timestamp(date_text: str) -> datetime:
    """``YYYYMMDD`` 문자열을 자정(KST) datetime 으로."""
    try:
        day = datetime.strptime(date_text, "%Y%m%d")  # noqa: DTZ007 -- 아래 .replace 로 KST-aware
    except ValueError as err:
        raise KisError(f"바 날짜(stck_bsop_date) 파싱 실패: {date_text!r}") from err
    return day.replace(tzinfo=_KST)
