"""종목 핸들 -- :class:`Ticker`.

한 종목에 대해 행위를 시키는 핸들이다: ``kis.ticker("005930").quote()`` 처럼. 시세는 세션
인증만으로 되고, 주문은 세션에 묶인 계좌 + 내부 안전엔진을 쓴다(후속 슬라이스). 사용자는 KIS
구조(quotations/trading/국내/해외)를 몰라도 되고, 시장은 심볼로 자동 판별된다.

핸들은 :class:`~kis_openapi.client.KisClient` 가 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from ._domestic import market_data
from .bar import Bar, Interval
from .instrument import DomesticBoard, resolve_market
from .order_book import OrderBook
from .quote import Quote

if TYPE_CHECKING:
    from .client import KisClient


class Ticker:
    """한 종목에 대한 행위 핸들. 세션(:class:`KisClient`)과 심볼/시장을 안다.

    보통 직접 만들지 않고 :meth:`KisClient.ticker` 로 얻는다(세션이 필요하므로).
    """

    symbol: str
    market: DomesticBoard

    def __init__(self, client: KisClient, symbol: str, *, market: DomesticBoard | None = None) -> None:
        self._client = client
        self.symbol = symbol
        self.market = resolve_market(symbol, market=market)

    def quote(self) -> Quote:
        """현재가 스냅샷."""
        return market_data.fetch_quote(self._client.transport, symbol=self.symbol, market=self.market)

    def bars(
        self,
        *,
        interval: Interval = "1d",
        start: str | date,
        end: str | date | None = None,
        adjusted: bool = True,
        max_bars: int | None = None,
    ) -> list[Bar]:
        """[start, end] 구간의 OHLCV 바(과거->현재). ``interval`` 은 1d/1wk/1mo(분봉 후속).

        ``start`` > ``end`` 이거나 ``max_bars`` <= 0 이면 :class:`~kis_openapi.errors.KisUsageError`,
        응답 손상(비배열 output2)이나 페이지 상한 초과는 :class:`~kis_openapi.errors.KisError`.
        """
        return market_data.fetch_bars(
            self._client.transport, symbol=self.symbol, market=self.market,
            interval=interval, start=start, end=end, adjusted=adjusted, max_bars=max_bars,
        )

    def order_book(self) -> OrderBook:
        """10단계 호가창 스냅샷."""
        return market_data.fetch_order_book(
            self._client.transport, symbol=self.symbol, market=self.market
        )
