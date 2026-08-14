"""재무제표(DATA) -- :class:`BalanceSheet` / :class:`IncomeStatement`.

한 종목의 결산기별 재무제표 한 행이다. :meth:`~kis_trader.stock.DomesticStock.balance_sheet` /
:meth:`~kis_trader.stock.DomesticStock.income_statement` 가 결산기 리스트(최근->과거)로 돌려준다.
``period`` 는 결산년월(``"YYYYMM"``). 금액 단위는 KIS 원본을 따른다(대개 억원). 세부 항목은 ``_raw``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class BalanceSheet:
    """한 결산기의 대차대조표 요약(불변)."""

    symbol: str
    period: str                       # 결산년월(stac_yymm, "YYYYMM")
    current_assets: Decimal | None    # 유동자산(cras; 금융업은 공란)
    fixed_assets: Decimal | None      # 고정(비유동)자산(fxas; 금융업은 공란)
    total_assets: Decimal             # 자산총계(total_aset)
    current_liabilities: Decimal | None  # 유동부채(flow_lblt; 금융업은 공란)
    fixed_liabilities: Decimal | None  # 고정(비유동)부채(fix_lblt; 금융업은 공란)
    total_liabilities: Decimal        # 부채총계(total_lblt)
    capital: Decimal                  # 자본금(cpfn)
    total_equity: Decimal             # 자본총계(total_cptl)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class FinancialRatio:
    """한 결산기의 주요 재무비율(불변).

    수익성(``roe``)·주당지표(``eps`` / ``sps`` / ``bps``)·안정성(``debt_ratio`` 부채비율,
    ``reserve_ratio`` 유보율)·성장성(``revenue_growth`` / ``operating_income_growth`` /
    ``net_income_growth``)의 헤드라인을 담는다(비율은 %, 주당지표 ``eps`` / ``sps`` / ``bps`` 는 원).
    세부(총자본순이익률·유동/당좌비율 등)는
    ``_raw`` 나 별도 조회에 있다. 비율이 특정 기에 결측이면 ``None``.
    """

    symbol: str
    period: str                       # 결산년월(stac_yymm, "YYYYMM")
    revenue_growth: Decimal | None    # 매출액 증가율(grs)
    operating_income_growth: Decimal | None  # 영업이익 증가율(bsop_prfi_inrt)
    net_income_growth: Decimal | None  # 순이익 증가율(ntin_inrt)
    roe: Decimal | None               # 자기자본이익률(roe_val)
    eps: Decimal | None               # 주당순이익 원(eps)
    sps: Decimal | None               # 주당매출액 원(sps)
    bps: Decimal | None               # 주당순자산 원(bps)
    reserve_ratio: Decimal | None     # 유보율(rsrv_rate)
    debt_ratio: Decimal | None        # 부채비율(lblt_rate)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class GrowthRatio:
    """한 결산기의 성장성비율(불변, 단위 %).

    매출액/영업이익/자기자본/총자산 증가율의 헤드라인이다. :class:`FinancialRatio` 와 매출액/영업이익
    증가율이 겹치지만, 이 조회는 자기자본(``equity_growth``)·총자산(``total_asset_growth``) 증가율을
    함께 준다. 특정 기에 결측이면 ``None``. 세부는 ``_raw``.
    """

    symbol: str
    period: str                       # 결산년월(stac_yymm, "YYYYMM")
    revenue_growth: Decimal | None    # 매출액 증가율(grs)
    operating_income_growth: Decimal | None  # 영업이익 증가율(bsop_prfi_inrt)
    equity_growth: Decimal | None     # 자기자본 증가율(equt_inrt)
    total_asset_growth: Decimal | None  # 총자산 증가율(totl_aset_inrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OtherRatio:
    """한 결산기의 기타주요비율(불변).

    기업가치 지표 -- ``eva``(경제적 부가가치), ``ebitda``, ``ev_ebitda``(EV/EBITDA 배수)를 담는다.
    ``payout_rate``(배당성향)는 KIS 명세가 "비정상 출력되는 데이터"로 명시해 무시 대상이라 별도 필드로
    노출하지 않는다(필요하면 ``_raw["payout_rate"]``). 특정 기에 결측이면 ``None``.
    """

    symbol: str
    period: str                       # 결산년월(stac_yymm, "YYYYMM")
    eva: Decimal | None               # 경제적 부가가치(eva)
    ebitda: Decimal | None            # EBITDA(ebitda)
    ev_ebitda: Decimal | None         # EV/EBITDA 배수(ev_ebitda)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ProfitabilityRatio:
    """한 결산기의 수익성비율(불변, 단위 %).

    총자본순이익률(``return_on_assets``)·자기자본순이익률(``return_on_equity``, ROE)·매출액순이익률
    (``net_margin``)·매출액총이익률(``gross_margin``)을 담는다. 특정 기에 결측이면 ``None``.
    """

    symbol: str
    period: str                       # 결산년월(stac_yymm, "YYYYMM")
    return_on_assets: Decimal | None  # 총자본순이익률(cptl_ntin_rate)
    return_on_equity: Decimal | None  # 자기자본순이익률(self_cptl_ntin_inrt, ROE)
    net_margin: Decimal | None        # 매출액순이익률(sale_ntin_rate)
    gross_margin: Decimal | None      # 매출액총이익률(sale_totl_rate)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class StabilityRatio:
    """한 결산기의 안정성비율(불변, 단위 %).

    부채비율(``debt_ratio``)·차입금의존도(``borrowing_dependency``)·유동비율(``current_ratio``)·
    당좌비율(``quick_ratio``)을 담는다. 특정 기에 결측이면 ``None``.
    """

    symbol: str
    period: str                       # 결산년월(stac_yymm, "YYYYMM")
    debt_ratio: Decimal | None        # 부채비율(lblt_rate)
    borrowing_dependency: Decimal | None  # 차입금의존도(bram_depn)
    current_ratio: Decimal | None     # 유동비율(crnt_rate)
    quick_ratio: Decimal | None       # 당좌비율(quck_rate)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class IncomeStatement:
    """한 결산기의 손익계산서 요약(불변)."""

    symbol: str
    period: str                       # 결산년월(stac_yymm, "YYYYMM")
    revenue: Decimal                  # 매출액(sale_account)
    cost_of_sales: Decimal | None     # 매출원가(sale_cost; 금융업은 공란)
    gross_profit: Decimal | None      # 매출총이익(sale_totl_prfi; 금융업은 공란)
    sga_expenses: Decimal | None      # 판매관리비(sell_mang; 금융업은 공란)
    operating_income: Decimal         # 영업이익(bsop_prti)
    net_income: Decimal               # 당기순이익(thtr_ntin)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
