"""선물/옵션(파생) 시세 DATA -- :class:`DerivativeQuote` / :class:`UnderlyingQuote`.

파생 핸들(:class:`~kis_trader.derivative.FuturesContract` / :class:`~kis_trader.derivative.OptionContract`,
``kis.domestic.futures(code)`` / ``kis.domestic.option(code)``)이 돌려주는 한 계약의 현재가 스냅샷이다. 종목의 :class:`~kis_trader.quote.Quote` 와 달리 파생 고유의
미결제약정(open interest)·베이시스·이론가·괴리율을 담는다. 옵션 그릭스(delta/gamma/theta/vega/rho)와
변동성·잔존일수는 ``_raw`` 로 접근한다(선물엔 없거나 무의미하므로). :class:`UnderlyingQuote` 는
선물과 그 기초자산(지수)을 나란히 보여주는 스냅샷이다(베이시스 판단용).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class DerivativeQuote:
    """선물/옵션 계약의 현재가 스냅샷(불변).

    ``change`` / ``change_percent`` 는 전일대비로 하락이면 음수. ``open_interest`` 는 미결제약정,
    ``basis`` 는 선물-기초자산 베이시스, ``theoretical_price`` 는 이론가, ``premium`` 은 괴리율(%).
    베이시스/이론가/괴리율은 계약에 따라 없을 수 있어 ``None`` 이다.
    """

    code: str
    name: str
    current_price: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    previous_close: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int
    open_interest: int
    theoretical_price: Decimal | None
    basis: Decimal | None             # 베이시스(선물-기초자산)
    premium: Decimal | None           # 괴리율(%)
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class UnderlyingQuote:
    """선물과 그 기초자산(지수)을 나란히 담는 스냅샷(불변).

    ``underlying_*`` 은 기초자산(예: KOSPI200 지수), ``futures_*`` 은 선물 계약의 현재가·전일대비다
    (각각 자기 부호 필드로 복원, 하락이면 음수). 선물가-기초자산가 = 베이시스 판단의 기본 재료다.
    :meth:`~kis_trader.derivative.FuturesContract.underlying_quote` 가 돌려준다. ``as_of`` 는 조회 시각.
    """

    symbol: str                       # 선물 계약코드
    name: str                         # HTS 종목명(hts_kor_isnm)
    underlying_price: Decimal         # 기초자산 현재가(unas_prpr)
    underlying_change: Decimal        # 기초자산 전일대비(부호 포함)
    underlying_change_percent: Decimal  # 기초자산 전일대비율(부호 포함)
    underlying_volume: int            # 기초자산 누적 거래량(unas_acml_vol)
    futures_price: Decimal            # 선물 현재가(futs_prpr)
    futures_change: Decimal           # 선물 전일대비(부호 포함)
    futures_change_percent: Decimal   # 선물 전일대비율(부호 포함)
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ExpectedExecutionPoint:
    """선물·옵션의 한 시각 예상체결가."""

    timestamp: datetime
    price: Decimal
    change: Decimal
    change_percent: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ExpectedExecutionTrend:
    """현재 예상체결 요약과 일중 예상체결가 추이."""

    code: str
    name: str
    price: Decimal
    change: Decimal
    change_percent: Decimal
    base_price: Decimal
    points: tuple[ExpectedExecutionPoint, ...]
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", tuple(self.points))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OptionExpiry:
    """지수옵션의 한 만기 월물(불변).

    ``code`` 는 만기 년월 코드(예: ``"0V05"``), ``year_month`` 는 만기 년월(``"YYYYMM"``)이다.
    ``kis.domestic.option_expiries`` 가 유효한 월물 목록을 돌려준다 -- 옵션
    계약코드를 만들기 전에 상장된 만기를 확인하는 용도.
    """

    code: str                         # 만기 년월 코드(mtrt_yymm_code)
    year_month: str                   # 만기 년월(mtrt_yymm, "YYYYMM")
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OptionBoardRow:
    """옵션 전광판의 한 행사가 시세와 그릭스(불변)."""

    strike: Decimal
    code: str
    price: Decimal
    change: Decimal
    change_percent: Decimal
    bid: Decimal | None
    ask: Decimal | None
    volume: int
    open_interest: int
    delta: Decimal | None
    gamma: Decimal | None
    vega: Decimal | None
    theta: Decimal | None
    rho: Decimal | None
    implied_volatility: Decimal | None
    theoretical_price: Decimal | None
    time_value: Decimal | None
    intrinsic_value: Decimal | None
    atm_class: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OptionBoard:
    """한 만기월의 옵션 콜/풋 전광판(불변)."""

    expiry: str
    underlying: str
    calls: tuple[OptionBoardRow, ...]
    puts: tuple[OptionBoardRow, ...]
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "calls", tuple(self.calls))
        object.__setattr__(self, "puts", tuple(self.puts))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class FuturesBoardQuote:
    """옵션 전광판 하단에 표시되는 한 선물 계약의 시세 스냅샷."""

    code: str
    name: str
    price: Decimal
    change: Decimal
    change_percent: Decimal
    theoretical_price: Decimal
    volume: int
    ask: Decimal
    bid: Decimal
    open_interest: int
    high: Decimal
    low: Decimal
    days_to_expiry: int
    total_ask_quantity: int
    total_bid_quantity: int
    expected_price: Decimal
    expected_change: Decimal
    expected_change_percent: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
