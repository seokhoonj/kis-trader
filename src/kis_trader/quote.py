"""현재가 스냅샷(DATA) -- :class:`Quote`.

한 종목의 가격 스냅샷(체결가/시고저/전일대비/거래량)이다. 호가(bid/ask)와 잔량은 여기 없다 --
그건 :class:`~kis_trader.order_book.OrderBook` 의 몫이다. ``Quote`` 는 어떤 시장이든(국내/해외)
같은 형태로 돌려주는 통합 반환 타입이고, KIS 원본 필드 매핑은 내부 조회 계층 docstring에 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class Quote:
    """한 종목의 현재가 스냅샷(불변).

    ``change`` / ``change_percent`` 는 전일대비로 하락이면 음수. ``previous_close`` 는 기준가
    (정상 세션에선 전일 종가). ``as_of`` 는 데이터 유효(조회) 시각. 금액은 종목 통화의 Decimal.
    """

    symbol: str
    #: 종목(주식)의 거래소 코드(국내 KRX/NXT/UN, 해외 NAS/NYS/...). ``Quote`` 는 국내·해외 주식
    #: 현재가에만 쓰이고(파생·채권은 자기 전용 quote 타입) 저장값은 항상 거래소 코드라, 시장구분
    #: (KOSPI/KOSDAQ)을 뜻하는 ``market`` 과 구별해 주식 핸들·주문 계층과 같은 ``exchange`` 로 통일한다.
    exchange: str
    currency: str                     # 국내는 KRW
    current_price: Decimal            # 현재가
    open: Decimal
    high: Decimal
    low: Decimal
    previous_close: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int
    week_52_high: Decimal | None
    week_52_low: Decimal | None
    as_of: datetime                   # KST-aware
    # _raw 는 원본 와이어의 읽기전용 뷰(안정 API 아님, 미매핑 필드 접근용). 동등성/해시/repr 제외 -- 값 동일성은 파싱된 필드로,
    # dict 는 unhashable 이라 포함하면 frozen 인데도 hash() 가 TypeError.
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
