"""해외선물옵션(08) 계좌 조회 DATA -- 예수금/보유/주문가능.

``kis.account`` (해외선물옵션 08)의 :class:`~kis_trader.overseas.derivative_account.
OverseasDerivativesAccount` 뷰가 돌려준다. **금액·수량은 조회 통화(``currency``)의 Decimal 이다
-- 원화(KRW)가 아니다.** 해외선물옵션 계좌는 통화별로 조회하므로 각 타입에 ``currency`` 를 함께
담고, 그 밖에 타입화하지 않은 벤더 필드는 ``_raw`` 로 노출한다. 모든 조회는 **실전 전용**이다.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload
from ...order import Side


@dataclass(frozen=True, slots=True)
class OverseasDerivativeDeposit:
    """해외선물옵션 예수금현황 -- 예수금·자산·증거금·손익 요약(불변).

    금액은 ``currency`` 통화의 Decimal(원화 아님). ``cash_balance`` 예수금잔액, ``total_asset``
    총자산평가금액, ``unrealized_pnl`` 선물옵션평가손익금액, ``realized_pnl`` 청산손익금액,
    ``brokerage_margin`` 위탁증거금액, ``maintenance_margin`` 유지증거금액, ``additional_margin``
    추가증거금액, ``risk_rate`` 위험율, ``orderable_amount`` 주문가능금액, ``withdrawable_amount``
    출금가능금액, ``receivable`` 미수금액, ``next_day_deposit`` 익일예수금액, ``option_value``
    옵션평가금액, ``fee`` 수수료. 타입화하지 않은 요약 필드는 ``_raw`` 로 접근한다.
    """

    currency: str                     # 통화코드(crcy_cd)
    cash_balance: Decimal             # 예수금잔액(fm_dnca_rmnd)
    total_asset: Decimal              # 총자산평가금액(fm_tot_asst_evlu_amt)
    unrealized_pnl: Decimal           # 선물옵션평가손익금액(fm_fuop_evlu_pfls_amt)
    realized_pnl: Decimal             # 청산손익금액(fm_lqd_pfls_amt)
    brokerage_margin: Decimal         # 위탁증거금액(fm_brkg_mgn_amt)
    maintenance_margin: Decimal       # 유지증거금액(fm_mntn_mgn_amt)
    additional_margin: Decimal        # 추가증거금액(fm_add_mgn_amt)
    risk_rate: Decimal                # 위험율(fm_risk_rt)
    orderable_amount: Decimal         # 주문가능금액(fm_ord_psbl_amt)
    withdrawable_amount: Decimal      # 출금가능금액(fm_drwg_psbl_amt)
    receivable: Decimal               # 미수금액(fm_rcvb_amt)
    next_day_deposit: Decimal         # 익일예수금액(fm_nxdy_dncl_amt)
    option_value: Decimal             # 옵션평가금액(fm_opt_evlu_amt)
    fee: Decimal                      # 수수료(fm_fee)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasDerivativePosition:
    """한 해외선물옵션 종목의 미결제(보유) 현황(불변).

    금액·수량은 ``currency`` 통화의 Decimal(원화 아님). ``symbol`` 해외선물FX상품번호,
    ``product_type`` 상품유형코드, ``side`` 매수/매도, ``quantity`` 미결제수량, ``average_price``
    체결평균가격, ``current_price`` 현재가격, ``unrealized_pnl`` 평가손익금액, ``option_value``
    옵션평가금액, ``option_unrealized_pnl`` 옵션평가손익금액, ``liquidatable_quantity`` 청산가능수량,
    ``exercise_reserved`` 행사예약주문여부("Y"/"N" 원본 문자열). 타입화하지 않은 필드는 ``_raw``.
    """

    symbol: str                       # 해외선물FX상품번호(ovrs_futr_fx_pdno)
    product_type: str                 # 상품유형코드(prdt_type_cd)
    currency: str                     # 통화코드(crcy_cd)
    side: Side                        # buy / sell (sll_buy_dvsn_cd: 01 매도 / 02 매수)
    quantity: Decimal                 # 미결제수량(fm_ustl_qty)
    average_price: Decimal            # 체결평균가격(fm_ccld_avg_pric)
    current_price: Decimal            # 현재가격(fm_now_pric)
    unrealized_pnl: Decimal           # 평가손익금액(fm_evlu_pfls_amt)
    option_value: Decimal             # 옵션평가금액(fm_opt_evlu_amt)
    option_unrealized_pnl: Decimal    # 옵션평가손익금액(fm_otp_evlu_pfls_amt)
    liquidatable_quantity: Decimal    # 청산가능수량(fm_lqd_psbl_qty)
    exercise_reserved: str            # 행사예약주문여부(ecis_rsvn_ord_yn) -- "Y"/"N"
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasDerivativeMargin:
    """해외선물옵션 증거금상세 -- 계좌의 증거금·주문가능 요약(불변).

    금액은 ``currency`` 통화의 Decimal(원화 아님). ``orderable_amount`` 주문가능금액,
    ``brokerage_margin`` 위탁증거금액, ``settlement_brokerage_margin`` 정산위탁증거금액,
    ``open_margin`` 미결제증거금액, ``maintenance_margin`` 유지증거금액, ``order_margin``
    주문증거금액, ``additional_margin`` 추가증거금액, ``net_risk_applied`` 계좌순위험증거금
    적용여부("Y"/"N" 원본 문자열). SPAN/EUREX 등 상세 증거금 내역은 ``_raw`` 로 접근한다.
    """

    currency: str                       # 통화코드(crcy_cd)
    orderable_amount: Decimal           # 주문가능금액(fm_ord_psbl_amt)
    brokerage_margin: Decimal           # 위탁증거금액(fm_brkg_mgn_amt)
    settlement_brokerage_margin: Decimal  # 정산위탁증거금액(fm_excc_brkg_mgn_amt)
    open_margin: Decimal                # 미결제증거금액(fm_ustl_mgn_amt)
    maintenance_margin: Decimal         # 유지증거금액(fm_mntn_mgn_amt)
    order_margin: Decimal               # 주문증거금액(fm_ord_mgn_amt)
    additional_margin: Decimal          # 추가증거금액(fm_add_mgn_amt)
    net_risk_applied: str               # 계좌순위험증거금적용여부(acnt_net_risk_mgna_aply_yn)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasDerivativeOrderable:
    """한 해외선물옵션 계약의 주문가능 수량(불변).

    수량은 ``currency`` 통화 기준(가격은 그 통화, 수량은 계약수). ``symbol`` 해외선물FX상품번호,
    ``open_quantity`` 미결제수량, ``liquidatable_quantity`` 청산가능수량, ``new_orderable_quantity``
    신규주문가능수량, ``total_orderable_quantity`` 총주문가능수량, ``market_orderable_quantity``
    시장가총주문가능수량. 타입화하지 않은 필드는 ``_raw`` 로 접근한다.
    """

    symbol: str                       # 해외선물FX상품번호(ovrs_futr_fx_pdno)
    currency: str                     # 통화코드(crcy_cd)
    open_quantity: Decimal            # 미결제수량(fm_ustl_qty)
    liquidatable_quantity: Decimal    # 청산가능수량(fm_lqd_psbl_qty)
    new_orderable_quantity: Decimal   # 신규주문가능수량(fm_new_ord_psbl_qty)
    total_orderable_quantity: Decimal  # 총주문가능수량(fm_tot_ord_psbl_qty)
    market_orderable_quantity: Decimal  # 시장가총주문가능수량(fm_mkpr_tot_ord_psbl_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasDerivativeOrder:
    """해외선물옵션 당일 주문 한 건(체결/미체결, 불변).

    수량·가격은 각 계약 통화 기준(가격은 그 통화, 수량은 계약수). ``order_date`` 주문일자
    (ord_dt) -- 형식오류/공백이면 None. ``order_id`` 주문번호(odno), ``original_order_id``
    원주문번호(orgn_odno), ``symbol`` 해외선물FX상품번호, ``side`` 매수/매도, ``status``
    주문상태코드(ord_stat_cd 원본 문자열), ``order_quantity`` 주문수량, ``order_price``
    주문가격, ``filled_quantity`` 체결수량, ``filled_price`` 체결가격, ``remaining_quantity``
    주문잔량, ``new_liquidation`` 신규청산구분코드(new_lqd_dvsn_cd 원본), ``fuop`` 선물옵션구분
    (fuop_dvsn 원본). 주문번호가 빈 패딩 행은 담기지 않는다. 타입화하지 않은 필드는 ``_raw``.
    """

    order_date: datetime.date | None           # 주문일자(ord_dt)
    order_id: str                     # 주문번호(odno)
    original_order_id: str            # 원주문번호(orgn_odno)
    symbol: str                       # 해외선물FX상품번호(ovrs_futr_fx_pdno)
    side: Side                        # buy / sell (sll_buy_dvsn_cd: 01 매도 / 02 매수)
    status: str                       # 주문상태코드(ord_stat_cd)
    order_quantity: Decimal           # 주문수량(fm_ord_qty)
    order_price: Decimal              # 주문가격(fm_ord_pric)
    filled_quantity: Decimal          # 체결수량(fm_ccld_qty)
    filled_price: Decimal             # 체결가격(fm_ccld_pric)
    remaining_quantity: Decimal       # 주문잔량(fm_ord_rmn_qty)
    new_liquidation: str              # 신규청산구분코드(new_lqd_dvsn_cd)
    fuop: str                         # 선물옵션구분(fuop_dvsn)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasDerivativeFill:
    """해외선물옵션 일별 체결 한 건(불변).

    금액·수량은 ``currency`` 통화의 Decimal(원화 아님). ``date`` 체결일자(dt) -- 형식오류/공백
    이면 None. ``fill_number`` 체결번호(ccno), ``symbol`` 해외선물FX상품번호, ``side`` 매수/매도,
    ``fill_quantity`` 체결수량, ``fill_amount`` 체결금액, ``fee`` 수수료, ``order_date`` 주문일자
    (ord_dt) -- 형식오류/공백이면 None, ``order_id`` 주문번호(odno), ``order_medium`` 주문매체
    구분명(ord_mdia_dvsn_name). 체결번호·주문번호가 빈 패딩 행은 담기지 않는다. 타입화하지 않은
    필드는 ``_raw``.
    """

    date: datetime.date | None                 # 체결일자(dt)
    fill_number: str                  # 체결번호(ccno)
    symbol: str                       # 해외선물FX상품번호(ovrs_futr_fx_pdno)
    side: Side                        # buy / sell (sll_buy_dvsn_cd: 01 매도 / 02 매수)
    fill_quantity: Decimal            # 체결수량(fm_ccld_qty)
    fill_amount: Decimal              # 체결금액(fm_ccld_amt)
    currency: str                     # 통화코드(crcy_cd)
    fee: Decimal                      # 수수료(fm_fee)
    order_date: datetime.date | None           # 주문일자(ord_dt)
    order_id: str                     # 주문번호(odno)
    order_medium: str                 # 주문매체구분명(ord_mdia_dvsn_name)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasDerivativeDailyOrder:
    """해외선물옵션 일별 주문 한 건(기간 주문내역, 불변).

    수량·가격은 각 계약 통화 기준(가격은 그 통화, 수량은 계약수). ``date`` 주문접수일자(dt) --
    형식오류/공백이면 None. ``order_date`` 주문일자(ord_dt) -- 형식오류/공백이면 None,
    ``order_id`` 주문번호(odno), ``original_order_id`` 원주문번호(orgn_odno), ``symbol``
    해외선물FX상품번호, ``revise_cancel_type`` 정정취소구분코드(rvse_cncl_dvsn_cd 원본 문자열),
    ``side`` 매수/매도, ``order_quantity`` 주문수량, ``order_price`` 주문가격, ``filled_quantity``
    체결수량, ``filled_price`` 체결가격, ``remaining_quantity`` 주문잔량, ``reject_reason``
    거부사유명(rjct_rson_name 원본 문자열), ``trade_end_date`` 거래종료일자(trad_end_dt) --
    형식오류/공백이면 None. 주문번호가 빈 패딩 행은 담기지 않는다. 타입화하지 않은 필드는 ``_raw``.
    """

    date: datetime.date | None        # 주문접수일자(dt)
    order_date: datetime.date | None  # 주문일자(ord_dt)
    order_id: str                     # 주문번호(odno)
    original_order_id: str            # 원주문번호(orgn_odno)
    symbol: str                       # 해외선물FX상품번호(ovrs_futr_fx_pdno)
    revise_cancel_type: str           # 정정취소구분코드(rvse_cncl_dvsn_cd)
    side: Side                        # buy / sell (sll_buy_dvsn_cd: 01 매도 / 02 매수)
    order_quantity: Decimal           # 주문수량(fm_ord_qty)
    order_price: Decimal              # 주문가격(fm_ord_pric)
    filled_quantity: Decimal          # 체결수량(fm_ccld_qty)
    filled_price: Decimal             # 체결가격(fm_ccld_pric)
    remaining_quantity: Decimal       # 주문잔량(fm_ord_rmn_qty)
    reject_reason: str                # 거부사유명(rjct_rson_name)
    trade_end_date: datetime.date | None  # 거래종료일자(trad_end_dt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasDerivativeFillHistory:
    """해외선물옵션 일별 체결내역 -- 기간 합계 요약과 체결 한 벌(불변).

    금액·수량은 통화별 조회의 Decimal(전체 통화 조회면 통화가 섞일 수 있어 각 체결의
    ``currency`` 를 함께 본다). ``total_fill_quantity`` 총체결수량, ``total_futures_amount``
    총선물약정금액, ``total_options_amount`` 총옵션약정금액, ``total_fee`` 수수료합계.
    ``fills`` 체결 목록. 타입화하지 않은 요약 필드는 ``_raw``.
    """

    total_fill_quantity: Decimal      # 총체결수량(fm_tot_ccld_qty)
    total_futures_amount: Decimal     # 총선물약정금액(fm_tot_futr_agrm_amt)
    total_options_amount: Decimal     # 총옵션약정금액(fm_tot_opt_agrm_amt)
    total_fee: Decimal                # 수수료합계(fm_fee_smtl)
    fills: tuple[OverseasDerivativeFill, ...]  # 체결내역(output1)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "fills", tuple(self.fills))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasDerivativePnl:
    """해외선물옵션 기간 손익 한 행 -- 통화별/종목별 손익 요약(불변).

    금액·수량은 ``currency`` 통화의 Decimal(원화 아님). 통화별 집계 행에서는 ``symbol`` 이
    빈 문자열이고, 종목별 집계 행에서는 종목번호가 채워진다. ``currency`` 통화코드(crcy_cd),
    ``symbol`` 해외선물FX상품번호(ovrs_futr_fx_pdno), ``buy_quantity`` 매수수량(fm_buy_qty),
    ``sell_quantity`` 매도수량(fm_sll_qty), ``realized_pnl`` 청산손익금액(fm_lqd_pfls_amt),
    ``fee`` 수수료(fm_fee), ``net_pnl`` 순손익금액(fm_net_pfls_amt), ``open_buy_quantity``
    미결제매수수량(fm_ustl_buy_qty), ``open_sell_quantity`` 미결제매도수량(fm_ustl_sll_qty),
    ``unrealized_pnl`` 미결제평가손익금액(fm_ustl_evlu_pfls_amt), ``open_agreement_amount``
    미결제약정금액(fm_ustl_agrm_amt). 타입화하지 않은 필드는 ``_raw``.
    """

    currency: str                     # 통화코드(crcy_cd)
    symbol: str                       # 해외선물FX상품번호(ovrs_futr_fx_pdno)
    buy_quantity: Decimal             # 매수수량(fm_buy_qty)
    sell_quantity: Decimal            # 매도수량(fm_sll_qty)
    realized_pnl: Decimal             # 청산손익금액(fm_lqd_pfls_amt)
    fee: Decimal                      # 수수료(fm_fee)
    net_pnl: Decimal                  # 순손익금액(fm_net_pfls_amt)
    open_buy_quantity: Decimal        # 미결제매수수량(fm_ustl_buy_qty)
    open_sell_quantity: Decimal       # 미결제매도수량(fm_ustl_sll_qty)
    unrealized_pnl: Decimal           # 미결제평가손익금액(fm_ustl_evlu_pfls_amt)
    open_agreement_amount: Decimal    # 미결제약정금액(fm_ustl_agrm_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasDerivativePnlHistory:
    """해외선물옵션 기간 손익 -- 통화별 집계와 종목별 집계 두 벌(불변).

    ``by_currency`` 통화별 손익(output1), ``by_symbol`` 종목별 손익(output2). 두 벌 모두
    :class:`OverseasDerivativePnl` 행이며 금액·수량은 각 행 통화의 Decimal(원화 아님).
    타입화하지 않은 요약 필드는 ``_raw``.
    """

    by_currency: tuple[OverseasDerivativePnl, ...]  # 통화별 손익(output1)
    by_symbol: tuple[OverseasDerivativePnl, ...]    # 종목별 손익(output2)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "by_currency", tuple(self.by_currency))
        object.__setattr__(self, "by_symbol", tuple(self.by_symbol))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
