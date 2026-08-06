"""해외 종목 상품기본정보 엔티티."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class OverseasProductInfo:
    """해외 종목의 거래소·통화·상장정보 등 상품기본정보(불변)."""

    symbol: str
    isin: str
    name: str
    english_name: str
    exchange_code: str
    exchange_name: str
    country: str
    currency: str
    currency_name: str
    par_value: Decimal | None
    listed_shares: int | None
    buy_unit: int | None
    sell_unit: int | None
    sedol: str
    bloomberg_ticker: str
    is_listed: bool
    is_delisted: bool
    taxable: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
