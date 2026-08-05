"""투자자별 매매(DATA) -- :class:`InvestorFlow` 와 :class:`InvestorActivity`.

하루치, 투자자 주체(개인/외국인/기관)별 매수·매도·순매수를 담는다. 주체 하나의 활동을
:class:`InvestorActivity` 로 묶어 ``flow.foreign.net_buy_volume`` 처럼 읽히게 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class InvestorActivity:
    """한 투자자 주체의 하루치 매매(불변). ``net_buy_*`` 는 순매도면 음수. 대금은 원(KRW)."""

    buy_volume: int                   # 매수 수량(주)
    sell_volume: int                  # 매도 수량(주)
    net_buy_volume: int               # 순매수 수량(주; 음수면 순매도)
    buy_value: Decimal                # 매수 대금(원)
    sell_value: Decimal               # 매도 대금(원)
    net_buy_value: Decimal            # 순매수 대금(원; 음수면 순매도)


@dataclass(frozen=True, slots=True)
class InvestorFlow:
    """하루치 투자자별 매매(불변). 주체별 활동은 :class:`InvestorActivity`."""

    symbol: str
    trading_date: date
    close: Decimal                    # 그날 종가
    individual: InvestorActivity      # 개인
    foreign: InvestorActivity         # 외국인
    institutional: InvestorActivity   # 기관
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class InvestorEstimate:
    """한 시점의 장중 투자자 순매수 추정(불변).

    장중 실시간 추정치라 확정값이 아니라 **가(假)추정**이다(장 마감 후 확정치는
    :class:`InvestorFlow`). ``foreign_net`` / ``institutional_net`` 은 외국인/기관의 추정 순매수
    수량, ``total_net`` 은 합계. ``timestamp`` 는 조회일 날짜를 붙인 시각(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 시각(조회일 날짜; KST)
    foreign_net: int                  # 외국인 추정 순매수 수량
    institutional_net: int            # 기관 추정 순매수 수량
    total_net: int                    # 추정 순매수 합계
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
