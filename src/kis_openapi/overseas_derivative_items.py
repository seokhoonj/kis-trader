"""해외 선물/옵션 시세 DATA -- :class:`OverseasDerivativeQuote`.

해외 파생(선물/옵션)은 시리즈코드(``srs_cd``, 예: ESZ25 = E-mini S&P 2025.12) 하나로 계약을
식별한다. 국내 파생 :class:`~kis_openapi.derivative_items.DerivativesQuote` 와 달리 계약 통화
(``currency``)·거래소(``exchange``)·만기/최종거래일·정산가·틱사이즈·증거금을 함께 담는다(해외 계약은
거래소·통화가 제각각이라 시세에 명시된다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class OverseasDerivativeQuote:
    """한 해외 선물/옵션 계약의 현재가 스냅샷(불변).

    ``last`` 는 현재가, ``settlement_price`` 는 정산가. ``change`` / ``change_percent`` 는 전일대비
    (하락이면 음수). ``bid`` / ``ask`` 는 최우선 호가와 그 수량(``bid_size`` / ``ask_size``),
    ``total_bid_quantity`` / ``total_ask_quantity`` 는 총 잔량. 금액은 계약 통화(``currency``) 기준
    이고 ``exchange`` 는 상장 거래소, ``expiry_date`` / ``last_trade_date`` / ``remaining_days`` 는
    만기 관련, ``tick_size`` 는 호가 단위, ``margin`` 은 증거금.
    """

    symbol: str                       # 시리즈코드(srs_cd)
    last: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    previous_close: Decimal
    settlement_price: Decimal | None  # 정산가(sttl_price)
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int
    bid: Decimal | None
    ask: Decimal | None
    bid_size: int | None
    ask_size: int | None
    total_bid_quantity: int | None
    total_ask_quantity: int | None
    currency: str                     # 계약 통화(crc_cd)
    exchange: str                     # 거래소코드(exch_cd)
    expiry_date: datetime | None      # 만기일(KST-aware)
    last_trade_date: datetime | None  # 최종거래일(KST-aware)
    remaining_days: int | None        # 잔존일수
    tick_size: Decimal | None         # 틱사이즈
    margin: Decimal | None            # 증거금(trst_mgn)
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasDerivativeDetail:
    """한 해외 선물/옵션 계약의 명세(불변).

    시세(:class:`OverseasDerivativeQuote`)가 "지금 얼마"라면 이건 "어떤 계약인가" -- 거래소·통화·
    품목종류·틱사이즈/틱가치·계약크기·증거금·만기 관련·결제구분 같은 계약 조건이다.
    :meth:`~kis_openapi.overseas_derivative.OverseasDerivative.detail` 가 돌려준다.
    """

    symbol: str
    exchange: str                     # 거래소코드(exch_cd)
    currency: str                     # 거래통화(crc_cd)
    product_class: str                # 품목종류(clas_cd)
    tick_size: Decimal | None         # 틱사이즈(tick_sz)
    tick_value: Decimal | None        # 틱가치(tick_val)
    contract_size: Decimal | None     # 계약크기(ctrt_size)
    margin: Decimal | None            # 증거금(trst_mgn)
    price_digits: int | None          # 가격표시진법(disp_digit)
    listing_date: datetime | None     # 상장일(trd_fr_date; KST-aware)
    expiry_date: datetime | None      # 만기일(expr_date; KST-aware)
    last_trade_date: datetime | None  # 최종거래일(trd_to_date; KST-aware)
    remaining_days: int | None        # 잔존일수(remn_cnt)
    settlement_type: str              # 최종결제구분(stl_tp)
    tradable: str                     # 매매여부(stat_tp)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasDerivativeMarketHours:
    """해외 선물/옵션 상품군의 장운영시간(불변).

    계약과 무관한 상품군·거래소·클래스별 일정이다. 오전장·오후장·익일장·기본시장 시작/종료
    시각은 응답 값이 비었거나 유효하지 않으면 ``None`` 이다.
    """

    product_group_code: str
    product_group_name: str
    exchange_code: str
    exchange_name: str
    kind: str
    class_code: str
    class_name: str
    am_open: time | None
    am_close: time | None
    pm_open: time | None
    pm_close: time | None
    next_day_open: time | None
    next_day_close: time | None
    base_open: time | None
    base_close: time | None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasFuturesOpenInterest:
    """한 날짜의 해외선물 상품 CFTC 미결제약정 구성(불변)."""

    product: str
    cftc_code: str
    date: date
    speculative_long: int
    speculative_short: int
    speculative_spread: int
    hedging_long: int
    hedging_short: int
    total_open_interest: int
    unclassified_long: int
    unclassified_short: int
    customer_speculative_long: int
    customer_speculative_short: int
    customer_speculative_spread: int
    customer_hedging_long: int
    customer_hedging_short: int
    customer_total: int
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
