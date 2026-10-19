"""해외 지수류 시세 DATA -- :class:`OverseasIndexQuote`.

해외 지수·환율·국채·금선물 핸들(``kis.overseas.index(code)``)의 현재가 스냅샷이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class OverseasIndexQuote:
    """한 해외 지수류 코드의 현재가 스냅샷(불변).

    ``current_value`` 는 현재 값 -- 지수 레벨(kind=index) 또는 환율(fx)·국채 수익률(bond)·금선물 가격(gold)이다.
    ``change`` 와 ``change_percent`` 는 전일대비로 하락이면 음수이며, ``as_of`` 는 조회 시각이다.
    """

    code: str
    name: str
    current_value: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    previous_close: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    previous_volume: int | None
    as_of: datetime
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
