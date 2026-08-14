"""선물/옵션 핸들 -- :class:`FuturesContract` / :class:`OptionContract` (공통 베이스 :class:`_ContractBase`).

한 파생 계약에 대해 조회를 시키는 핸들이다: ``kis.domestic.futures("101W09").quote()`` 처럼. 종목 핸들
:class:`~kis_trader.domestic.stock.DomesticStock` 와 대칭이며, 파생은 종목이 아니라 계약코드 + 시장구분
(F:지수선물 / O:지수옵션)으로 조회한다. 종목의 국내/해외 분리(:class:`~kis_trader.domestic.stock.DomesticStock` /
:class:`~kis_trader.overseas.stock.OverseasStock`)처럼 선물과 옵션도 겸용 핸들 하나가 아니라 계약종류별 클래스로
나뉜다 -- 선물에만 있는 조회(기초자산 나란히 보기)를 옵션 핸들에서 부르는 잘못된 조합은 런타임 오류가
아니라 애초에 그 메서드가 없다(타입체커가 먼저 잡는다).

핸들은 :class:`~kis_trader.client.KISClient` 가 ``kis.domestic.futures(code)`` / ``kis.domestic.option(code)`` 로
만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._domestic import derivatives as derivatives_api
from .derivative_items import DerivativeQuote, ExpectedExecutionTrend, UnderlyingQuote

if TYPE_CHECKING:
    from datetime import date

    from ._literals import DerivativeMarket
    from .bar import Bar, Interval
    from .client import KISClient
    from .order_book import OrderBook


class _ContractBase:
    """파생 계약 핸들 공통 베이스 -- 선물/옵션이 공유하는 조회(현재가·호가·예상체결·봉)와 세션·계약코드·
    시장구분 배선. 시장구분은 서브클래스가 :attr:`_MARKET` 로 고정한다(선물 F / 옵션 O). 계약종류별
    핸들(:class:`FuturesContract` / :class:`OptionContract`)로만 만든다."""

    #: 서브클래스가 고정하는 파생 시장(보드) 구분 코드. FID_COND_MRKT_DIV_CODE 로 나간다.
    _MARKET: DerivativeMarket

    code: str
    market: DerivativeMarket

    def __init__(self, client: KISClient, code: str) -> None:
        self._client = client
        self.code = code
        self.market = self._MARKET

    def quote(self) -> DerivativeQuote:
        """계약 현재가 스냅샷(가격·미결제약정·베이시스·이론가·괴리율; 옵션 그릭스는 ``_raw``)."""
        return derivatives_api.fetch_quote(
            self._client.transport, code=self.code, market=self.market
        )

    def order_book(self) -> OrderBook:
        """계약 호가창(5단계 매수/매도 심도)."""
        return derivatives_api.fetch_order_book(
            self._client.transport, code=self.code, market=self.market
        )

    def expected_execution_trend(self) -> ExpectedExecutionTrend:
        """현재 예상체결 요약과 당일 시각별 예상체결가 추이."""
        return derivatives_api.fetch_expected_execution_trend(
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
        """OHLCV 봉을 과거->현재 오름차순으로. ``interval="1m"`` 은 당일 1분봉(``start``/``end`` 무시,
        ``max_bars`` 로 최근 N개), ``1d``/``1wk``/``1mo`` 는 ``[start, end]`` 기간봉(``start`` 필요)."""
        return derivatives_api.fetch_bars(
            self._client.transport, code=self.code, market=self.market,
            interval=interval, start=start, end=end, max_bars=max_bars,
        )


class FuturesContract(_ContractBase):
    """지수선물 계약 조회 핸들 -- ``kis.domestic.futures(code)``. 시장구분 F.

    공통 조회(:meth:`quote`·:meth:`order_book`·:meth:`expected_execution_trend`·:meth:`bars`)에 더해
    선물 전용 :meth:`underlying_quote`(선물과 기초자산을 나란히 보는 베이시스 스냅샷)를 가진다."""

    _MARKET: DerivativeMarket = "F"

    def underlying_quote(self) -> UnderlyingQuote:
        """선물과 그 기초자산(지수)을 나란히 담는 스냅샷(베이시스 판단용). 선물 최근월물 계약에서 쓴다."""
        return derivatives_api.fetch_underlying_quote(
            self._client.transport, code=self.code, market="F"
        )


class OptionContract(_ContractBase):
    """지수옵션 계약 조회 핸들 -- ``kis.domestic.option(code)``. 시장구분 O.

    공통 조회(:meth:`quote`·:meth:`order_book`·:meth:`expected_execution_trend`·:meth:`bars`)만 가진다 --
    기초자산 나란히 보기(``underlying_quote``)는 선물 전용이라 옵션 핸들엔 아예 없다(호출 시 구조적
    ``AttributeError``, 타입체커가 먼저 잡는다)."""

    _MARKET: DerivativeMarket = "O"
