"""장내채권 시세 DATA -- :class:`BondQuote`, :class:`BondValuation`.

:meth:`~kis_openapi.bond.Bond.quote` 가 돌려주는 한 채권의 현재가 스냅샷이다. 종목의
:class:`~kis_openapi.quote.Quote` 와 달리 채권 고유의 **수익률**(``yield_rate``)을 함께 담는다.
채권가는 액면 대비 가격(관행상 액면 10,000 기준)이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class BondQuote:
    """한 채권의 현재가 스냅샷(불변).

    ``price`` 는 채권 가격(액면 대비), ``yield_rate`` 는 그 가격에 대응하는 수익률(%). ``change`` /
    ``change_percent`` 는 전일대비로 하락이면 음수.
    """

    code: str
    name: str
    price: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    previous_close: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int
    yield_rate: Decimal | None        # 수익률(%)
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class BondDailyPrice:
    """한 거래일의 채권 가격·등락·누적거래량."""

    date: date
    code: str
    price: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class BondProfile:
    """한 장내채권의 기본/발행 정보(불변).

    :meth:`~kis_openapi.bond.Bond.info` 가 돌려준다. 시세(:class:`BondQuote`)가 "지금 얼마"라면
    ``BondProfile`` 는 "어떤 채권인가" -- 발행일·만기일·표면금리·만기수익률·통화 같은 채권의 계약 조건이다.
    ``coupon_rate`` 는 표면금리(%), ``yield_to_maturity`` 는 만기수익률(%), ``interest_period_months``
    는 이자 지급 주기(개월). 날짜/비율은 없으면 ``None``. 세부는 ``_raw``.
    """

    code: str
    name: str
    english_name: str
    currency: str                     # ISO 통화(iso_crcy_cd)
    issue_date: date | None           # 발행일(issu_dt)
    maturity_date: date | None        # 만기(상환)일(rdpt_dt)
    listing_date: date | None         # 상장일(lstg_dt)
    coupon_rate: Decimal | None       # 표면금리 %(ksd_rcvg_bond_srfc_inrt)
    discount_rate: Decimal | None     # 할인율 %(ksd_rcvg_bond_dsct_rt)
    redemption_rate: Decimal | None   # 만기상환율 %(bond_expd_rdpt_rt)
    yield_to_maturity: Decimal | None  # 만기수익률 %(bond_expd_asrc_erng_rt)
    interest_period_months: int | None  # 이자 계산 주기(개월; int_caltm_mcnt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class BondIssuance:
    """한 장내채권의 상세 발행 조건과 상태(불변).

    액면가·발행액·상장잔액·발행기관·이자지급 주기와 주요 일자를 담는다. 평가기관별
    신용등급은 ``credit_ratings`` 의 기관 코드(KIS, KBP, NICE, FNP)로 제공한다.
    """

    code: str
    name: str
    english_name: str
    classification: str
    face_value: Decimal
    issue_amount: Decimal
    outstanding_amount: Decimal
    issuer_name: str
    interest_payment_months: int
    coupon_rate: Decimal
    discount_rate: Decimal
    redemption_rate: Decimal
    yield_to_maturity: Decimal
    issue_date: date | None
    listing_date: date | None
    maturity_date: date | None
    redemption_date: date | None
    previous_interest_date: date | None
    next_interest_date: date | None
    credit_ratings: Mapping[str, str]
    is_inflation_linked: bool
    is_trade_suspended: bool
    is_electronic: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "credit_ratings", MappingProxyType(dict(self.credit_ratings)))
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class BondValuation:
    """한 날짜의 채권 평가기관 단가·수익률과 평균(불변).

    ``average_price`` / ``average_yield`` 는 평가기관 평균이고, 기관별 값은
    ``agency_prices`` / ``agency_yields`` / ``credit_ratings`` 에 기관 코드(KIS, KBP, NICE,
    FNP)로 담는다. ``risk_free_prices`` 는 응답에 값이 있는 기관만 포함한다.
    """

    date: date
    code: str
    name: str
    average_price: Decimal
    average_yield: Decimal
    agency_prices: Mapping[str, Decimal]
    agency_yields: Mapping[str, Decimal]
    credit_ratings: Mapping[str, str]
    risk_free_prices: Mapping[str, Decimal]
    changed: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "agency_prices", MappingProxyType(dict(self.agency_prices)))
        object.__setattr__(self, "agency_yields", MappingProxyType(dict(self.agency_yields)))
        object.__setattr__(self, "credit_ratings", MappingProxyType(dict(self.credit_ratings)))
        object.__setattr__(self, "risk_free_prices", MappingProxyType(dict(self.risk_free_prices)))
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
