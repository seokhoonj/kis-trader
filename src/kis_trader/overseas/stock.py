"""해외 종목 핸들 -- :class:`OverseasStock`.

한 해외 종목에 대해 행위를 시키는 핸들이다: ``kis.overseas.stock("AAPL").quote()`` 처럼.
현재가·기간봉·호가·체결·주문(정규/미국 오버나이트/미국 예약)을 준다. 국내 전용 조회(재무·투자자
수급 등)는 애초에 이 핸들에 없다(:class:`~kis_trader.domestic.stock.DomesticStock` 로 분리 --
잘못된 조합은 런타임 오류가 아니라 타입체커가 먼저 잡는다).

시세는 세션 인증만으로 되고, 주문은 세션에 묶인 계좌 + 내부 안전엔진(이중체결 방지·재시도 금지)을
쓴다. 핸들은 세션이 만들어 준다(``kis.overseas.stock``) -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from ._engine import market_data as overseas_market_data
from .._stock_base import _StockBase
from ..bar import Bar, Interval
from ..errors import KISUsageError
from ..order import Order, Side, TimeInForce
from ..order_book import OrderBook
from .entities.quote import OverseasCurrentPrice
from ..quote import Quote
from ..report import ExecutionReport
from ..trade import Trade

if TYPE_CHECKING:
    from .._literals import Numeric
    from ..client import KISClient


class OverseasStock(_StockBase):
    """해외 종목 핸들 -- 현재가·기간봉·호가·체결·주문(정규/미국주간/미국예약). ``kis.overseas.stock(symbol, exchange=)``.

    거래소코드(``exchange`` = NAS/NYS/AMS/TSE/HKS/...)로 식별한다. ``exchange`` 를 생략하면 세션이 KIS
    종목 마스터로 자동 해석한다."""

    is_overseas = True
    exchange: str

    def __init__(self, client: KISClient, symbol: str, *, exchange: str) -> None:
        super().__init__(client, symbol)
        self.exchange = exchange

    # --- 시세 ---
    def quote(self) -> Quote:
        """현재가 스냅샷."""
        return overseas_market_data.fetch_quote(
            self._client.transport, symbol=self.symbol, exchange=self.exchange
        )

    def current_price(self) -> OverseasCurrentPrice:
        """현재체결가와 누적 거래량·거래대금(간이 엔드포인트)."""
        return overseas_market_data.fetch_current_price(
            self._client.transport, symbol=self.symbol, exchange=self.exchange
        )

    def bars(
        self, interval: Interval = "1d", *, start: str | date | None = None,
        end: str | date | None = None, adjusted: bool = True, max_bars: int | None = None,
    ) -> list[Bar]:
        """OHLCV 바(과거->현재). ``1d``/``1wk``/``1mo`` 는 [start, end] 기간봉(``start`` 필요), ``1m`` 은
        최신 분봉을 뒤로 밀며 모은다(``start``/``end`` 무시, ``max_bars`` 로 최근 N개).

        ``start`` > ``end``, ``max_bars`` <= 0, 기간봉인데 ``start`` 없음이면 :class:`~kis_trader.
        errors.KISUsageError`. 응답 손상·페이지 상한 초과는 :class:`~kis_trader.errors.KISError`."""
        return overseas_market_data.fetch_bars(
            self._client.transport, symbol=self.symbol, exchange=self.exchange,
            interval=interval, start=start, end=end, adjusted=adjusted, max_bars=max_bars,
        )

    def order_book(self) -> OrderBook:
        """호가창 스냅샷(미국 10단계 / 그 외 국가 1단계)."""
        return overseas_market_data.fetch_order_book(
            self._client.transport, symbol=self.symbol, exchange=self.exchange
        )

    def trades(self) -> list[Trade]:
        """최근 체결 목록(time & sales; 최신순)."""
        return overseas_market_data.fetch_trades(
            self._client.transport, symbol=self.symbol, exchange=self.exchange
        )

    # --- 미국 오버나이트 거래(한국 낮 시간대; 미국 NAS/NYS/AMS 만) ---
    def overnight_buy(
        self, *, quantity: Numeric, limit_price: Numeric, client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 미국 종목을 **미국 오버나이트 거래**로 매수한다(한국 낮 시간대). 지정가만(``limit_price`` 필수).

        정규 :meth:`buy` 와 같은 안전 엔진(이중체결 방지·재시도 금지)을 공유하되 세션이 달라 정정·취소는
        미국주간 전용 엔드포인트로 라우팅된다(반환 리포트의 ``client_order_id`` 로 ``kis.orders.cancel``/
        ``kis.orders.modify``). **모의투자 미지원**, 미국(NAS/NYS/AMS)만. 타임아웃 시 ``kis.orders.reconcile``
        은 주간 체결이 정규 체결내역에 없어 자동 확정하지 않고 None(in-flight 유지)을 준다 -- 수동 확인이
        필요하다. 예외는 :meth:`buy` 와 같다(접수 거부 ``OrderRejectedError``·타임아웃 ``OrderTimeoutError``)."""
        return self._client._place_order(self._make_overnight_order(
            "buy", quantity=quantity, limit_price=limit_price, client_order_id=client_order_id))

    def overnight_sell(
        self, *, quantity: Numeric, limit_price: Numeric, client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 미국 종목을 미국 오버나이트 거래로 매도한다(계약은 :meth:`overnight_buy` 와 동일, 방향만 매도)."""
        return self._client._place_order(self._make_overnight_order(
            "sell", quantity=quantity, limit_price=limit_price, client_order_id=client_order_id))

    def _make_overnight_order(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric, client_order_id: str | None,
    ) -> Order:
        return Order.limit(self.symbol, side=side, quantity=quantity, limit_price=limit_price,
                           exchange=self.exchange, session="overnight", client_order_id=client_order_id)

    def _make_order(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None,
        time_in_force: TimeInForce, client_order_id: str | None,
    ) -> Order:
        if limit_price is None:
            raise KISUsageError("해외 주문은 지정가만 지원한다 -- limit_price 를 지정하라(시장가 미지원).")
        return Order.limit(self.symbol, side=side, quantity=quantity, limit_price=limit_price,
                           time_in_force=time_in_force, client_order_id=client_order_id,
                           exchange=self.exchange)

    def _reserve(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None, end_date: str | None,
        client_order_id: str | None,
    ) -> ExecutionReport:
        if end_date is not None:      # 미국 예약: 지정가만, end_date 미지원
            raise KISUsageError("해외 예약주문은 end_date 를 지원하지 않는다(미국 예약).")
        if limit_price is None:
            raise KISUsageError("해외 예약주문은 지정가만 지원한다 -- limit_price 를 지정하라.")
        return self._client._place_overseas_reserved_order(
            symbol=self.symbol, side=side, quantity=quantity, limit_price=limit_price,
            exchange=self.exchange, client_order_id=client_order_id,
        )
