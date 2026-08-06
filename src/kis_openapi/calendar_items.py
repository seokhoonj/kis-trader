"""기업행위 캘린더(DATA) -- :class:`DividendEvent` 등.

한국예탁결제원(KSD)이 제공하는 기업행위 일정의 한 항목이다. 배당·유상증자·주주총회 같은 이벤트를
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
