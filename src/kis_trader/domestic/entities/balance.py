"""계좌 잔고(DATA) -- :class:`DomesticPosition`, :class:`DomesticBalance`, :class:`Portfolio`.

:class:`DomesticPosition` 은 한 종목의 보유 현황, :class:`DomesticBalance` 는 계좌 현금·자산 요약,
:class:`Portfolio` 는 그 둘을 한 스냅샷으로 묶은 것이다. 금액은 종목/계좌 통화의 Decimal
(국내는 KRW). 다중통화가 필요해지면 ``Money`` 로 승격한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class DomesticPosition:
    """한 종목의 보유 현황(불변).

    ``average_purchase_price`` 는 매입평균단가(취득원가)로, 체결가 평균인
    ``ExecutionReport.average_price`` 와 다른 개념이라 이름을 구분한다. 0수량 잔여 lot(정산
    대기)도 그대로 담긴다 -- 필터는 호출자 몫.
    """

    symbol: str
    name: str
    currency: str
    quantity: Decimal
    sellable_quantity: Decimal
    average_purchase_price: Decimal
    purchase_amount: Decimal
    current_price: Decimal
    market_value: Decimal             # 평가금액 = 현재가 x 수량
    unrealized_pnl: Decimal
    unrealized_pnl_percent: Decimal   # 평가손익율(evlu_pfls_rt) = 평가손익 / 매입금액
    change: Decimal                   # 전일대비(bfdy_cprs_icdc): 1주 가격의 전일 대비 증감(부호 포함)
    change_percent: Decimal           # 등락율(fltt_rt): 전일 대비 가격 등락 %(평가손익율과 별개)
    evaluation_return_percent: Decimal  # 평가수익률(evlu_erng_rt): KIS 별도 수익률 키(평가손익율과 다름)
    trade_type_name: str              # 매매구분명(trad_dvsn_name): 현금/신용 등 보유 lot 구분
    substitute_price: Decimal         # 대용가(sbst_pric): 대용증거금 산정 단가
    margin_rate_name: str             # 증거금율명(item_mgna_rt_name): 예 "40%"
    guarantee_rate_name: str          # 보증금율명(grta_rt_name): 예 "45%" / "불가"
    stock_loan_unit_price: Decimal    # 대주단가(stck_loan_unpr)
    previous_day_buy_quantity: Decimal   # 전일매수수량(bfdy_buy_qty)
    previous_day_sell_quantity: Decimal  # 전일매도수량(bfdy_sll_qty)
    today_buy_quantity: Decimal          # 금일매수수량(thdt_buyqty)
    today_sell_quantity: Decimal         # 금일매도수량(thdt_sll_qty)
    loan_amount: Decimal              # 대출금액(loan_amt): 신용/대주 lot 에 걸린 대출 원금(비대출이면 0)
    short_selling_charges: Decimal    # 대주매도대금(stln_slng_chgs): 대주 포지션 외엔 0
    loan_date: date | None            # 대출일자(loan_dt): 신용/대주 lot 의 대출 개시일(없으면 None)
    expiry_date: date | None          # 만기일자(expd_dt): 신용/대주 상환 만기(없으면 None)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DomesticBalance:
    """계좌의 현금·자산 요약(불변).

    ``deposit`` 예수금총액, ``settlement_cash_d1`` / ``settlement_cash_d2`` D+1 / D+2 정산예정 현금
    (D+2가 실질 인출가능), ``net_asset`` 순자산(현금+평가), ``market_value`` 보유 종목 평가금액
    합계, ``total_evaluation`` KIS 총평가금액, ``unrealized_pnl`` 평가손익 합계.
    """

    currency: str
    deposit: Decimal
    settlement_cash_d1: Decimal
    settlement_cash_d2: Decimal
    total_evaluation: Decimal
    net_asset: Decimal
    purchase_amount: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    securities_evaluation: Decimal    # 유가증권평가금액(scts_evlu_amt): 현금 제외 보유 평가
    total_loan_amount: Decimal        # 총대출금액(tot_loan_amt): 레버리지
    previous_total_asset: Decimal     # 전일총자산평가금액(bfdy_tot_asst_evlu_amt)
    asset_change_amount: Decimal      # 자산증감액(asst_icdc_amt): 전일 대비 총자산 변동(부호 포함)
    asset_change_percent: Decimal     # 자산증감수익율(asst_icdc_erng_rt): 전일 대비 수익률 %
    cma_evaluation: Decimal           # CMA평가금액(cma_evlu_amt): 비-CMA 계좌는 0
    previous_day_buy_amount: Decimal  # 전일매수금액(bfdy_buy_amt)
    today_buy_amount: Decimal         # 금일매수금액(thdt_buy_amt)
    previous_day_sell_amount: Decimal  # 전일매도금액(bfdy_sll_amt)
    today_sell_amount: Decimal        # 금일매도금액(thdt_sll_amt)
    previous_day_expenses: Decimal    # 전일제비용(bfdy_tlex_amt): 수수료+세금
    today_expenses: Decimal           # 금일제비용(thdt_tlex_amt)
    d2_auto_redemption_amount: Decimal    # D+2자동상환금액(d2_auto_rdpt_amt)
    next_day_auto_redemption_amount: Decimal  # 익일자동상환금액(nxdy_auto_rdpt_amt)
    total_short_sell_amount: Decimal  # 총대주매도대금(tot_stln_slng_chgs)
    financing_auto_redemption: bool   # 융자자동상환여부(fncg_gld_auto_rdpt_yn)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class Portfolio:
    """계좌 스냅샷 -- 현금·자산 요약과 보유 종목 한 벌(무거운 조회를 한 번만)."""

    balance: DomesticBalance
    positions: tuple[DomesticPosition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))


@dataclass(frozen=True, slots=True)
class AccountAssets:
    """투자계좌 자산현황 요약(불변). 자산군 전반의 총자산·순자산·예수금·대출·외화까지 아우른다.

    :class:`DomesticBalance`(주식 잔고 요약)보다 넓은 계좌 전체 관점이다 -- ``total_foreign_evaluation``
    외화평가총액, ``overseas_stock_evaluation`` 해외주식평가금액, ``total_substitute_amount``
    총대용금액, ``total_loan_amount`` 대출금액합계 등을 포함한다. 자산군별 내역(output1)은 계좌
    유형에 따라 항목 순서가 달라 라벨을 단정하지 않고 ``_raw`` 로 남긴다. 금액은 KRW Decimal.
    """

    total_asset_amount: Decimal         # 총자산금액(tot_asst_amt)
    total_net_asset_amount: Decimal     # 순자산총금액(nass_tot_amt)
    total_purchase_amount: Decimal      # 매입금액합계(pchs_amt_smtl)
    total_evaluation_amount: Decimal    # 평가금액합계(evlu_amt_smtl)
    total_unrealized_pnl: Decimal       # 평가손익합계(evlu_pfls_amt_smtl)
    total_loan_amount: Decimal          # 대출금액합계(loan_amt_smtl)
    total_deposit: Decimal              # 총예수금액(tot_dncl_amt)
    deposit: Decimal                    # 예수금액(dncl_amt)
    total_foreign_evaluation: Decimal   # 외화평가총액(frcr_evlu_tota)
    overseas_stock_evaluation: Decimal  # 해외주식평가금액(ovrs_stck_evlu_amt1)
    total_substitute_amount: Decimal    # 총대용금액(tot_sbst_amt)
    today_receivable: Decimal           # 당일미수금액(thdt_rcvb_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
