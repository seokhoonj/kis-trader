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
