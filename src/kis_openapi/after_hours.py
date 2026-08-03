"""시간외 단일가(DATA) -- :class:`AfterHoursQuote`.

정규장 마감 후 시간외 단일가 세션의 최우선 호가와 예상체결 정보다. 이 세션 밖에서는 값이 비어
올 수 있어(장중 조회 등) 필드를 모두 optional 로 둔다(빈 값은 ``None``, 있는데 깨지면 예외).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class AfterHoursQuote:
    """시간외 단일가 스냅샷(불변). 세션이 열려 있지 않으면 대부분 ``None`` 일 수 있다.

    ``change`` / ``change_percent`` 는 예상체결가의 전일대비(하락이면 음수). ``as_of`` 는 조회 시각.
    """

    symbol: str
    bid: Decimal | None               # 최우선 매수호가
    ask: Decimal | None               # 최우선 매도호가
    expected_price: Decimal | None    # 시간외 단일가 예상체결가
    expected_quantity: int | None     # 예상체결 수량
    change: Decimal | None            # 예상체결가 전일대비(부호 포함)
    change_percent: Decimal | None    # 예상체결가 전일대비율(부호 포함)
    as_of: datetime                   # KST-aware
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))
