"""해외 기업행위 DATA -- 권리·기업행위(배당·증자·액면변경 등).

해외 종목의 권리·기업행위 조회가 돌려주는 DATA."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


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
