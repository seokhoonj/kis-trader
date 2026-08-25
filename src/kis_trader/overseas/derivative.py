"""해외 선물/옵션 핸들 -- :class:`OverseasDerivative`.

한 해외 파생 계약을 조회하는 핸들이다: ``kis.overseas.futures("ESZ25").quote()`` 처럼. 국내 파생
핸들(:class:`~kis_trader.derivative.FuturesContract` / :class:`~kis_trader.derivative.OptionContract`)과
대칭이며, 해외 계약은 시장구분(F/O) 대신 시리즈코드(``series_code``)로 식별하고 계약 통화·거래소가 시세에
함께 온다.

핸들은 :class:`~kis_trader.client.KISClient` 가 ``kis.overseas.futures(series_code)`` /
``kis.overseas.option(series_code)`` 로 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..bar import Bar, Interval
from ..order import _OVERSEAS_FO_EXCHANGE, Order, coerce_decimal, mint_client_order_id
from ._engine import derivatives as overseas_derivatives_api

if TYPE_CHECKING:
    from .._literals import DerivativeProduct, Numeric
    from ..client import KISClient
    from ..order import OrderType, Side
    from ..order_book import OrderBook
    from ..report import ExecutionReport
    from ..trade import Trade
    from .entities.derivative import OverseasDerivativeDetail, OverseasDerivativeQuote


class OverseasDerivative:
    """한 해외 선물/옵션 계약에 대한 조회·주문 핸들. 세션과 시리즈코드·시장을 안다.

    보통 직접 만들지 않고 ``kis.overseas.futures`` / ``kis.overseas.option``
    으로 얻는다. ``market`` 은 ``"future"``(선물) 또는 ``"option"``(옵션)이고, ``symbol`` 은
    시리즈코드(예: ESZ25).
    """

    symbol: str
    market: DerivativeProduct

    def __init__(self, client: KISClient, symbol: str, *, market: DerivativeProduct) -> None:
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

    # --- 발주(해외선물옵션 OTFM3001U; 계좌 + 안전 엔진 -- 종목 핸들 buy/sell 과 대칭) ---
    def buy(
        self, *, quantity: Numeric, limit_price: Numeric | None = None,
        stop_price: Numeric | None = None, order_type: OrderType | None = None,
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 계약을 매수한다 -- ``limit_price`` 를 주면 지정가, ``stop_price`` 를 주면 STOP,
        둘 다 없으면 시장가(``order_type`` 으로 명시 가능). ``quantity`` 는 계약 수(정수)이며,
        결제통화는 계약이 정한다(해외 파생 종목코드가 통화별로 유일). ``client_order_id`` 는
        멱등키(생략 시 자동 발행).

        KIS URL/TR-ID: ``POST /uapi/overseas-futureoption/v1/trading/order`` (실전 ``OTFM3001U``,
        모의 미지원).

        **실전투자 전용**(모의투자 미지원)이라 ``environment="paper"`` 세션은 와이어 전에 fail-closed.
        이중체결 방지·타임아웃 재시도 금지가 안전 엔진에서 자동 적용된다. 실제 주문 경로는 실거래이며
        여기서 라이브 검증할 수 없다(목킹 테스트로만 확인). 계좌 미설정·모의 세션은
        :class:`~kis_trader.errors.KISUsageError`, 접수 거부는 ``OrderRejectedError``, 타임아웃(체결
        불명)은 ``OrderTimeoutError`` -- 후자는 ``kis.orders.reconcile`` 로 확인한다."""
        return self._client._place_order(self._make_order(
            "buy", quantity=quantity, limit_price=limit_price, stop_price=stop_price,
            order_type=order_type, client_order_id=client_order_id,
        ))

    def sell(
        self, *, quantity: Numeric, limit_price: Numeric | None = None,
        stop_price: Numeric | None = None, order_type: OrderType | None = None,
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 계약을 매도한다 -- 가격구분·``quantity``·**실전 전용** 규칙은 :meth:`buy` 와 동일
        (방향만 매도)."""
        return self._client._place_order(self._make_order(
            "sell", quantity=quantity, limit_price=limit_price, stop_price=stop_price,
            order_type=order_type, client_order_id=client_order_id,
        ))

    def _make_order(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None,
        stop_price: Numeric | None, order_type: OrderType | None,
        client_order_id: str | None,
    ) -> Order:
        """해외선물옵션(OSFO) 발주 :class:`Order` 를 조립한다. ``order_type`` 미지정 시 가격 인자로
        정한다: ``limit_price`` 있으면 지정가, ``stop_price`` 있으면 STOP, 둘 다 없으면 시장가."""
        resolved_type: OrderType = order_type or (
            "limit" if limit_price is not None
            else "stop" if stop_price is not None
            else "market"
        )
        return Order(
            symbol=self.symbol, side=side, order_type=resolved_type,
            quantity=coerce_decimal(quantity, "quantity"),
            limit_price=None if limit_price is None else coerce_decimal(limit_price, "limit_price"),
            stop_price=None if stop_price is None else coerce_decimal(stop_price, "stop_price"),
            exchange=_OVERSEAS_FO_EXCHANGE,
            client_order_id=client_order_id or mint_client_order_id(),
        )
