"""종목 핸들 -- :class:`Ticker`.

한 종목에 대해 행위를 시키는 핸들이다: ``kis.ticker("005930").quote()`` 처럼. 시세는 세션
인증만으로 되고, 주문은 세션에 묶인 계좌 + 내부 안전엔진을 쓴다. 사용자는 KIS
구조(quotations/trading/국내/해외)를 몰라도 되고, 시장은 심볼로 자동 판별된다.

핸들은 :class:`~kis_openapi.client.KisClient` 가 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from ._domestic import account as account_api
from ._domestic import etf as etf_api
from ._domestic import market_data
from ._overseas import market_data as overseas_market_data
from .after_hours import AfterHoursQuote
from .bar import Bar, Interval
from .broker import BrokerActivitySummary
from .errors import KisUsageError
from .etf_items import EtfComponent, EtfNav, EtfNavHistoryPoint
from .instrument import DomesticBoard, resolve_market
from .investor import InvestorFlow
from .order import Order, Side, TimeInForce
from .order_book import OrderBook
from .orderable import BuyableAmount, SellableQuantity
from .quote import Quote
from .report import ExecutionReport
from .trade import Trade

if TYPE_CHECKING:
    from .client import KisClient


class Ticker:
    """한 종목에 대한 행위 핸들. 세션(:class:`KisClient`)과 심볼/시장을 안다.

    국내는 시장 보드(:attr:`market`), 해외는 거래소코드(:attr:`exchange`)로 식별한다 -- 둘 중 하나만
    있다. 보통 직접 만들지 않고 :meth:`KisClient.ticker` 로 얻는다(세션이 필요하므로).
    """

    symbol: str
    market: DomesticBoard | None      # 국내 보드(해외면 None)
    exchange: str | None              # 해외 거래소코드(국내면 None)

    def __init__(
        self,
        client: KisClient,
        symbol: str,
        *,
        market: DomesticBoard | None = None,
        exchange: str | None = None,
    ) -> None:
        self._client = client
        self.symbol = symbol
        if exchange is not None:      # 해외: 거래소코드로 식별
            self.market = None
            self.exchange = exchange
        else:                         # 국내: 심볼/보드로 판별
            self.market = resolve_market(symbol, market=market)
            self.exchange = None

    @property
    def is_overseas(self) -> bool:
        """해외 종목이면 True(거래소코드로 식별)."""
        return self.exchange is not None

    def _domestic_market(self) -> DomesticBoard:
        """국내 전용 메서드가 쓰는 시장 보드. 해외 티커면 -- 아직 해외 미구현이라 -- 명확히 거부한다."""
        if self.market is None:
            raise KisUsageError(
                f"해외 티커({self.symbol}@{self.exchange})에선 이 기능이 아직 미지원이다 "
                f"-- 해외는 .quote() 만 된다."
            )
        return self.market

    def quote(self) -> Quote:
        """현재가 스냅샷(국내/해외 자동 라우팅)."""
        if self.exchange is not None:
            return overseas_market_data.fetch_quote(
                self._client.transport, symbol=self.symbol, exchange=self.exchange
            )
        return market_data.fetch_quote(self._client.transport, symbol=self.symbol, market=self.market)

    def bars(
        self,
        *,
        interval: Interval = "1d",
        start: str | date | None = None,
        end: str | date | None = None,
        adjusted: bool = True,
        max_bars: int | None = None,
    ) -> list[Bar]:
        """OHLCV 바(과거->현재). ``interval="1m"`` 은 당일 1분봉(``start``/``end``/``adjusted``
        무시, 최신 세션; ``max_bars`` 로 최근 N개), ``1d``/``1wk``/``1mo`` 는 [start, end]
        기간봉(``start`` 필요).

        ``start`` > ``end``, ``max_bars`` <= 0, 기간봉인데 ``start`` 없음이면
        :class:`~kis_openapi.errors.KisUsageError`. 응답 손상(비배열 output2)이나 페이지 상한
        초과는 :class:`~kis_openapi.errors.KisError`. 해외는 일/주/월봉만(분봉 미지원).
        """
        if self.exchange is not None:
            return overseas_market_data.fetch_bars(
                self._client.transport, symbol=self.symbol, exchange=self.exchange,
                interval=interval, start=start, end=end, adjusted=adjusted, max_bars=max_bars,
            )
        return market_data.fetch_bars(
            self._client.transport, symbol=self.symbol, market=self.market,
            interval=interval, start=start, end=end, adjusted=adjusted, max_bars=max_bars,
        )

    def order_book(self) -> OrderBook:
        """10단계 호가창 스냅샷."""
        return market_data.fetch_order_book(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def trades(self) -> list[Trade]:
        """최근 체결 목록(time & sales; 최신순)."""
        return market_data.fetch_trades(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def investor_flows(self) -> list[InvestorFlow]:
        """일자별 투자자(개인/외국인/기관) 매매동향(최신순)."""
        return market_data.fetch_investor_flows(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def broker_activity(self) -> BrokerActivitySummary:
        """매도/매수 상위 회원사(증권사) 매매 비중."""
        return market_data.fetch_broker_activity(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def after_hours_quote(self) -> AfterHoursQuote:
        """시간외 단일가 스냅샷(예상체결가·최우선호가)."""
        return market_data.fetch_after_hours_quote(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def nav(self) -> EtfNav:
        """ETF/ETN 순자산가치(NAV) 스냅샷(NAV·괴리율·추적오차율·순자산총액). 이 종목이 ETF/ETN
        일 때만 유효하다(아니면 서버가 거부). 시장 체결가는 :meth:`quote`."""
        self._domestic_market()        # 국내 ETF 전용
        return etf_api.fetch_etf_nav(self._client.transport, symbol=self.symbol)

    def components(self) -> list[EtfComponent]:
        """ETF 구성종목(PDF) 목록 -- 각 구성종목의 시세·ETF 내 구성 비중·평가금액. 이 종목이 ETF
        일 때만 유효하다(아니면 서버가 거부)."""
        self._domestic_market()        # 국내 ETF 전용
        return etf_api.fetch_etf_components(self._client.transport, symbol=self.symbol)

    def nav_history(self, *, start: str | date, end: str | date) -> list[EtfNavHistoryPoint]:
        """일별 NAV-가격 추이(과거->현재). ``start``/``end`` 는 기간(YYYYMMDD 또는 ``date``). 각
        거래일의 종가·NAV·괴리율로 프리미엄/디스카운트 추이를 본다. 이 종목이 ETF/ETN 일 때만 유효."""
        self._domestic_market()        # 국내 ETF 전용
        return etf_api.fetch_etf_nav_history(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    def buyable(self, *, limit_price: object | None = None) -> BuyableAmount:
        """이 종목의 매수가능 여력(현금 기준·미수 포함 최대). ``limit_price`` 없으면 시장가 기준.

        계좌 정보 없이 생성한 세션이면 :class:`~kis_openapi.errors.KisUsageError`.
        """
        self._domestic_market()        # 해외 미지원
        cano, product_code = self._client._require_account()
        return account_api.fetch_buyable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, symbol=self.symbol, limit_price=limit_price,
        )

    def sellable(self) -> SellableQuantity:
        """이 종목의 매도가능 수량. **모의투자 미지원**(demo면 :class:`~kis_openapi.errors.KisUsageError`)."""
        self._domestic_market()        # 해외 미지원
        cano, product_code = self._client._require_account()
        return account_api.fetch_sellable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, symbol=self.symbol,
        )

    # --- 주문 실행(안전 엔진 위임; 계좌 정보 필요) --------------------
    def buy(
        self, *, quantity: object, price: object | None = None,
        time_in_force: TimeInForce = "day", client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 매수한다 -- ``price`` 를 주면 지정가, 없으면 시장가.

        이중체결 방지·타임아웃 재시도 금지가 안전 엔진에서 자동 적용된다. 계좌 정보가 없으면
        :class:`~kis_openapi.errors.KisUsageError`, 조회전용(퇴직연금 등) 계좌면
        :class:`~kis_openapi.errors.AccountNotOrderable`. 접수 거부는 ``OrderRejectedError``,
        타임아웃(체결 불명)은 ``OrderTimeoutError`` -- 후자는 :meth:`KisClient.reconcile` 로 확인한다.
        """
        return self._client._place_order(self._make_order("buy", quantity, price, time_in_force, client_order_id))

    def sell(
        self, *, quantity: object, price: object | None = None,
        time_in_force: TimeInForce = "day", client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 매도한다 -- ``price`` 를 주면 지정가, 없으면 시장가(계약은 :meth:`buy` 와 동일)."""
        return self._client._place_order(self._make_order("sell", quantity, price, time_in_force, client_order_id))

    def _make_order(
        self, side: Side, quantity: object, price: object | None,
        time_in_force: TimeInForce, client_order_id: str | None,
    ) -> Order:
        self._domestic_market()        # 해외 주문은 아직 미지원 -- 명확히 거부(국내 주문 오전송 방지)
        if price is None:
            return Order.market(self.symbol, side=side, quantity=quantity,
                                time_in_force=time_in_force, client_order_id=client_order_id)
        return Order.limit(self.symbol, side=side, quantity=quantity, limit_price=price,
                          time_in_force=time_in_force, client_order_id=client_order_id)
