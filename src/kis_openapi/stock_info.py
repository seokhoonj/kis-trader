"""종목 기본정보(DATA) -- :class:`StockProfile`.

한 종목의 상장/기업 기본정보 스냅샷이다. :meth:`~kis_openapi.stock.DomesticStock.info` 가 돌려준다.
시세(:class:`~kis_openapi.quote.Quote`)가 "지금 얼마"라면 ``StockProfile`` 는 "어떤 종목인가"
(이름·상장주식수·자본금·액면가·업종·상장일)다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class StockProfile:
    """한 종목의 기본정보(불변).

    ``listed_shares`` 는 상장주식수, ``capital`` 은 자본금, ``par_value`` 는 액면가, ``issue_price``
    는 발행가. ``sector_large`` / ``sector_medium`` / ``sector_small`` 은 지수업종 대/중/소분류명,
    ``is_kospi200`` 는 KOSPI200 편입 여부. ``listing_date`` 는 상장일(KST-aware, 없으면 ``None``).
    ``kind`` 는 주식종류코드(보통주/우선주 등). 세부는 ``_raw``.
    """

    symbol: str
    name: str
    short_name: str
    english_name: str
    listed_shares: int | None         # 상장주식수(lstg_stqt)
    capital: Decimal | None           # 자본금(cpta)
    par_value: Decimal | None         # 액면가(papr)
    issue_price: Decimal | None       # 발행가(issu_pric)
    sector_large: str                 # 지수업종 대분류명(idx_bztp_lcls_cd_name)
    sector_medium: str                # 지수업종 중분류명(idx_bztp_mcls_cd_name)
    sector_small: str                 # 지수업종 소분류명(idx_bztp_scls_cd_name)
    is_kospi200: bool                  # KOSPI200 편입 여부(kospi200_item_yn)
    kind: str                         # 주식종류코드(stck_kind_cd)
    listing_date: datetime | None     # 상장일(KST-aware)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class StockStatus:
    """현재가와 거래·규제·경고 상태를 함께 담은 종목 스냅샷(불변)."""

    symbol: str
    market: str
    market_name: str
    industry_name: str
    price: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    previous_close: Decimal
    base_price: Decimal
    upper_limit: Decimal
    lower_limit: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    previous_volume: int
    volume_ratio: Decimal
    cumulative_trading_amount: Decimal
    credit_allowed: bool
    credit_ratio: Decimal
    margin_ratio: Decimal
    managed: bool
    short_term_overheated: bool
    market_warning_code: str
    market_warning_name: str
    investment_caution: bool
    abnormal_runup: bool
    short_sale_overheated: bool
    low_liquidity: bool
    vi_code: str
    liquidation_trading: bool
    halted: bool
    new_listing_name: str
    ex_rights_name: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
