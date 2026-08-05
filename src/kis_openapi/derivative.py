"""선물/옵션 핸들 -- :class:`Derivative`.

한 파생 계약에 대해 조회를 시키는 핸들이다: ``kis.futures("101W09").quote()`` 처럼. 종목 핸들
:class:`~kis_openapi.ticker.Ticker` 와 대칭이며, 파생은 종목이 아니라 계약코드 + 시장구분
(F:지수선물 / O:지수옵션)으로 조회한다.

핸들은 :class:`~kis_openapi.client.KisClient` 가 ``kis.futures(code)`` / ``kis.option(code)`` 로
만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._domestic import derivatives as derivatives_api
from .derivative_items import DerivativesQuote

if TYPE_CHECKING:
    from datetime import date

    from .bar import Bar, Interval
    from .client import KisClient
    from .order_book import OrderBook


class Derivative:
    """한 파생 계약(선물/옵션)에 대한 조회 핸들. 세션(:class:`KisClient`)과 계약코드·시장구분을 안다.

    보통 직접 만들지 않고 :meth:`KisClient.futures` / :meth:`KisClient.option` 으로 얻는다.
    ``market`` 은 F(지수선물) 또는 O(지수옵션).
    """

    code: str
    market: str

    def __init__(self, client: KisClient, code: str, *, market: str) -> None:
        self._client = client
        self.code = code
        self.market = market

    def quote(self) -> DerivativesQuote:
        """계약 현재가 스냅샷(가격·미결제약정·베이시스·이론가·괴리율; 옵션 그릭스는 ``_raw``)."""
        return derivatives_api.fetch_quote(
            self._client.transport, code=self.code, market=self.market
        )

    def order_book(self) -> OrderBook:
        """계약 호가창(5단계 매수/매도 심도)."""
        return derivatives_api.fetch_order_book(
            self._client.transport, code=self.code, market=self.market
        )

    def bars(
        self,
        interval: Interval = "1d",
        *,
        start: str | date | None = None,
        end: str | date | None = None,
        max_bars: int | None = None,
    ) -> list[Bar]:
        """기간봉(일/주/월) OHLCV 를 과거->현재 오름차순으로. ``start`` 가 필요하다(분봉 미지원)."""
        return derivatives_api.fetch_bars(
            self._client.transport, code=self.code, market=self.market,
            interval=interval, start=start, end=end, max_bars=max_bars,
        )
