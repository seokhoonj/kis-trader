"""기업행위 캘린더(DATA) -- :class:`DividendEvent` 등.

한국예탁결제원(KSD)이 제공하는 기업행위 일정의 한 항목이다. 배당·유상증자·무상증자·주주총회 같은 이벤트를
기준일 기준으로 준다. :class:`~kis_openapi.calendar.CalendarQueries`(``kis.calendar``)가 기간
조회로 리스트를 돌려준다. 날짜는 시각/시간대 없는 순수 달력 날짜라 :class:`datetime.date` 로 둔다
(시세 타임스탬프의 KST-aware ``datetime`` 과 구분).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class DividendEvent:
    """한 종목의 배당 일정(불변).

    ``record_date`` 는 배당 기준일, ``dividend_kind`` 는 배당종류(결산/중간 등), ``cash_dividend`` 는
    1주당 현금배당금, ``cash_dividend_rate`` / ``stock_dividend_rate`` 는 현금/주식 배당률(%)이다.
    지급일(``cash_pay_date`` / ``stock_pay_date`` / ``odd_lot_pay_date``)은 미정이면 ``None``.
    ``high_dividend`` 는 고배당종목 표시 여부.
    """

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    record_date: date                 # 기준일(record_date)
    dividend_kind: str                # 배당종류(divi_kind; 결산/중간 등)
    face_value: Decimal | None        # 액면가(face_val)
    cash_dividend: Decimal | None     # 1주당 현금배당금(per_sto_divi_amt)
    cash_dividend_rate: Decimal | None  # 현금배당률 %(divi_rate)
    stock_dividend_rate: Decimal | None  # 주식배당률 %(stk_divi_rate)
    cash_pay_date: date | None        # 배당금지급일(divi_pay_dt)
    stock_pay_date: date | None       # 주식배당지급일(stk_div_pay_dt)
    odd_lot_pay_date: date | None     # 단주대금지급일(odd_pay_dt)
    stock_kind: str                   # 주식종류(stk_kind; 보통/우선)
    high_dividend: bool               # 고배당종목여부(high_divi_gb)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class IPOSubscription:
    """한 종목의 공모주 청약 일정(불변).

    ``offer_price`` 는 공모가, ``subscription_period`` 는 청약기간(벤더가 준 텍스트 범위 그대로),
    ``pay_date`` / ``refund_date`` / ``list_date`` 는 납입/환불/상장일(미정이면 ``None``),
    ``lead_manager`` 는 주간사, ``allocated_quantity`` 는 당사 배정물량이다.
    """

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    record_date: date                 # 기준일(record_date)
    offer_price: Decimal | None       # 공모가(fix_subscr_pri)
    face_value: Decimal | None        # 액면가(face_value)
    subscription_period: str          # 청약기간 텍스트(subscr_dt; 예 "2024/03/25 ~ 2024/03/26")
    pay_date: date | None             # 납입일(pay_dt)
    refund_date: date | None          # 환불일(refund_dt)
    list_date: date | None            # 상장/등록일(list_dt)
    lead_manager: str                 # 주간사(lead_mgr)
    capital_before: Decimal | None    # 공모전 자본금(pub_bf_cap)
    capital_after: Decimal | None     # 공모후 자본금(pub_af_cap)
    allocated_quantity: int | None    # 당사 배정물량(assign_stk_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class RightsOffering:
    """한 종목의 유상증자 일정(불변).

    ``new_shares`` 는 발행할 주식수, ``allocation_rate`` 는 확정배정율(%), ``discount_rate`` 는
    할인율(%), ``issue_price`` 는 발행예정가, ``ex_rights_date`` 는 권리락일, ``subscription_start`` 는
    청약 시작일, ``subscription_period`` 는 청약기간 텍스트, ``stock_kind`` 는 주식종류 코드다.
    """

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    record_date: date                 # 기준일(record_date)
    total_shares: int | None          # 발행주식수(tot_issue_stk_qty)
    new_shares: int | None            # 발행할 주식수(issue_stk_qty)
    allocation_rate: Decimal | None   # 확정배정율 %(fix_rate)
    discount_rate: Decimal | None     # 할인율 %(disc_rate)
    issue_price: Decimal | None       # 발행예정가(fix_price)
    ex_rights_date: date | None       # 권리락일(right_dt)
    subscription_start: date | None   # 청약 시작일(sub_term_ft)
    subscription_period: str          # 청약기간 텍스트(sub_term)
    list_date: date | None            # 상장/등록일(list_date)
    stock_kind: str                   # 주식종류 코드(stk_kind)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class BonusIssue:
    """한 종목의 무상증자 일정(불변)."""

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    record_date: date                 # 기준일(record_date)
    allocation_rate: Decimal | None   # 확정배정율 %(fix_rate)
    odd_lot_base_price: Decimal | None  # 단주기준가(odd_rec_price)
    ex_rights_date: date | None       # 권리락일(right_dt)
    odd_lot_pay_date: date | None     # 단주대금지급일(odd_pay_dt)
    list_date: date | None            # 상장/등록일(list_date)
    total_shares: int | None          # 발행주식수(tot_issue_stk_qty)
    new_shares: int | None            # 발행할 주식수(issue_stk_qty)
    stock_kind: str                   # 주식종류 코드(stk_kind)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class CapitalReduction:
    """한 종목의 자본감소 일정(불변)."""

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    record_date: date                 # 기준일(record_date)
    stock_kind: str                   # 주식종류(stk_kind)
    reduction_type: str               # 감자구분(reduce_cap_type)
    reduction_rate: Decimal | None    # 감자비율 %(reduce_cap_rate)
    computation_method: str           # 계산방법(comp_way)
    trading_halt_period: str          # 매매거래정지기간 텍스트(td_stop_dt)
    list_date: date | None            # 상장/등록일(list_dt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class MergerSplit:
    """한 종목의 합병분할 일정(불변)."""

    symbol: str                       # 종목코드(sht_cd)
    record_date: date                 # 기준일(record_date)
    company_code: str                 # 회사코드(cust_cd)
    company_name: str                 # 회사명(cust_nm)
    counterparty_code: str            # 상대회사코드(opp_cust_cd)
    counterparty_name: str            # 상대회사명(opp_cust_nm)
    merge_type: str                   # 합병구분(merge_type)
    merge_ratio: Decimal | None       # 합병비율(merge_rate)
    trading_halt_period: str          # 매매거래정지기간 텍스트(td_stop_dt)
    list_date: date | None            # 상장/등록일(list_dt)
    odd_lot_pay_date: date | None     # 단주대금지급일(odd_amt_pay_dt)
    total_shares: int | None          # 발행주식수(tot_issue_stk_qty)
    new_shares: int | None            # 발행할 주식수(issue_stk_qty)
    sequence: str                     # 일련번호(seq)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ShareholderMeeting:
    """한 종목의 주주총회 일정(불변)."""

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    record_date: date                 # 기준일(record_date)
    meeting_date: date | None         # 총회일(gen_meet_dt)
    meeting_type: str                 # 총회구분(gen_meet_type)
    agenda: str                       # 안건(agenda)
    voting_shares: int | None         # 의결권 주식수(vote_tot_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class MandatoryDeposit:
    """한 종목의 의무예치 내역(불변)."""

    symbol: str                       # 종목코드(sht_cd, 영숫자 가능)
    name: str                         # 종목명(isin_name)
    deposit_shares: int | None        # 예치 주식수(stk_qty)
    deposit_period: str               # 예치기간 텍스트(depo_date)
    deposit_reason: str               # 예치사유(depo_reason)
    issued_shares_ratio: Decimal | None  # 총발행수량 대비 비율(tot_issue_qty_per_rate)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ListingInfo:
    """한 종목의 상장정보(불변)."""

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    list_date: date                   # 상장일(list_dt)
    stock_kind: str                   # 주식종류(stk_kind)
    issue_type: str                   # 발행구분(issue_type)
    new_shares: int | None            # 발행 주식수(issue_stk_qty)
    total_shares: int | None          # 총발행 주식수(tot_issue_stk_qty)
    issue_price: Decimal | None       # 발행가(issue_price)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ParValueChange:
    """한 종목의 액면교체 일정(불변)."""

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    record_date: date                 # 기준일(record_date)
    face_value_before: Decimal | None  # 교체 전 액면가(inter_bf_face_amt)
    face_value_after: Decimal | None  # 교체 후 액면가(inter_af_face_amt)
    trading_halt_period: str          # 매매거래정지기간 텍스트(td_stop_dt)
    list_date: date | None            # 상장/등록일(list_dt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ForfeitedShares:
    """한 종목의 실권주 일정(불변)."""

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    record_date: date                 # 기준일(record_date)
    subscription_period: str          # 청약기간 텍스트(subscr_dt)
    subscription_price: Decimal | None  # 청약가(subscr_price)
    subscription_shares: int | None   # 청약 주식수(subscr_stk_qty)
    refund_date: date | None          # 환불일(refund_dt)
    list_date: date | None            # 상장/등록일(list_dt)
    lead_manager: str                 # 주간사(lead_mgr)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class AppraisalRights:
    """한 종목의 주식매수청구 일정(불변)."""

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    record_date: date                 # 기준일(record_date)
    stock_kind: str                   # 주식종류(stk_kind)
    opposition_period: str            # 반대의사 접수기간 텍스트(opp_opi_rcpt_term)
    buyback_request_period: str       # 매수청구 접수기간 텍스트(buy_req_rcpt_term)
    buyback_price: Decimal | None     # 매수청구가(buy_req_price)
    payment_date: date | None         # 매수대금 지급일(buy_amt_pay_dt)
    meeting_date: date | None         # 총회일(get_meet_dt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
