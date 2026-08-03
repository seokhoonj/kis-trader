"""회원사(증권사) 매매(DATA) -- :class:`BrokerActivity` 와 :class:`BrokerActivitySummary`.

한 종목에 대해 매도·매수 상위 회원사(증권사)를 담는다. 회원사 하나를 :class:`BrokerActivity`
로 묶고, 매도/매수 쪽을 각각 상위 리스트로 정리한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class BrokerActivity:
    """한 회원사(증권사)의 이 종목 매매 비중(불변). ``quantity_change`` 는 직전 대비 증감."""

    member_name: str                  # 회원사(증권사)명
    member_number: str                # 회원사 번호(이름은 있는데 번호가 빈 경우 "")
    volume_share_percent: Decimal     # 이 종목 거래량 점유율(%)
    quantity_change: int              # 직전 대비 수량 증감(주; 음수 가능)
    is_foreign: bool                  # 외국계 회원사 여부


@dataclass(frozen=True, slots=True)
class BrokerActivitySummary:
    """이 종목의 매도/매수 상위 회원사(불변). 각 리스트는 비중 높은 순으로 최대 5개."""

    symbol: str
    sellers: tuple[BrokerActivity, ...]   # 매도 상위 회원사
    buyers: tuple[BrokerActivity, ...]    # 매수 상위 회원사
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
