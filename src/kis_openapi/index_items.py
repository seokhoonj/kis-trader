"""지수/업종 시세 DATA -- :class:`IndexQuote`, :class:`IndexIntradayPoint`, :class:`CategoryIndex`.

:class:`~kis_openapi.index.Index` 핸들(``kis.domestic.index(code)``)이 돌려주는 지수(업종) 시세 타입들이다.
:class:`IndexQuote` 는 현재가 스냅샷(:meth:`~kis_openapi.index.Index.quote`), :class:`IndexIntradayPoint`
는 당일 시간대별 시계열(:meth:`~kis_openapi.index.Index.intraday`)의 한 점, :class:`CategoryIndex` 는
시장 하위 업종 지수(:meth:`~kis_openapi.index.Index.categories`)의 한 항목이다. 종목의
:class:`~kis_openapi.quote.Quote` 와 달리 체결가가 아니라 **지수 레벨**(``index_value``)을 담는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class IndexQuote:
    """한 지수(업종)의 현재가 스냅샷(불변).

    ``index_value`` 는 지수 레벨(포인트), ``change`` / ``change_percent`` 는 전일대비로 하락이면 음수.
    ``advances`` 등은 그 지수 구성종목 중 상승/하락/보합/상한/하한 종목 수. ``as_of`` 는 조회 시각.
    """

    code: str                         # 업종/지수 코드(예: 0001 KOSPI)
    index_value: Decimal              # 지수 현재 레벨(포인트)
    open: Decimal
    high: Decimal
    low: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 구성종목 누적 거래량
    cumulative_trading_amount: Decimal  # 누적 거래대금
    advances: int
    declines: int
    unchanged: int
    limit_up: int
    limit_down: int
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class IndexIntradayPoint:
    """지수 당일 시간대별 시계열의 한 점(불변).

    ``timestamp`` 은 그 시각(당일, KST-aware), ``index_value`` 는 그때의 지수 레벨. ``change`` 는 전일대비로
    하락이면 음수. ``interval_volume`` 은 그 구간의 체결 거래량(``volume`` 은 그 시각까지 누적).
    """

    timestamp: datetime               # 그 시각(당일, KST-aware)
    index_value: Decimal              # 지수 레벨
    change: Decimal                   # 전일대비(부호 포함)
    volume: int                       # 누적 거래량
    interval_volume: int              # 그 구간 체결 거래량
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ExpectedIndexPoint:
    """동시호가 중 한 시각의 예상체결 지수(불변)."""

    timestamp: datetime
    index_value: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    cumulative_trading_amount: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ExpectedIndexQuote:
    """동시호가 중 한 지수의 예상체결 스냅샷(불변)."""

    code: str
    name: str
    index_value: Decimal
    base_index_value: Decimal | None
    change: Decimal
    change_percent: Decimal
    volume: int
    advances: int
    unchanged: int
    declines: int
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ExpectedIndexSnapshot:
    """대표 예상체결 지수와 함께 반환된 시장별 지수 목록(불변)."""

    summary: ExpectedIndexQuote
    markets: tuple[ExpectedIndexQuote, ...]
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "markets", tuple(self.markets))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class IndexDailyPoint:
    """지수 일·주·월 통계의 한 시점(불변)."""

    trading_date: date
    index_value: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    change: Decimal
    change_percent: Decimal
    volume_share: Decimal
    volume: int
    cumulative_trading_amount: Decimal
    sentiment: Decimal
    disparity_20d: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class IndexDailyHistory:
    """조회 시점의 지수 스냅샷과 일·주·월 통계(불변)."""

    snapshot: IndexQuote
    points: tuple[IndexDailyPoint, ...]
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", tuple(self.points))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class CategoryIndex:
    """시장 하위 업종 지수 한 항목(불변).

    한 시장(KOSPI/KOSDAQ/KOSPI200) 아래 업종별 지수 중 하나다. ``index_value`` 는 그 업종 지수 레벨,
    ``change`` / ``change_percent`` 는 전일대비로 하락이면 음수. ``volume_share`` / ``amount_share`` 는
    그 업종이 시장 전체 거래량 / 거래대금에서 차지하는 비중(%).
    """

    code: str
    name: str
    index_value: Decimal              # 업종 지수 레벨
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량
    cumulative_trading_amount: Decimal  # 누적 거래대금
    volume_share: Decimal             # 거래량 비중(%)
    amount_share: Decimal             # 거래대금 비중(%)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
