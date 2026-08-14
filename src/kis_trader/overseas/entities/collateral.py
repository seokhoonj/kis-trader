"""해외 담보 DATA -- 담보증권·담보요약.

해외 대용/담보 조회가 돌려주는 종목별 담보정보와 요약."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class OverseasCollateralStock:
    """해외주식 담보대출 가능 여부와 적용 비율."""

    symbol: str
    name: str
    loan_rate: Decimal | None
    maintenance_rate: Decimal | None
    collateral_rate: Decimal | None
    is_loanable: bool
    registered_date: date | None
    market_name: str
    currency: str
    country_name: str
    exchange: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasCollateralSummary:
    """해외주식 담보대출 가능종목 조회의 요약(불변).

    ``loanable_count`` 는 조회 조건에 걸린 대출가능종목 수(전체 페이지 합계)다.
    """

    loanable_count: int               # 대출가능종목수
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasCollateralStockSearch:
    """해외주식 담보대출 가능종목 목록과 요약(불변).

    ``summary`` 는 조회 요약(:class:`OverseasCollateralSummary`), ``stocks`` 는 대출가능종목
    (:class:`OverseasCollateralStock`) 튜플이다.
    """

    summary: OverseasCollateralSummary
    stocks: tuple[OverseasCollateralStock, ...]
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "stocks", tuple(self.stocks))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
