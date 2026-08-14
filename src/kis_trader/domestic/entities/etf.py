"""ETF/ETN 시세 DATA -- :class:`ETFNAV`, :class:`ETFComponent`, :class:`ETFNAVHistoryPoint`.

ETF/ETN 은 호가창에서 거래되는 종목이라 시세/주문은 :class:`~kis_trader.domestic.stock.DomesticStock` 로 하고,
ETF 고유 정보만 이 타입들로 돌려준다. :class:`ETFNAV` 는 순자산가치 스냅샷
(:meth:`~kis_trader.domestic.stock.DomesticStock.nav`), :class:`ETFComponents` 는 구성종목(PDF) 목록과 ETF
요약(:meth:`~kis_trader.domestic.stock.DomesticStock.etf_components`) -- 각 항목은 :class:`ETFComponent` --,
:class:`ETFNAVHistoryPoint` 는 일별 NAV-가격 추이(:meth:`~kis_trader.domestic.stock.DomesticStock.nav_history`)의
한 점이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload
from ...order_book import OrderBook, PriceLevel


@dataclass(frozen=True, slots=True)
class ETFNAV:
    """ETF/ETN 순자산가치(NAV) 스냅샷(불변).

    ``nav`` 는 현재 NAV(순자산가치), ``premium`` 은 괴리율(시장가가 NAV 대비 얼마나 벗어났는지, %),
    ``tracking_error`` 는 추적오차율(%). ``nav_change`` / ``nav_change_percent`` 는 NAV 전일대비로
    하락이면 음수. 시장 체결가는 :meth:`~kis_trader.domestic.stock.DomesticStock.quote` 에 있다.
    """

    symbol: str
    nav: Decimal
    nav_change: Decimal               # NAV 전일대비(부호 포함)
    nav_change_percent: Decimal       # NAV 전일대비율(부호 포함)
    previous_nav: Decimal
    premium: Decimal                  # 괴리율(%): 시장가 vs NAV
    tracking_error: Decimal           # 추적오차율(%)
    net_assets: Decimal
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ETFComponent:
    """ETF 구성종목(PDF) 한 항목(불변).

    ETF 가 담고 있는 개별 종목 하나다. ``weight`` 는 ETF 안에서 차지하는 구성 비중(%),
    ``valuation`` 은 ETF 내 평가금액. ``change`` / ``change_percent`` 는 그 구성종목의 전일대비로
    하락이면 음수.
    """

    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    weight: Decimal                   # ETF 구성 비중(%)
    valuation: Decimal                # ETF 내 평가금액
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ETFComponentsSummary:
    """ETF 구성종목 조회의 ETF 자체 요약(불변) -- 시세·NAV·구성 규모.

    구성종목 목록(:class:`ETFComponent`)과 함께 오는 output1 요약이다. ``price`` 는 ETF 시장 체결가,
    ``nav`` 는 순자산가치, ``net_assets`` 는 ETF 순자산총액, ``components_market_cap`` 는 구성종목
    시가총액. ``change`` / ``nav_change`` 및 각 ``*_percent`` 는 전일대비로 하락이면 음수.
    ``cu_unit_shares`` 는 CU(설정/환매 단위) 1좌당 증권 수, ``component_count`` 는 구성종목 수.
    """

    price: Decimal                    # ETF 현재가
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    components_market_cap: Decimal    # ETF 구성종목 시가총액
    nav: Decimal
    nav_change: Decimal               # NAV 전일대비(부호 포함)
    nav_change_percent: Decimal       # NAV 전일대비율(부호 포함)
    net_assets: Decimal               # ETF 순자산총액
    previous_nav: Decimal             # NAV 전일종가
    nav_open: Decimal
    nav_high: Decimal
    nav_low: Decimal
    cu_unit_shares: int               # CU 1단위 증권 수
    component_count: int              # ETF 구성종목 수
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ETFComponents:
    """ETF 구성종목(PDF) 목록과 ETF 요약(불변).

    ``summary`` 는 ETF 자체 시세·NAV·구성 규모(:class:`ETFComponentsSummary`), ``components`` 는
    구성종목(:class:`ETFComponent`) 튜플이다.
    """

    summary: ETFComponentsSummary
    components: tuple[ETFComponent, ...]
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", tuple(self.components))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ETFNAVHistoryPoint:
    """일별 NAV-가격 추이의 한 점(불변).

    ``trading_date`` 그 거래일, ``close`` 시장 종가, ``nav`` 그 날 NAV, ``premium`` 괴리율(시장가가 NAV 대비
    벗어난 정도, %). ``nav_change`` / ``nav_change_percent`` 는 NAV 전일대비로 하락이면 음수.
    """

    trading_date: date
    close: Decimal                    # 시장 종가
    nav: Decimal
    nav_change: Decimal               # NAV 전일대비(부호 포함)
    nav_change_percent: Decimal       # NAV 전일대비율(부호 포함)
    premium: Decimal                  # 괴리율(%): 시장가 vs NAV
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ETFNAVComparison:
    """ETF 시장가격과 NAV의 당일 OHLC 비교 스냅샷(불변)."""

    symbol: str
    price: Decimal
    previous_close: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    cumulative_trading_amount: Decimal
    nav: Decimal
    previous_nav: Decimal
    nav_open: Decimal
    nav_high: Decimal
    nav_low: Decimal
    nav_change: Decimal
    nav_change_percent: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ETFNAVMinutePoint:
    """ETF 시장가격과 NAV의 분별 비교 한 점(불변)."""

    timestamp: datetime
    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    interval_volume: int
    nav: Decimal
    nav_change: Decimal
    nav_change_percent: Decimal
    price_minus_nav: Decimal
    premium: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ETFOrderBook:
    """ETF 10단계 호가와 LP 잔량·잔량 증감·중간가(불변)."""

    order_book: OrderBook
    lp_bids: tuple[PriceLevel, ...]
    lp_asks: tuple[PriceLevel, ...]
    bid_quantity_changes: tuple[int, ...]
    ask_quantity_changes: tuple[int, ...]
    lp_total_bid_quantity: int
    lp_total_ask_quantity: int
    total_bid_quantity_change: int
    total_ask_quantity_change: int
    midpoint: Decimal | None
    midpoint_quantity: int | None
    midpoint_code: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "lp_bids", tuple(self.lp_bids))
        object.__setattr__(self, "lp_asks", tuple(self.lp_asks))
        object.__setattr__(self, "bid_quantity_changes", tuple(self.bid_quantity_changes))
        object.__setattr__(self, "ask_quantity_changes", tuple(self.ask_quantity_changes))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
