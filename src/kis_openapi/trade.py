"""체결(DATA) -- :class:`Trade`.

한 건의 체결(가격/수량/시각)이다. 종목의 최근 체결 목록(time & sales)을 이루는 낱개 단위로,
:meth:`~kis_openapi.ticker.Ticker.trades` 가 최신순 리스트로 돌려준다. 호가창(:class:`~kis_openapi
.order_book.OrderBook`)이 "지금 걸린 주문"이라면 ``Trade`` 는 "이미 이뤄진 거래"다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class Trade:
    """한 건의 체결(불변).

    ``timestamp`` 는 체결 시각으로 시각(HHMMSS)만 벤더가 주므로 날짜는 **조회일**을 붙인다
    (KST-aware) -- 장 시간 밖에서 조회하면 직전 세션 체결이 조회일 날짜로 찍힐 수 있다.
    ``change`` / ``change_percent`` 는 그 체결가의 전일대비(하락이면 음수). ``quantity`` 는 그
    체결의 체결 수량(누적이 아니라 낱건).
    """

    symbol: str
    timestamp: datetime               # 체결 시각(시각은 벤더, 날짜는 조회일; KST)
    price: Decimal                    # 체결가
    quantity: int                     # 이 체결의 수량(낱건)
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
