"""HTS 저장 조건검색(DATA)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._freeze import freeze_vendor_payload


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
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class SavedScreenStock:
    """저장 조건에 일치한 종목의 시세.

    가격 필드(``current_price`` / ``open_price`` / ``high_price`` / ``low_price`` /
    ``week_52_high`` / ``week_52_low`` / ``expected_price`` / ``base_price`` / ``upper_limit`` /
    ``lower_limit``)와 그 전일대비(``price_change`` / ``expected_change``)는 원,
    ``cumulative_volume`` / ``expected_volume`` 은 주, ``change_percent`` /
    ``expected_change_percent`` / ``volume_change_percent`` 은 % 다.
    """

    symbol: str
    name: str
    current_price: Decimal
    price_change: Decimal
    change_percent: Decimal
    cumulative_volume: int
    trading_amount: Decimal
    execution_strength: Decimal
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
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
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class WatchlistGroup:
    """HTS 관심종목 그룹."""

    date: date
    transmitted_at: time
    rank: int
    code: str
    name: str
    requested_count: int
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class WatchlistStock:
    """관심종목 그룹에 저장된 종목."""

    market_code: str
    rank: int
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
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class Watchlist:
    """관심종목 그룹 요약과 저장 종목 목록."""

    rank: int
    name: str
    stocks: tuple[WatchlistStock, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "stocks", tuple(self.stocks))
