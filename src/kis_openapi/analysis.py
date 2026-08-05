"""per-ticker 일별 시세분석(DATA) -- :class:`CreditBalancePoint` / :class:`ShortSalePoint`.

한 종목의 일별 신용잔고/공매도 추이 한 점이다. :meth:`~kis_openapi.ticker.Ticker.credit_balance_trend`
/ :meth:`~kis_openapi.ticker.Ticker.short_sale_trend` 가 일자 리스트(최근->과거)로 돌려준다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class CreditBalancePoint:
    """하루의 신용잔고 스냅샷(불변).

    ``margin_loan_*`` 은 융자(신용매수) 잔고, ``stock_loan_*`` 은 대주(신용매도) 잔고다. ``*_shares``
    는 잔고 주수, ``*_amount`` 는 잔고 금액, ``*_ratio`` 는 잔고 비율(%). ``price`` / ``change`` 는
    그날 종목 시세. ``timestamp`` 는 영업일(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 영업일(KST-aware)
    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    margin_loan_shares: int           # 융자 잔고 주수(whol_loan_rmnd_stcn)
    margin_loan_amount: Decimal       # 융자 잔고 금액(whol_loan_rmnd_amt)
    margin_loan_ratio: Decimal | None  # 융자 잔고 비율 %(whol_loan_rmnd_rate)
    stock_loan_shares: int            # 대주 잔고 주수(whol_stln_rmnd_stcn)
    stock_loan_amount: Decimal        # 대주 잔고 금액(whol_stln_rmnd_amt)
    stock_loan_ratio: Decimal | None  # 대주 잔고 비율 %(whol_stln_rmnd_rate)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class LoanPoint:
    """하루의 대차거래(주식 대여) 스냅샷(불변).

    ``new_shares`` 는 그날 신규 대차 체결 주수, ``redeemed_shares`` 는 상환 주수, ``balance_shares``
    / ``balance_amount`` 는 대차잔고 주수/금액, ``balance_change`` 는 잔고 전일대비 주수다. 대차잔고는
    공매도 공급 여력의 대리지표로 본다. ``price`` / ``change`` 는 그날 종목 시세, ``timestamp`` 는
    영업일(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 영업일(KST-aware)
    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    new_shares: int                   # 신규 대차 체결 주수(new_stcn)
    redeemed_shares: int              # 상환 주수(rdmp_stcn)
    balance_shares: int               # 대차잔고 주수(rmnd_stcn)
    balance_amount: Decimal           # 대차잔고 금액(rmnd_amt)
    balance_change: int               # 잔고 전일대비 주수(prdy_rmnd_vrss)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ShortSalePoint:
    """하루의 공매도 스냅샷(불변).

    ``short_volume`` 은 그날 공매도 체결량, ``short_volume_ratio`` 는 거래량 대비 공매도 비중(%),
    ``short_amount`` 는 공매도 대금, ``short_avg_price`` 는 공매도 평균가. ``close`` / ``change`` 는
    그날 종목 종가. ``timestamp`` 는 영업일(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 영업일(KST-aware)
    close: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int                       # 종목 거래량
    short_volume: int                 # 공매도 체결량(ssts_cntg_qty)
    short_volume_ratio: Decimal | None  # 공매도 비중 %(ssts_vol_rlim)
    short_amount: Decimal             # 공매도 대금(ssts_tr_pbmn)
    short_average_price: Decimal | None  # 공매도 평균가(avrg_prc)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
