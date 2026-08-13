"""주문가능 여력(DATA) -- :class:`BuyableAmount`, :class:`SellableQuantity`.

주문 전 사전점검값. :class:`BuyableAmount` 는 얼마나 살 수 있는지(현금 기준·미수 포함 최대),
:class:`SellableQuantity` 는 얼마나 팔 수 있는지(보유 중 주문가능 수량). 금액은 KRW Decimal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class BuyableAmount:
    """한 종목의 매수가능 여력(불변).

    ``cash_buyable_*`` 는 미수(외상) 없는 현금 기준, ``max_buyable_*`` 는 미수 포함 최대. 종목/
    단가 없이(금액만) 조회하면 종목별 수량·금액은 0이다.
    """

    symbol: str
    currency: str
    orderable_cash: Decimal
    reusable_cash: Decimal
    cash_buyable_amount: Decimal
    cash_buyable_quantity: Decimal
    max_buyable_amount: Decimal
    max_buyable_quantity: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class SellableQuantity:
    """한 종목의 매도가능 수량(불변).

    ``quantity`` 보유 잔고수량, ``sellable_quantity`` 그중 주문(매도) 가능 수량(미결제·담보로
    둘이 다를 수 있음). 미보유 종목이면 둘 다 0.
    """

    symbol: str
    security_name: str
    quantity: Decimal
    sellable_quantity: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
