"""장내채권 시세 DATA -- :class:`BondQuote`.

:meth:`~kis_openapi.bond.Bond.quote` 가 돌려주는 한 채권의 현재가 스냅샷이다. 종목의
:class:`~kis_openapi.quote.Quote` 와 달리 채권 고유의 **수익률**(``yield_rate``)을 함께 담는다.
채권가는 액면 대비 가격(관행상 액면 10,000 기준)이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
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
class BondInfo:
    """한 장내채권의 기본/발행 정보(불변).

    :meth:`~kis_openapi.bond.Bond.info` 가 돌려준다. 시세(:class:`BondQuote`)가 "지금 얼마"라면
    ``BondInfo`` 는 "어떤 채권인가" -- 발행일·만기일·표면금리·만기수익률·통화 같은 채권의 계약 조건이다.
    ``coupon_rate`` 는 표면금리(%), ``yield_to_maturity`` 는 만기수익률(%), ``interest_period_months``
    는 이자 지급 주기(개월). 날짜/비율은 없으면 ``None``. 세부는 ``_raw``.
    """

    code: str
    name: str
    english_name: str
    currency: str                     # ISO 통화(iso_crcy_cd)
    issue_date: datetime | None       # 발행일(issu_dt; KST-aware)
    maturity_date: datetime | None    # 만기(상환)일(rdpt_dt; KST-aware)
    listing_date: datetime | None     # 상장일(lstg_dt; KST-aware)
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
