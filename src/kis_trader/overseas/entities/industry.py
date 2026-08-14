"""해외 업종/섹터 DATA -- 업종 구성종목과 요약.

해외 업종별 종목 구성·시세 조회가 돌려주는 DATA."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class OverseasIndustry:
    """해외 거래소의 업종(섹터) 코드 한 건(불변)."""

    code: str
    name: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasIndustryStock:
    """해외 거래소의 한 업종에 속한 종목 시세(불변)."""

    exchange: str
    symbol: str
    name: str
    english_name: str
    current_price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    ask_price: Decimal
    ask_quantity: int
    bid_price: Decimal
    bid_quantity: int
    rank: int
    is_tradable: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
