"""해외 참조 DATA -- 업종·종목검색·뉴스·권리·기업행위.

해외 시장 참조 조회(업종 구성·종목 검색·뉴스 헤드라인·권리/기업행위)가 돌려주는 DATA."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
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


@dataclass(frozen=True, slots=True)
class OverseasNewsHeadline:
    """해외뉴스 종합 피드의 제목 한 건."""

    news_type: str
    key: str
    timestamp: datetime
    category_code: str
    category_name: str
    source: str
    country_code: str
    exchange_code: str
    symbol: str
    symbol_name: str
    title: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasRight:
    """해외증권의 배당·증자·합병 등 기간별 권리."""

    base_date: date | None
    right_type_code: str
    symbol: str
    name: str
    product_type: str
    standard_symbol: str
    local_base_date: date | None
    subscription_start_date: date | None
    subscription_end_date: date | None
    cash_allocation_rate: Decimal | None
    stock_allocation_rate: Decimal | None
    currencies: tuple[str, ...]
    allocation_price: Decimal | None
    dividends_per_share: tuple[Decimal | None, ...]
    is_final: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "currencies", tuple(self.currencies))
        object.__setattr__(self, "dividends_per_share", tuple(self.dividends_per_share))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasCorporateAction:
    """외부 권리정보 제공사가 집계한 해외종목 기업행사 일정."""

    announced_date: date | None
    title: str
    ex_dividend_date: date | None
    payment_date: date | None
    record_date: date | None
    validity_date: date | None
    local_deadline: date | None
    ex_rights_date: date | None
    delisting_date: date | None
    redemption_date: date | None
    early_redemption_date: date | None
    effective_date: date | None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
