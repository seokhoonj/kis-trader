"""국내주식 현재가 스냅샷(DATA) -- :class:`Quote` 와 :func:`parse_quote`.

:class:`Quote` 는 한 종목의 **가격 스냅샷**이다(체결가/시고저/전일대비/거래량). 호가(bid/ask)와
호가잔량은 여기 없다 -- KIS는 그것을 별도 엔드포인트(주식현재가 호가)로 주므로, 스냅샷에 넣으면
항상 비는 죽은 필드가 된다. 호가/심도는 :class:`OrderBook`(다음 슬라이스)의 몫이다.

KIS URL/TR-id:
- 주식현재가 시세: ``GET /uapi/domestic-stock/v1/quotations/inquire-price``
  실전/모의 공통 ``FHKST01010100`` (모의투자 지원).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal

from ..._wire import optional_decimal, required_decimal, required_int

#: 조회 대상 시장 보드. KIS 조건시장분류코드로는 KRX=J / NXT=NX / UN=통합(변환은 파사드가).
Market = Literal["KRX", "NXT", "UN"]

#: 전일대비 부호 코드(``prdy_vrss_sign``) 중 하락을 뜻하는 값: 4 하한, 5 하락.
#: 1 상한 / 2 상승 / 3 보합은 양(0 포함)으로 본다. KIS는 대비 크기를 부호 없는 크기로 주고
#: 방향을 이 코드로 따로 주므로, 부호를 여기서 복원한다.
_DOWN_SIGNS = frozenset(("4", "5"))


@dataclass(frozen=True, slots=True)
class Quote:
    """한 종목의 현재가 스냅샷(불변).

    ``change`` / ``change_percent`` 는 전일대비로, 하락이면 음수다. ``previous_close`` 는
    KIS 기준가(``stck_sdpr``)로, 정상 세션에서는 전일 종가와 같다. ``as_of`` 는 조회 시각
    (KST)이다 -- KIS 스냅샷 자체에는 타임스탬프가 없다.
    """

    symbol: str
    market: Market                    # 조회한 시장 보드: KRX / NXT / UN
    currency: str                     # 국내는 항상 KRW
    last: Decimal                     # 현재가(stck_prpr)
    open: Decimal
    high: Decimal
    low: Decimal
    previous_close: Decimal           # 기준가(stck_sdpr)
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(acml_vol)
    week_52_high: Decimal | None
    week_52_low: Decimal | None
    as_of: datetime                   # 데이터 유효 시각 = 조회 시각(KST-aware)
    # raw 는 원본 와이어의 읽기전용 뷰(출처 보존용). 동등성/해시/repr 에서 제외한다 --
    # 값 동일성은 파싱된 필드로 정하고(같은 시세면 같은 Quote), dict 는 unhashable 이라
    # 포함하면 frozen 인데도 hash(quote) 가 TypeError 를 낸다.
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        # frozen 은 재바인딩만 막으므로, raw 를 읽기전용 뷰로 감싼다(얕은 스냅샷).
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


def parse_quote(output: Mapping[str, Any], *, symbol: str, market: Market, as_of: datetime) -> Quote:
    """KIS 현재가 응답의 ``output`` 블록을 :class:`Quote` 로(순수).

    ``symbol`` / ``market`` / ``as_of`` 는 요청 맥락에서 온다(응답이 돌려주지 않음). 수치 파싱은
    fail-closed -- 필수 필드가 비거나 파싱 실패면 :class:`~kis_openapi.errors.KisError`.
    """
    change_sign = str(output.get("prdy_vrss_sign", "")).strip()
    return Quote(
        symbol=symbol,
        market=market,
        currency="KRW",
        last=required_decimal(output.get("stck_prpr"), "stck_prpr"),
        open=required_decimal(output.get("stck_oprc"), "stck_oprc"),
        high=required_decimal(output.get("stck_hgpr"), "stck_hgpr"),
        low=required_decimal(output.get("stck_lwpr"), "stck_lwpr"),
        previous_close=required_decimal(output.get("stck_sdpr"), "stck_sdpr"),
        change=_signed(required_decimal(output.get("prdy_vrss"), "prdy_vrss"), change_sign),
        change_percent=_signed(required_decimal(output.get("prdy_ctrt"), "prdy_ctrt"), change_sign),
        volume=required_int(output.get("acml_vol"), "acml_vol"),
        week_52_high=optional_decimal(output.get("w52_hgpr"), "w52_hgpr"),
        week_52_low=optional_decimal(output.get("w52_lwpr"), "w52_lwpr"),
        as_of=as_of,
        raw=output,
    )


def _signed(magnitude: Decimal, sign_code: str) -> Decimal:
    """전일대비 크기에 방향 부호를 입힌다(하락 코드면 음수)."""
    return -magnitude if sign_code in _DOWN_SIGNS else magnitude
