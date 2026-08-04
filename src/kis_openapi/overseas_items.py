"""해외 계좌 DATA -- :class:`OverseasPosition`.

해외 잔고(:meth:`~kis_openapi.client.KisClient.overseas_positions`)가 돌려주는 보유 종목 한 건.
금액은 종목 통화(USD/HKD/JPY/...)라 :class:`~kis_openapi.money.Money` 로 통화를 함께 담는다 --
국내 :class:`~kis_openapi.balance.Position`(KRW Decimal)와 달리 다통화이기 때문이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from .money import Money


@dataclass(frozen=True, slots=True)
class OverseasPosition:
    """해외 보유 종목 한 건(불변). 금액은 종목 통화의 :class:`Money`.

    ``quantity`` 는 보유 수량, ``sellable_quantity`` 는 매도가능 수량. ``unrealized_pnl`` 은 외화
    평가손익, ``pnl_percent`` 는 평가손익률(%).
    """

    symbol: str
    name: str
    exchange: str                     # 조회한 해외거래소코드(OVRS_EXCG_CD)
    quantity: int                     # 보유 수량
    sellable_quantity: int            # 매도가능 수량
    average_price: Money              # 매입 평균가
    current_price: Money              # 현재가
    purchase_amount: Money            # 외화 매입금액
    market_value: Money               # 평가금액
    unrealized_pnl: Money             # 외화 평가손익
    pnl_percent: Decimal              # 평가손익률(%)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
