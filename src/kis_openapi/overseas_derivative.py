"""해외 선물/옵션 핸들 -- :class:`OverseasDerivative`.

한 해외 파생 계약을 조회하는 핸들이다: ``kis.overseas.futures("ESZ25").quote()`` 처럼. 국내 파생
핸들(:class:`~kis_openapi.derivative.Derivative`)과 대칭이며, 해외 계약은 시장구분(F/O) 대신
시리즈코드(``srs_cd``)로 식별하고 계약 통화·거래소가 시세에 함께 온다.

핸들은 :class:`~kis_openapi.client.KISClient` 가 ``kis.overseas.futures(srs_cd)`` /
``kis.overseas.option(srs_cd)`` 로 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._overseas import derivatives as overseas_derivatives_api
from .bar import Bar, Interval

if TYPE_CHECKING:
    from .client import KISClient
    from .order_book import OrderBook
    from .overseas_derivative_items import (
        OverseasDerivativeDetail,
        OverseasDerivativeQuote,
    )
    from .trade import Trade


class OverseasDerivative:
    """한 해외 선물/옵션 계약에 대한 조회 핸들. 세션과 시리즈코드·시장을 안다.

    보통 직접 만들지 않고 ``kis.overseas.futures`` / ``kis.overseas.option``
    으로 얻는다. ``market`` 은 ``"future"``(선물) 또는 ``"option"``(옵션)이고, ``symbol`` 은
    시리즈코드(예: ESZ25).
    """

    symbol: str
    market: str

    def __init__(self, client: KISClient, symbol: str, *, market: str) -> None:
        self._client = client
        self.symbol = symbol
        self.market = market

    def quote(self) -> OverseasDerivativeQuote:
        """계약 현재가 스냅샷(가격·정산가·전일대비·호가·통화·거래소·만기·틱사이즈·증거금)."""
        return overseas_derivatives_api.fetch_quote(
            self._client.transport, srs_cd=self.symbol, market=self.market
        )

    def order_book(self) -> OrderBook:
        """계약 호가창(매수/매도 5단계 심도). 도메스틱과 같은 :class:`OrderBook` 로 돌려준다."""
        return overseas_derivatives_api.fetch_order_book(
            self._client.transport, srs_cd=self.symbol, market=self.market
        )

    def bars(
        self, *, exchange: str, interval: Interval = "1d", max_bars: int = 40
    ) -> list[Bar]:
        """최근 분·일·주·월 OHLCV. 거래소코드는 필수이며 TR별 한도는 40~120건."""
        return overseas_derivatives_api.fetch_bars(
            self._client.transport, srs_cd=self.symbol, market=self.market,
            exchange=exchange, interval=interval, max_bars=max_bars,
            environment=self._client.environment,
        )

    def trades(self, *, exchange: str, max_trades: int = 40) -> list[Trade]:
        """최근 틱 체결을 시간 오름차순으로(최대 40건)."""
        return overseas_derivatives_api.fetch_trades(
            self._client.transport, srs_cd=self.symbol, market=self.market,
            exchange=exchange, max_trades=max_trades, environment=self._client.environment,
        )

    def detail(self) -> OverseasDerivativeDetail:
        """계약 명세(거래소·통화·틱사이즈/틱가치·계약크기·증거금·만기·결제구분)."""
        return overseas_derivatives_api.fetch_detail(
            self._client.transport, srs_cd=self.symbol, market=self.market
        )
