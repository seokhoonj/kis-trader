"""해외 시세 DATA -- :class:`OverseasCurrentPrice`.

해외 종목 현재체결가·거래량 스냅샷(``kis.overseas.stock(...).current_price``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class OverseasCurrentPrice:
    """해외주식 현재체결가의 간결한 가격·누적거래 스냅샷."""

    symbol: str
    exchange: str
    current_price: Decimal
    previous_close: Decimal
    change: Decimal
    change_percent: Decimal
    previous_volume: int
    volume: int
    traded_amount: Decimal
    decimal_places: int
    buyable_status: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
