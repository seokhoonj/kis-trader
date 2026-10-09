"""호가창(DATA) -- :class:`OrderBook` 와 :class:`PriceLevel`.

매수/매도 호가와 잔량(Level 2 / market depth) -- 국내·미국 주식 10단계, 그 외 해외 주식 1단계,
채권/파생 5단계(가격 있는 단계만). ``bids``/``asks`` 는 최우선 호가가
index 0(top of book)이고, 가격이 있는 단계만 담는다.
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
class PriceLevel:
    """호가창 한 단계 -- 가격과 그 가격의 잔량(대기 주문 수량)."""

    price: Decimal
    quantity: int


@dataclass(frozen=True, slots=True)
class OrderBook:
    """한 종목의 호가창 스냅샷(불변).

    ``bids`` 는 매수호가(가격 높은 순, index 0 = 최우선), ``asks`` 는 매도호가(낮은 순, index 0
    = 최우선), 가격 있는 단계만. ``as_of`` 는 조회 시각(KST-aware).
    """

    symbol: str
    #: 여러 자산군이 함께 쓰는 스냅샷의 일반 시장/구분(주식=거래소, 파생=``DerivativeMarket``, 채권=시장구분).
    #: 주식 전용 핸들과 주문 계층은 더 정확한 이름인 ``exchange`` 를 쓴다.
    market: str
    bids: tuple[PriceLevel, ...]
    asks: tuple[PriceLevel, ...]
    total_bid_quantity: int
    total_ask_quantity: int
    as_of: datetime
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "bids", tuple(self.bids))
        object.__setattr__(self, "asks", tuple(self.asks))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
