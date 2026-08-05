"""재무제표(DATA) -- :class:`BalanceSheet` / :class:`IncomeStatement`.

한 종목의 결산기별 재무제표 한 행이다. :meth:`~kis_openapi.ticker.Ticker.balance_sheet` /
:meth:`~kis_openapi.ticker.Ticker.income_statement` 가 결산기 리스트(최근->과거)로 돌려준다.
``period`` 는 결산년월(``"YYYYMM"``). 금액 단위는 KIS 원본을 따른다(대개 억원). 세부 항목은 ``_raw``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class BalanceSheet:
    """한 결산기의 대차대조표 요약(불변)."""

    symbol: str
    period: str                       # 결산년월(stac_yymm, "YYYYMM")
    current_assets: Decimal           # 유동자산(cras)
    fixed_assets: Decimal             # 고정(비유동)자산(fxas)
    total_assets: Decimal             # 자산총계(total_aset)
    current_liabilities: Decimal      # 유동부채(flow_lblt)
    fixed_liabilities: Decimal        # 고정(비유동)부채(fix_lblt)
    total_liabilities: Decimal        # 부채총계(total_lblt)
    capital: Decimal                  # 자본금(cpfn)
    total_equity: Decimal             # 자본총계(total_cptl)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class IncomeStatement:
    """한 결산기의 손익계산서 요약(불변)."""

    symbol: str
    period: str                       # 결산년월(stac_yymm, "YYYYMM")
    revenue: Decimal                  # 매출액(sale_account)
    cost_of_sales: Decimal            # 매출원가(sale_cost)
    gross_profit: Decimal             # 매출총이익(sale_totl_prfi)
    sga_expenses: Decimal             # 판매관리비(sell_mang)
    operating_income: Decimal         # 영업이익(bsop_prti)
    net_income: Decimal               # 당기순이익(thtr_ntin)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
