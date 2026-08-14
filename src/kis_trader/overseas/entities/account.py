"""해외 계좌 DATA -- 외화 증거금·거래내역·매수가능·기간손익.

해외 계좌 조회(``kis.overseas.account.foreign_margin`` / ``.transactions`` / ``.buyable`` /
``.period_profit``)가 돌려준다. 금액은 거래/조회 통화라 :class:`~kis_trader.money.Money`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload
from ...money import Money


@dataclass(frozen=True, slots=True)
class OverseasForeignMargin:
    """통화별 해외증거금 한 건(불변). 계좌의 통화별 외화 예수금·증거금·주문가능금액을 한 줄로.
    금액은 그 통화의 :class:`Money`.

    ``deposit`` 외화예수금액, ``margin_amount`` 외화증거금액, ``receivable_amount`` 외화미수금액,
    ``unsettled_buy_amount``/``unsettled_sell_amount`` 미결제 매수/매도금액, ``general_orderable_amount``
    외화일반주문가능금액, ``orderable_amount`` 외화주문가능금액, ``integrated_orderable_amount``
    통합주문가능금액, ``exchange_rate`` 기준환율.
    """

    country_name: str                 # 국가명(natn_name)
    currency: str                     # 통화코드(crcy_cd)
    deposit: Money                    # 외화예수금액(frcr_dncl_amt1)
    unsettled_buy_amount: Money       # 미결제매수금액(ustl_buy_amt)
    unsettled_sell_amount: Money      # 미결제매도금액(ustl_sll_amt)
    receivable_amount: Money          # 외화미수금액(frcr_rcvb_amt)
    margin_amount: Money              # 외화증거금액(frcr_mgn_amt)
    general_orderable_amount: Money   # 외화일반주문가능금액(frcr_gnrl_ord_psbl_amt)
    orderable_amount: Money           # 외화주문가능금액(frcr_ord_psbl_amt1)
    integrated_orderable_amount: Money  # 통합주문가능금액(itgr_ord_psbl_amt)
    exchange_rate: Decimal            # 기준환율(bass_exrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasTransaction:
    """해외주식 일별 거래내역 한 건(불변). 체결된 매매 한 줄 -- 매매일·결제일·종목·수량·단가·금액·
    수수료. 외화 금액은 그 거래 통화의 :class:`Money`, 원화 수수료는 KRW Decimal.

    ``price`` 체결단가, ``trade_amount`` 거래외화금액, ``settlement_amount`` 외화정산금액,
    ``foreign_fee`` 외화수수료, ``domestic_won_fee``/``overseas_won_fee`` 국내·해외 원화수수료,
    ``loan_type`` 대출구분명(예: 현금).
    """

    trade_date: date | None           # 매매일자(trad_dt)
    settlement_date: date | None      # 결제일자(sttl_dt)
    side: str                         # buy / sell
    symbol: str
    name: str
    quantity: Decimal                 # 체결수량(ccld_qty)
    price: Money                      # 체결단가(ft_ccld_unpr2)
    trade_amount: Money               # 거래외화금액2(tr_frcr_amt2)
    settlement_amount: Money          # 외화정산금액1(frcr_excc_amt_1)
    foreign_fee: Money                # 외화수수료1(frcr_fee1)
    domestic_won_fee: Decimal         # 국내원화수수료(dmst_wcrc_fee), KRW
    overseas_won_fee: Decimal         # 해외원화수수료(ovrs_wcrc_fee), KRW
    currency: str                     # 거래통화코드(crcy_cd)
    loan_type: str                    # 대출구분명(loan_dvsn_name)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasBuyableAmount:
    """해외주식 매수가능금액(불변). 거래소·종목·주문단가 기준. 금액은 조회 통화의 :class:`Money`.

    ``orderable_amount`` 외화 기준 주문가능금액("외화" 화면), ``max_quantity`` 그 최대 주문가능수량,
    ``integrated_orderable_amount``/``integrated_max_quantity`` 는 원화 통합("통합" 화면) 기준.
    ``orderable_foreign_cash`` 주문가능외화금액, ``reusable_sell_amount`` 매도재사용가능금액,
    ``exchange_rate`` 적용환율. **매수 시 수량단위 절사가 필요**(예: 100주 단위면 545 -> 500).
    """

    symbol: str
    exchange: str                     # 주문 거래소코드(OVRS_EXCG_CD)
    currency: str
    orderable_foreign_cash: Money     # 주문가능외화금액(ord_psbl_frcr_amt)
    reusable_sell_amount: Money       # 매도재사용가능금액(sll_ruse_psbl_amt)
    orderable_amount: Money           # 해외주문가능금액(ovrs_ord_psbl_amt) -- 외화 기준
    max_quantity: Decimal             # 최대주문가능수량(max_ord_psbl_qty) -- 외화 기준
    integrated_orderable_amount: Money  # 외화주문가능금액1(frcr_ord_psbl_amt1) -- 통합 기준
    integrated_max_quantity: Decimal  # 해외최대주문가능수량(ovrs_max_ord_psbl_qty) -- 통합 기준
    exchange_rate: Decimal            # 환율(exrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasPeriodProfitRow:
    """해외주식 기간손익의 매도청산 한 줄(불변). 금액은 조회통화(``CRCY_CD``) 외화 Decimal -- 조회를
    통화 지정 없이(전체) 하면 통화가 섞일 수 있다."""

    trade_day: date | None             # 매매일(trad_day)
    symbol: str                        # 해외상품번호(ovrs_pdno)
    name: str                          # 해외종목명(ovrs_item_name)
    sold_quantity: Decimal             # 매도청산수량(slcl_qty)
    average_purchase_price: Decimal    # 매입평균가격(pchs_avg_pric)
    purchase_amount: Decimal           # 외화매입금액1(frcr_pchs_amt1)
    average_sell_price: Decimal        # 평균매도단가(avg_sll_unpr)
    sell_amount: Decimal               # 외화매도금액합계1(frcr_sll_amt_smtl1)
    sell_expense: Decimal              # 주식매도제비용(stck_sll_tlex)
    realized_pnl: Decimal              # 해외실현손익금액(ovrs_rlzt_pfls_amt)
    return_rate: Decimal               # 수익률(pftrt)
    exchange_rate: Decimal             # 환율(exrt)
    exchange: str                      # 해외거래소코드(ovrs_excg_cd)
    first_exchange_rate: Decimal       # 최초고시환율(frst_bltn_exrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasPeriodProfit:
    """해외주식 기간손익(불변) -- 기간 내 매도청산 종목별 실현손익(``rows``)과 총계. **모의투자 미지원**.

    .. note:: KIS 응답예시가 비어 있어 필드는 레이아웃 기준이다 -- 실제 응답과 다를 수 있으므로 각
       행과 결과의 ``_raw`` 로 원본을 함께 노출한다.
    """

    rows: tuple[OverseasPeriodProfitRow, ...]
    total_sell_amount: Decimal         # 주식매도금액합계(stck_sll_amt_smtl)
    total_buy_amount: Decimal          # 주식매수금액합계(stck_buy_amt_smtl)
    total_fee: Decimal                 # 합계수수료1(smtl_fee1)
    settlement_amount: Decimal         # 정산지급금액(excc_dfrm_amt)
    total_realized_pnl: Decimal        # 해외실현손익총금액(ovrs_rlzt_pfls_tot_amt)
    total_return_rate: Decimal         # 총수익률(tot_pftrt)
    basis_date: date | None            # 기준일자(bass_dt)
    exchange_rate: Decimal             # 환율(exrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
