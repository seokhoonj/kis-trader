"""회원사(증권사) 매매(DATA) -- :class:`BrokerActivity` 와 :class:`BrokerActivitySummary`.

한 종목에 대해 매도·매수 상위 회원사(증권사)를 담는다. 회원사 하나를 :class:`BrokerActivity`
로 묶고, 매도/매수 쪽을 각각 상위 리스트로 정리한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


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
        object.__setattr__(self, "sellers", tuple(self.sellers))
        object.__setattr__(self, "buyers", tuple(self.buyers))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class BrokerDailyActivity:
    """한 회원사의 한 종목 일별 매수·매도와 시세."""

    symbol: str
    member_code: str
    trading_date: date
    sell_quantity: int
    buy_quantity: int
    net_buy_quantity: int
    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class BrokerTradeTick:
    """회원사 실시간 매매동향의 한 체결 틱."""

    timestamp: datetime
    member_name: str
    symbol_name: str
    price: Decimal
    change: Decimal
    execution_volume: int
    cumulative_net_buy_quantity: int
    foreign_broker_net_buy_quantity: int
    foreign_net_buy_change: int
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class BrokerTradeTicks:
    """회원사 실시간 총매도·총매수와 체결 틱 목록."""

    total_sell_quantity: int
    total_buy_quantity: int
    ticks: tuple[BrokerTradeTick, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticks", tuple(self.ticks))
