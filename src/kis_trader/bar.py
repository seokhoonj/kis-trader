"""기간별 OHLCV 바(DATA) -- :class:`Bar` 와 :type:`Interval`.

한 시간 버킷의 OHLCV. ``Interval`` 은 현재 지원 토큰만 담아 정직하게 유지한다(``mo``=월).
``1m`` 은 당일 1분봉이고, ``1d``/``1wk``/``1mo`` 는 기간봉이다. 더 굵은 분봉
(5/15/30분·1시간)은 KIS가 이 형태로 제공하지 않아 담지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal

from ._internal._freeze import freeze_vendor_payload

#: 바 간격 -- 당일 1분봉(1m)과 일/주/월봉.
Interval = Literal["1m", "1d", "1wk", "1mo"]

#: 기간봉 전용 간격 -- 일/주/월만. 분봉을 제공하지 않는 조회(지수 일별통계, 최근 30일 시세 등)의
#: 공개 파라미터 타입. 이 조회에 ``"1m"`` 을 주면 엔진이 항상 거부하므로 타입에서 배제한다(정직한 계약).
PeriodInterval = Literal["1d", "1wk", "1mo"]


@dataclass(frozen=True, slots=True)
class Bar:
    """한 시간 버킷의 OHLCV(불변). ``timestamp`` 는 버킷 시작 시각(KST-aware; 일/주/월봉은 자정)."""

    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
