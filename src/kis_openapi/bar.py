"""기간별 OHLCV 바(DATA) -- :class:`Bar` 와 :type:`Interval`.

한 시간 버킷(일/주/월봉)의 OHLCV. ``Interval`` 은 현재 지원 토큰만 담아 정직하게 유지한다
(Yahoo 스타일; ``mo``=월). 분봉(``1m``..``1h``)은 전용 시간앵커 페이지네이션이 필요해 후속
슬라이스에서 ``Interval`` 을 확장하며 추가한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal

#: 바 간격 -- 현재 지원분(일/주/월). 분봉은 후속.
Interval = Literal["1d", "1wk", "1mo"]


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
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))
