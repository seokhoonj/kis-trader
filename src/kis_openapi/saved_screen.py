"""HTS 저장 조건검색(DATA)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class SavedScreen:
    """HTS에 서버 저장된 종목검색 조건."""

    user_id: str
    sequence: str
    group_name: str
    condition_name: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class SavedScreenStock:
    """저장 조건에 일치한 종목의 시세."""

    symbol: str
    name: str
    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    amount: Decimal
    strength: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    week_52_high: Decimal
    week_52_low: Decimal
    expected_price: Decimal
    expected_change: Decimal
    expected_change_percent: Decimal
    expected_volume: int
    volume_change_percent: Decimal
    base_price: Decimal
    upper_limit: Decimal
    lower_limit: Decimal
    market_cap: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class WatchlistGroup:
    """HTS 관심종목 그룹."""

    date: str
    transmitted_at: str
    rank: str
    code: str
    name: str
    requested_count: int
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class WatchlistStock:
    """관심종목 그룹에 저장된 종목."""

    market_code: str
    rank: str
    exchange_code: str
    symbol: str
    color_code: str
    memo: str
    name: str
    base_date_net_buy_quantity: int
    execution_price: Decimal
    execution_class_code: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class Watchlist:
    """관심종목 그룹 요약과 저장 종목 목록."""

    rank: str
    name: str
    stocks: tuple[WatchlistStock, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "stocks", tuple(self.stocks))
