"""프로그램매매(DATA) -- :class:`ProgramTradePoint`.

한 종목의 장중 시간대별 프로그램매매(기관·외국인의 바스켓/차익거래 자동주문) 흐름 한 점이다.
:meth:`~kis_openapi.ticker.Ticker.program_trades` 가 시간 순 리스트로 돌려준다. 프로그램 순매수가
크게 늘면 지수·수급에 영향이 커서 장중 관찰 지표로 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class ProgramTradePoint:
    """한 시점의 프로그램매매 스냅샷(불변).

    ``buy_volume`` / ``sell_volume`` 은 그 시각까지의 프로그램 매수/매도 수량, ``net_volume`` 은
    순매수(매수-매도) 수량, ``net_amount`` 는 순매수 금액. ``price`` / ``change`` 는 그 시각의 종목
    시세다. ``timestamp`` 는 조회일 날짜를 붙인 시각(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 시각(조회일 날짜; KST)
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 종목 누적 거래량
    buy_volume: int                   # 프로그램 매수 수량
    sell_volume: int                  # 프로그램 매도 수량
    net_volume: int                   # 프로그램 순매수 수량(매수-매도)
    net_amount: Decimal               # 프로그램 순매수 금액
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class DailyProgramTradePoint:
    """한 종목의 하루치 프로그램매매 합계와 시세."""

    symbol: str
    trading_date: date
    close: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    amount: Decimal
    sell_volume: int
    buy_volume: int
    net_volume: int
    sell_amount: Decimal
    buy_amount: Decimal
    net_amount: Decimal
    net_volume_change: int
    net_amount_change: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
