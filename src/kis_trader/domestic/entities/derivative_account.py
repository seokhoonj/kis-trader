"""국내선물옵션 계좌 잔고(DATA) -- :class:`DerivativePosition`, :class:`DerivativeBalance`.

:class:`DerivativePosition` 은 한 종목(선물/옵션)의 보유 현황, :class:`DerivativeBalance` 는
계좌 예수금·증거금·손익 요약과 보유내역 한 벌이다. 금액·수량은 KRW Decimal(선물옵션은 원화).
계좌 요약(output2)에는 여기 타입화하지 않은 필드가 많아 원본 요약을 ``_raw`` 로 함께 노출한다.
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
class DerivativePosition:
    """한 선물옵션 종목의 보유 현황(불변).

    ``symbol`` 은 주문 API 와 왕복하는 단축상품번호(shtn_pdno, 예 "101W09"), ``isin`` 은 표준
    상품번호(pdno, 예 "KR4101RC0000")다. ``side`` 는 매도매수구분명을 그대로(strip) 담는다
    -- 청산 등으로 빈 값일 수 있다. 청산 완료된 0수량 잔여 lot 도 그대로 담기며, 필터는 호출자 몫.
    """

    symbol: str                       # 단축상품번호(shtn_pdno)
    isin: str                         # 상품번호(pdno)
    name: str                         # 상품명(prdt_name)
    side: str                         # 매도매수구분명(sll_buy_dvsn_name)
    quantity: Decimal                 # 잔고수량(cblc_qty)
    settlement_price: Decimal         # 정산단가(excc_unpr)
    average_price: Decimal            # 체결평균단가1(ccld_avg_unpr1)
    purchase_amount: Decimal          # 매입금액(pchs_amt)
    market_value: Decimal             # 평가금액(evlu_amt)
    unrealized_pnl: Decimal           # 평가손익금액(evlu_pfls_amt)
    realized_pnl: Decimal             # 매매손익금액(trad_pfls_amt)
    liquidatable_quantity: Decimal    # 청산가능수량(lqd_psbl_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeBalance:
    """선물옵션 계좌의 예수금·증거금·손익 요약과 보유내역 한 벌(불변).

    ``total_deposit`` 총예수금액, ``deposit_cash`` 예수금현금, ``total_margin`` 증거금총액,
    ``orderable_cash`` / ``orderable_total`` 주문가능현금/총액, ``account_value`` 추정예탁자산금액.
    평가·매매손익은 합계(``total_*``)와 선물/옵션 분해(``futures_*`` / ``options_*``)를 함께 담는다.
    타입화하지 않은 요약 필드는 ``_raw`` 로 접근한다.
    """

    positions: tuple[DerivativePosition, ...]  # 보유내역(output1)
    total_deposit: Decimal                # 총예수금액(tot_dncl_amt)
    deposit_cash: Decimal                 # 예수금현금(dnca_cash)
    total_margin: Decimal                 # 증거금총액(mgna_tota)
    orderable_cash: Decimal               # 주문가능현금(ord_psbl_cash)
    orderable_total: Decimal              # 주문가능총액(ord_psbl_tota)
    total_unrealized_pnl: Decimal         # 평가손익금액합계(evlu_pfls_amt_smtl)
    total_realized_pnl: Decimal           # 매매손익금액합계(trad_pfls_amt_smtl)
    futures_unrealized_pnl: Decimal       # 선물평가손익금액(futr_evlu_pfls_amt)
    options_unrealized_pnl: Decimal       # 옵션평가손익금액(opt_evlu_pfls_amt)
    futures_realized_pnl: Decimal         # 선물매매손익금액(futr_trad_pfls_amt)
    options_realized_pnl: Decimal         # 옵션매매손익금액(opt_trad_pfls_amt)
    account_value: Decimal                # 추정예탁자산금액(prsm_dpast_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeValuationPosition:
    """한 선물옵션 종목의 잔고평가손익 현황(불변).

    :class:`DerivativePosition` 과 같은 종목 식별(``symbol``/``isin``/``name``/``side``)에 더해
    종목별 평가/매매 손익을 담는다. ``symbol`` 은 주문 API 와 왕복하는 단축상품번호(shtn_pdno,
    예 "101W09"), ``isin`` 은 표준 상품번호(pdno, 예 "KR4101RC0000")다. ``quantity`` 는 잔고수량1
    (cblc_qty1) -- 잔고 조회의 cblc_qty 와 필드명이 다르다. 청산 완료된 0수량 잔여 lot 도 그대로
    담기며, 필터는 호출자 몫.
    """

    symbol: str                       # 단축상품번호(shtn_pdno)
    isin: str                         # 상품번호(pdno)
    name: str                         # 상품명(prdt_name)
    side: str                         # 매도매수구분명(sll_buy_dvsn_name)
    quantity: Decimal                 # 잔고수량1(cblc_qty1)
    settlement_price: Decimal         # 정산단가(excc_unpr)
    average_price: Decimal            # 체결평균단가1(ccld_avg_unpr1)
    index_close: Decimal              # 지수종가(idx_clpr)
    purchase_amount: Decimal          # 매입금액(pchs_amt)
    market_value: Decimal             # 평가금액(evlu_amt)
    unrealized_pnl: Decimal           # 평가손익금액(evlu_pfls_amt)
    realized_pnl: Decimal             # 매매손익금액(trad_pfls_amt)
    liquidatable_quantity: Decimal    # 청산가능수량(lqd_psbl_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeValuationBalance:
    """선물옵션 잔고평가손익 뷰 -- 계좌 예수금·증거금·손익 요약과 보유내역 한 벌(불변).

    :class:`DerivativeBalance` 와 같은 요약 필드 집합을 담되, 보유내역은 종목별 평가/매매 손익을
    실은 :class:`DerivativeValuationPosition` 이다. ``total_deposit`` 총예수금액, ``deposit_cash``
    예수금현금, ``total_margin`` 증거금총액, ``orderable_cash`` / ``orderable_total`` 주문가능현금/총액,
    ``account_value`` 추정예탁자산금액. 평가·매매손익은 합계(``total_*``)와 선물/옵션 분해
    (``futures_*`` / ``options_*``)를 함께 담는다. 타입화하지 않은 요약 필드는 ``_raw`` 로 접근한다.
    """

    positions: tuple[DerivativeValuationPosition, ...]  # 보유내역(output1)
    total_deposit: Decimal                # 총예수금액(tot_dncl_amt)
    deposit_cash: Decimal                 # 예수금현금(dnca_cash)
    total_margin: Decimal                 # 증거금총액(mgna_tota)
    orderable_cash: Decimal               # 주문가능현금(ord_psbl_cash)
    orderable_total: Decimal              # 주문가능총액(ord_psbl_tota)
    total_unrealized_pnl: Decimal         # 평가손익금액합계(evlu_pfls_amt_smtl)
    total_realized_pnl: Decimal           # 매매손익금액합계(trad_pfls_amt_smtl)
    futures_unrealized_pnl: Decimal       # 선물평가손익금액(futr_evlu_pfls_amt)
    options_unrealized_pnl: Decimal       # 옵션평가손익금액(opt_evlu_pfls_amt)
    futures_realized_pnl: Decimal         # 선물매매손익금액(futr_trad_pfls_amt)
    options_realized_pnl: Decimal         # 옵션매매손익금액(opt_trad_pfls_amt)
    account_value: Decimal                # 추정예탁자산금액(prsm_dpast_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeSettlementPosition:
    """한 선물옵션 종목의 잔고정산손익 현황(불변).

    이 조회의 종목 식별은 상품번호(pdno)뿐이라 ``symbol`` 에 pdno 를 담는다(단축상품번호 없음).
    ``trade_type`` 은 매매구분명(trad_dvsn_name)을 그대로(strip). ``prior_quantity`` 전일잔고수량,
    ``new_quantity`` 신규수량, ``offset_quantity`` 상계환매수량, ``quantity`` 잔고수량,
    ``balance_amount`` 잔고금액. 상품번호가 빈 패딩 행은 담기지 않는다.
    """

    symbol: str                       # 상품번호(pdno)
    name: str                         # 상품명(prdt_name)
    trade_type: str                   # 매매구분명(trad_dvsn_name)
    prior_quantity: Decimal           # 전일잔고수량(bfdy_cblc_qty)
    new_quantity: Decimal             # 신규수량(new_qty)
    offset_quantity: Decimal          # 상계환매수량(mnpl_rpch_qty)
    quantity: Decimal                 # 잔고수량(cblc_qty)
    balance_amount: Decimal           # 잔고금액(cblc_amt)
    realized_pnl: Decimal             # 매매손익금액(trad_pfls_amt)
    market_value: Decimal             # 평가금액(evlu_amt)
    unrealized_pnl: Decimal           # 평가손익금액(evlu_pfls_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeSettlementBalance:
    """선물옵션 잔고정산손익 뷰 -- 계좌 예수금·증거금·수수료 요약과 정산 보유내역 한 벌(불변).

    ``next_day_deposit`` 익일예수금, ``maintenance_margin_cash`` / ``maintenance_margin_total``
    유지증거금현금/총액, ``brokerage_margin_cash`` / ``brokerage_margin_total`` 위탁증거금현금/총액,
    ``deposit_cash`` 예수금현금, ``deposit_substitute`` 예수금대용. 옵션 대금은 ``option_buy_amount``
    (매수대금), ``option_sell_amount``(매도대금), ``option_liquidation_value``(청산평가금액)로 담는다.
    ``today_settlement_diff`` 당일정산차금, ``renewal_settlement_diff`` 갱신정산차금, ``fee`` 수수료.
    타입화하지 않은 요약 필드는 ``_raw`` 로 접근한다.
    """

    positions: tuple[DerivativeSettlementPosition, ...]  # 정산 보유내역(output1)
    next_day_deposit: Decimal             # 익일예수금(nxdy_dnca)
    maintenance_margin_cash: Decimal      # 유지증거금현금(mmga_cash)
    maintenance_margin_total: Decimal     # 유지증거금총액(mmga_tota)
    brokerage_margin_cash: Decimal        # 위탁증거금현금(brkg_mgna_cash)
    brokerage_margin_total: Decimal       # 위탁증거금총액(brkg_mgna_tota)
    deposit_cash: Decimal                 # 예수금현금(dnca_cash)
    deposit_substitute: Decimal           # 예수금대용(dnca_sbst)
    option_buy_amount: Decimal            # 옵션매수대금(opt_buy_chgs)
    option_sell_amount: Decimal           # 옵션매도대금(opt_sll_chgs)
    option_liquidation_value: Decimal     # 옵션청산평가금액(opt_lqd_evlu_amt)
    fee: Decimal                          # 수수료(fee)
    today_settlement_diff: Decimal        # 당일정산차금(thdt_dfpa)
    renewal_settlement_diff: Decimal      # 갱신정산차금(rnwl_dfpa)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeFill:
    """선물옵션 기준일 체결 한 건(불변).

    ``symbol`` 은 상품번호(pdno), ``order_id`` 주문번호(odno), ``transaction_type`` 거래유형명
    (tr_type_name). ``final_settlement_date`` 는 최종결제일(last_sttldt) -- 형식오류/공백이면 None.
    ``fill_index`` 체결지수, ``filled_quantity`` 체결수량, ``trade_amount`` 거래금액, ``fee`` 수수료.
    ``fill_time`` 은 체결시각 구간(ccld_btwn)을 파싱하지 않고 원본 문자열 그대로 담는다.
    주문번호가 빈 패딩 행은 담기지 않는다.
    """

    symbol: str                        # 상품번호(pdno)
    name: str                          # 상품명(prdt_name)
    order_id: str                      # 주문번호(odno)
    transaction_type: str              # 거래유형명(tr_type_name)
    final_settlement_date: date | None  # 최종결제일(last_sttldt)
    fill_index: Decimal                # 체결지수(ccld_idx)
    filled_quantity: Decimal           # 체결수량(ccld_qty)
    trade_amount: Decimal              # 거래금액(trad_amt)
    fee: Decimal                       # 수수료(fee)
    fill_time: str                     # 체결시각구간(ccld_btwn) -- 원본 문자열
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeFillHistory:
    """선물옵션 기준일 체결내역 -- 기간 합계 요약과 체결 한 벌(불변).

    ``total_filled_quantity`` 총체결수량합계, ``total_filled_amount`` 총체결금액합계,
    ``fee_adjustment`` 수수료조정, ``total_fee`` 수수료합계. 타입화하지 않은 요약 필드는 ``_raw``.
    """

    fills: tuple[DerivativeFill, ...]  # 체결내역(output1)
    total_filled_quantity: Decimal     # 총체결수량합계(tot_ccld_qty_smtl)
    total_filled_amount: Decimal       # 총체결금액합계(tot_ccld_amt_smtl)
    fee_adjustment: Decimal            # 수수료조정(fee_adjt)
    total_fee: Decimal                 # 수수료합계(fee_smtl)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "fills", tuple(self.fills))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeCommission:
    """선물옵션 기간약정수수료 하루치(불변).

    ``order_date`` 주문일자(ord_dt) -- 형식오류/공백이면 None. ``symbol`` 상품번호(pdno),
    ``name`` 종목명(item_name). ``sell_agreement_amount`` / ``sell_fee`` 매도약정금액/수수료,
    ``buy_agreement_amount`` / ``buy_fee`` 매수약정금액/수수료, ``total_fee`` 수수료합계,
    ``realized_pnl`` 매매손익. 주문일자·상품번호가 모두 빈 패딩 행은 담기지 않는다.
    """

    order_date: date | None            # 주문일자(ord_dt)
    symbol: str                        # 상품번호(pdno)
    name: str                          # 종목명(item_name)
    sell_agreement_amount: Decimal     # 매도약정금액(sll_agrm_amt)
    sell_fee: Decimal                  # 매도수수료(sll_fee)
    buy_agreement_amount: Decimal      # 매수약정금액(buy_agrm_amt)
    buy_fee: Decimal                   # 매수수수료(buy_fee)
    total_fee: Decimal                 # 수수료합계(tot_fee_smtl)
    realized_pnl: Decimal              # 매매손익(trad_pfls)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeCommissionHistory:
    """선물옵션 기간약정수수료 -- 기간 합계 요약과 일별 내역 한 벌(불변).

    ``total_fee`` 수수료합계, ``total_agreement_amount`` 약정금액합계, ``total_sell_fee`` /
    ``total_buy_fee`` 매도/매수 수수료합계, ``futures_fee`` / ``options_fee`` 선물/옵션 수수료합계,
    ``total_realized_pnl`` 매매손익합계. 타입화하지 않은 요약 필드는 ``_raw`` 로 접근한다.
    """

    commissions: tuple[DerivativeCommission, ...]  # 일별 내역(output1)
    total_fee: Decimal                 # 수수료합계(fee_smtl)
    total_agreement_amount: Decimal    # 약정금액합계(agrm_amt_smtl)
    total_sell_fee: Decimal            # 매도수수료합계(sll_fee)
    total_buy_fee: Decimal             # 매수수수료합계(buy_fee)
    futures_fee: Decimal               # 선물수수료합계(futr_fee_smtl)
    options_fee: Decimal               # 옵션수수료합계(opt_fee_smtl)
    total_realized_pnl: Decimal        # 매매손익합계(trad_pfls_smtl)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "commissions", tuple(self.commissions))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeOrderable:
    """한 선물옵션 계약의 주문가능 수량(불변).

    ``orderable_quantity`` 주문가능수량(ord_psbl_qty), ``total_quantity`` 총가능수량(tot_psbl_qty),
    ``liquidatable_quantity`` 청산가능수량(주간 lqd_psbl_qty1 / 야간 lqd_psbl_qty), ``base_index``
    기준지수(bass_idx). 주간(:meth:`~kis_trader.derivative.FuturesContract.orderable`)과 야간
    (:meth:`~kis_trader.derivative.FuturesContract.night_orderable`)이 응답 필드가 조금 달라
    (야간엔 max_ord_psbl_qty 등) 타입화하지 않은 필드는 ``_raw`` 로 접근한다.
    """

    orderable_quantity: Decimal           # 주문가능수량(ord_psbl_qty)
    total_quantity: Decimal               # 총가능수량(tot_psbl_qty)
    liquidatable_quantity: Decimal        # 청산가능수량(lqd_psbl_qty1 / lqd_psbl_qty)
    base_index: Decimal                   # 기준지수(bass_idx)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeDeposit:
    """선물옵션 총자산현황 -- 예수금·주문가능·위탁증거금·손익 요약(불변).

    ``total_deposit`` 예수금총액, ``orderable_cash`` / ``orderable_total`` 주문가능현금/총액,
    ``brokerage_margin_cash`` / ``brokerage_margin_substitute`` 위탁증거금현금/대용,
    ``maintenance_ratio`` 유지비율, ``account_value`` 추정예탁자산금액, ``receivable`` 미수금.
    평가·매매손익은 합계(``total_*``)와 선물/옵션 분해(``futures_*`` / ``options_*``)를 함께 담는다.
    이 엔드포인트에 증거금총액(mgna_tota)은 없다 -- 위탁증거금 현금/대용을 대신 노출한다.
    타입화하지 않은 요약 필드는 ``_raw`` 로 접근한다.
    """

    total_deposit: Decimal                # 예수금총액(dnca_tota)
    orderable_cash: Decimal               # 주문가능현금(ord_psbl_cash)
    orderable_total: Decimal              # 주문가능총액(ord_psbl_tota)
    brokerage_margin_cash: Decimal        # 위탁증거금현금(brkg_mgna_cash)
    brokerage_margin_substitute: Decimal  # 위탁증거금대용(brkg_mgna_sbst)
    maintenance_ratio: Decimal            # 유지비율(mtnc_rt)
    total_unrealized_pnl: Decimal         # 평가손익합계(evlu_pfls_smtl)
    total_realized_pnl: Decimal           # 매매손익합계(trad_pfls_smtl)
    futures_unrealized_pnl: Decimal       # 선물평가손익금액(futr_evlu_pfls_amt)
    options_unrealized_pnl: Decimal       # 옵션평가손익금액(opt_evlu_pfls_amt)
    futures_realized_pnl: Decimal         # 선물매매손익(futr_trad_pfls)
    options_realized_pnl: Decimal         # 옵션매매손익금액(opt_trad_pfls_amt)
    account_value: Decimal                # 추정예탁자산금액(prsm_dpast_amt)
    receivable: Decimal                   # 미수금(rcva)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeNightBalance:
    """(야간)선물옵션 잔고현황 -- 계좌 예수금·증거금·손익 요약과 보유내역 한 벌(불변).

    주간 :class:`DerivativeBalance` 와 같은 요약 필드 집합(``total_deposit`` 총예수금액,
    ``deposit_cash`` 예수금현금, ``total_margin`` 증거금총액, ``orderable_cash`` /
    ``orderable_total`` 주문가능현금/총액, 평가·매매손익 합계·선물/옵션 분해, ``account_value``
    추정예탁자산)에 야간 전용 필드를 더한다: ``maintenance_ratio`` 유지비율, ``shortage_amount``
    부족금액, ``maintenance_margin_total`` / ``maintenance_margin_cash`` 유지증거금총금액/현금금액.
    보유내역은 주간과 같은 :class:`DerivativePosition` 이다. ``account_value`` 는 이 야간
    엔드포인트에선 추정예탁자산(prsm_dpast) -- 주간 잔고의 prsm_dpast_amt 와 필드명이 다르다.
    타입화하지 않은 요약 필드는 ``_raw`` 로 접근한다.
    """

    positions: tuple[DerivativePosition, ...]  # 보유내역(output1)
    total_deposit: Decimal                # 총예수금액(tot_dncl_amt)
    deposit_cash: Decimal                 # 예수금현금(dnca_cash)
    total_margin: Decimal                 # 증거금총액(mgna_tota)
    orderable_cash: Decimal               # 주문가능현금(ord_psbl_cash)
    orderable_total: Decimal              # 주문가능총액(ord_psbl_tota)
    total_unrealized_pnl: Decimal         # 평가손익금액합계(evlu_pfls_amt_smtl)
    total_realized_pnl: Decimal           # 매매손익금액합계(trad_pfls_amt_smtl)
    futures_unrealized_pnl: Decimal       # 선물평가손익금액(futr_evlu_pfls_amt)
    options_unrealized_pnl: Decimal       # 옵션평가손익금액(opt_evlu_pfls_amt)
    futures_realized_pnl: Decimal         # 선물매매손익금액(futr_trad_pfls_amt)
    options_realized_pnl: Decimal         # 옵션매매손익금액(opt_trad_pfls_amt)
    account_value: Decimal                # 추정예탁자산(prsm_dpast)
    maintenance_ratio: Decimal            # 유지비율(mtnc_rt)
    shortage_amount: Decimal              # 부족금액(isfc_amt)
    maintenance_margin_total: Decimal     # 유지증거금총금액(mmga_tot_amt)
    maintenance_margin_cash: Decimal      # 유지증거금현금금액(mmga_cash_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
