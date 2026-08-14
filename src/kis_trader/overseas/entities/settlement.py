"""해외 결제일자 DATA -- 시장별 현지·국내 결제일 캘린더.

해외 시장/국가별 현지·국내 결제일자 조회(``kis.overseas.settlement_dates``)가 돌려주는 한 건.
계좌 잔고가 아닌 시장 참조(캘린더) 데이터다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class OverseasSettlementDate:
    """해외 시장별 현지·국내 결제일자 한 건(불변)."""

    market_type_code: str
    country_code: str
    country_name: str
    country_abbr: str
    market_code: str
    market_name: str
    local_settlement_date: date | None
    domestic_settlement_date: date | None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
