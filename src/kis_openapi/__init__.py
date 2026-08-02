"""kis_openapi -- a clean, action-centric Python client for the Korea Investment &
Securities (KIS) Open API.

행위 중심 API: 세션 :class:`KisClient` 에서 종목 핸들 :class:`~kis_openapi.ticker.Ticker`
(``kis.ticker("005930").quote()``)와 계좌 조회를 시킨다. KIS URL 구조는 노출되지 않는다.

공개 식별자는 국제 표준 금융 영어(Yahoo/Alpaca/Coinbase/FIX/ISO); KIS URL·TR-id 매핑은
내부 조회 계층 docstring에 있다.
"""

from __future__ import annotations

from .balance import Balance, Portfolio, Position
from .bar import Bar, Interval
from .client import KisClient
from .order_book import OrderBook, PriceLevel
from .orderable import BuyableAmount, SellableQuantity
from .quote import Quote
from .ticker import Ticker

__version__ = "0.0.0"

__all__ = [
    "Balance",
    "Bar",
    "BuyableAmount",
    "Interval",
    "KisClient",
    "OrderBook",
    "Portfolio",
    "Position",
    "PriceLevel",
    "Quote",
    "SellableQuantity",
    "Ticker",
]
