"""해외 종목검색 DATA -- 조건검색 결과와 매칭 종목.

해외 종목 조건검색(``kis.overseas.search_stocks``)이 돌려주는 매칭 목록."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class OverseasStockSearchMatch:
    """해외 조건검색 결과 종목."""

    realtime_symbol: str
    exchange: str
    symbol: str
    name: str
    english_name: str
    price: Decimal
    change: Decimal
    change_percent: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    volume: int
    amount: Decimal
    shares: Decimal
    market_cap: Decimal
    eps: Decimal | None
    per: Decimal | None
    rank: int
    is_tradable: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasStockSearch:
    """해외 조건검색 건수 요약과 전체 연속조회 결과."""

    exchange: str
    decimal_places: int
    status: str
    total_count: int
    matches: tuple[OverseasStockSearchMatch, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "matches", tuple(self.matches))
