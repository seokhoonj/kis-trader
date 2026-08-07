"""상품 공통 기본정보(DATA) -- :class:`ProductInfo`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class ProductInfo:
    """주식·파생·채권·해외주식에 공통인 상품 등록·판매 기본정보(불변)."""

    symbol: str
    product_type: str
    name: str
    long_name: str
    short_name: str
    english_name: str
    long_english_name: str
    short_english_name: str
    standard_symbol: str
    compact_symbol: str
    sale_status_code: str
    risk_grade_code: str
    classification_code: str
    classification_name: str
    sale_start_date: date | None
    sale_end_date: date | None
    wrap_asset_type_code: str
    investment_product_type_code: str
    investment_product_type_name: str
    first_registered_date: date | None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
