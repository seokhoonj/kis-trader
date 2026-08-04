"""지수/업종 시세 DATA -- :class:`IndexQuote`, :class:`IndexIntradayPoint`.

:class:`~kis_openapi.index.Index` 핸들(``kis.index(code)``)이 돌려주는 지수(업종) 시세 타입들이다.
:class:`IndexQuote` 는 현재가 스냅샷(:meth:`~kis_openapi.index.Index.quote`), :class:`IndexIntradayPoint`
는 당일 시간대별 시계열(:meth:`~kis_openapi.index.Index.intraday`)의 한 점이다. 종목의
:class:`~kis_openapi.quote.Quote` 와 달리 체결가가 아니라 **지수 레벨**(``value``)을 담는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class IndexQuote:
    """한 지수(업종)의 현재가 스냅샷(불변).

    ``value`` 는 지수 레벨(포인트), ``change`` / ``change_percent`` 는 전일대비로 하락이면 음수.
    ``advances`` 등은 그 지수 구성종목 중 상승/하락/보합/상한/하한 종목 수. ``as_of`` 는 조회 시각.
    """

    code: str                         # 업종/지수 코드(예: 0001 KOSPI)
    value: Decimal                    # 지수 현재 레벨(포인트)
    open: Decimal
    high: Decimal
    low: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 구성종목 누적 거래량
    amount: Decimal                   # 누적 거래대금
    advances: int                     # 상승 종목 수
    declines: int                     # 하락 종목 수
    unchanged: int                    # 보합 종목 수
    limit_up: int                     # 상한 종목 수
    limit_down: int                   # 하한 종목 수
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class IndexIntradayPoint:
    """지수 당일 시간대별 시계열의 한 점(불변).

    ``time`` 은 그 시각(당일, KST-aware), ``value`` 는 그때의 지수 레벨. ``change`` 는 전일대비로
    하락이면 음수. ``interval_volume`` 은 그 구간의 체결 거래량(``volume`` 은 그 시각까지 누적).
    """

    time: datetime                    # 그 시각(당일, KST-aware)
    value: Decimal                    # 지수 레벨
    change: Decimal                   # 전일대비(부호 포함)
    volume: int                       # 누적 거래량
    interval_volume: int              # 그 구간 체결 거래량
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
