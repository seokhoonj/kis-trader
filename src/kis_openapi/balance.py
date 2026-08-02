"""계좌 잔고(DATA) -- :class:`Position`, :class:`Balance`, :class:`Portfolio`.

:class:`Position` 은 한 종목의 보유 현황, :class:`Balance` 는 계좌 현금·자산 요약,
:class:`Portfolio` 는 그 둘을 한 스냅샷으로 묶은 것이다. 금액은 종목/계좌 통화의 Decimal
(국내는 KRW). 다중통화가 필요해지면 ``Money`` 로 승격한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class Position:
    """한 종목의 보유 현황(불변).

    ``average_purchase_price`` 는 매입평균단가(취득원가)로, 체결가 평균인
    ``ExecutionReport.average_price`` 와 다른 개념이라 이름을 구분한다. 0수량 잔여 lot(정산
    대기)도 그대로 담긴다 -- 필터는 호출자 몫.
    """

    symbol: str
    security_name: str
    currency: str
    quantity: Decimal
    sellable_quantity: Decimal
    average_purchase_price: Decimal
    purchase_amount: Decimal
    current_price: Decimal
    market_value: Decimal             # 평가금액 = 현재가 x 수량
    unrealized_pnl: Decimal
    unrealized_pnl_percent: Decimal
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


@dataclass(frozen=True, slots=True)
class Balance:
    """계좌의 현금·자산 요약(불변).

    ``deposit`` 예수금총액, ``settlement_cash_d1`` / ``settlement_cash_d2`` D+1 / D+2 정산예정 현금
    (D+2가 실질 인출가능), ``net_asset`` 순자산(현금+평가), ``market_value`` 보유 종목 평가금액
    합계, ``total_evaluation`` KIS 총평가금액, ``unrealized_pnl`` 평가손익 합계.
    """

    currency: str
    deposit: Decimal
    settlement_cash_d1: Decimal
    settlement_cash_d2: Decimal
    total_evaluation: Decimal
    net_asset: Decimal
    purchase_amount: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


@dataclass(frozen=True, slots=True)
class Portfolio:
    """계좌 스냅샷 -- 현금·자산 요약과 보유 종목 한 벌(무거운 조회를 한 번만)."""

    balance: Balance
    positions: tuple[Position, ...]
