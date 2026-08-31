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

from .._stock_base import _StockBase
from ..bar import Bar, Interval
from ..errors import KISUsageError
from ..order import AlgoStrategy, Order, Side, TimeInForce
from ..order_book import OrderBook
from ..quote import Quote
from ..report import ExecutionReport
from ..trade import Trade
from ._engine import market_data as overseas_market_data
from .entities.quote import OverseasCurrentPrice

if TYPE_CHECKING:
    from .._literals import Numeric, ReservedCurrency
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

    # --- 즉시 주문(정규; 미국주식은 TWAP/VWAP 알고리즘 분할 지원) ---
    def buy(  # 해외는 algo 파라미터를 더 받는다(도메스틱 베이스보다 넓힌 오버라이드)
        self, *, quantity: Numeric, limit_price: Numeric | None = None,
        time_in_force: TimeInForce = "day", client_order_id: str | None = None,
        algo: AlgoStrategy | None = None, algo_window: tuple[str, str] | None = None,
    ) -> ExecutionReport:
        """이 해외 종목을 매수한다 -- 지정가만(``limit_price`` 필수; 시장가 미지원). ``algo``(twap/vwap)를
        주면 미국주식 알고리즘 분할주문(TWAP/VWAP)으로, ``algo_window=(시작, 종료)``(HHMMSS)를 함께 주면
        그 시간창에 집행하고 생략하면 정규장 종료까지 집행한다. algo 는 **미국(NAS/NYS/AMS) 실전 전용**
        이라 그 밖의 거래소·모의투자면 :class:`~kis_trader.errors.KISUsageError` 로 fail-closed 한다.

        이중체결 방지·타임아웃 재시도 금지는 :meth:`~kis_trader._stock_base._StockBase.buy` 와 같은 안전
        엔진에서 자동 적용된다(접수 거부 ``OrderRejectedError``·타임아웃 ``OrderTimeoutError``)."""
        return self._client._place_order(self._make_order(
            "buy", quantity=quantity, limit_price=limit_price, time_in_force=time_in_force,
            client_order_id=client_order_id, algo=algo, algo_window=algo_window))

    def sell(  # 해외는 algo 파라미터를 더 받는다(도메스틱 베이스보다 넓힌 오버라이드)
        self, *, quantity: Numeric, limit_price: Numeric | None = None,
        time_in_force: TimeInForce = "day", client_order_id: str | None = None,
        algo: AlgoStrategy | None = None, algo_window: tuple[str, str] | None = None,
    ) -> ExecutionReport:
        """이 해외 종목을 매도한다 -- 계약은 :meth:`buy` 와 동일(방향만 매도)."""
        return self._client._place_order(self._make_order(
            "sell", quantity=quantity, limit_price=limit_price, time_in_force=time_in_force,
            client_order_id=client_order_id, algo=algo, algo_window=algo_window))

    def _make_order(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None,
        time_in_force: TimeInForce, client_order_id: str | None,
        algo: AlgoStrategy | None = None, algo_window: tuple[str, str] | None = None,
    ) -> Order:
        if limit_price is None:
            raise KISUsageError("해외 주문은 지정가만 지원한다 -- limit_price 를 지정하라(시장가 미지원).")
        # 시간창은 (시작, 종료) 쌍으로만 받는다 -- 형식·범위(HHMMSS, 시작<종료)와 미국·정규세션 강제는
        # Order 가 생성 시점에 fail-closed 한다(algo 없이 window 만 주는 것도 거기서 거부).
        if algo_window is not None and algo is None:
            raise KISUsageError("algo_window 는 algo(twap/vwap) 없이는 줄 수 없다.")
        algo_start, algo_end = algo_window if algo_window is not None else ("", "")
        return Order.limit(self.symbol, side=side, quantity=quantity, limit_price=limit_price,
                           time_in_force=time_in_force, client_order_id=client_order_id,
                           exchange=self.exchange, algo_strategy=algo,
                           algo_start=algo_start, algo_end=algo_end)

    # --- 예약주문(미국/아시아 자동 라우팅) ---
    def reserve_buy(
        self, *, quantity: Numeric, limit_price: Numeric | None = None, end_date: str | None = None,
        client_order_id: str | None = None, currency: ReservedCurrency | None = None,
        algo: AlgoStrategy | None = None,
    ) -> ExecutionReport:
        """이 해외 종목의 **예약매수** -- 정규장 시작 전에 걸어두는 예약. **지정가만**(``limit_price``
        필수), ``end_date`` 미지원. 거래소의 시장이 와이어를 자동 라우팅한다: 미국(NAS/NYS/AMS)은
        매수/매도 분리 TR, 아시아(홍콩/상해/심천/일본/베트남)는 공용 TR(TTTS3013U). ``currency`` 는
        홍콩(HKS) 예약의 상품유형(HKD/CNY/USD) 선택 전용이고 **미지정(``None``)이면 홍콩은 HKD**다 --
        미국·기타 아시아 등 그 외 거래소에 ``currency`` 를 주면 :class:`~kis_trader.errors.KISUsageError`
        로 fail-closed(홍콩만 통화 선택이 있다). ``algo``(twap/vwap)는 미국 예약주문의 알고리즘 분할
        (정규장 종료 집행 고정, 시간창 없음)로, **미국 실전 전용**이라 아시아·모의투자면 fail-closed 한다.

        즉시 :meth:`buy` 와 같은 안전 규칙(이중발주 방지·재시도 금지·주문가능 계좌 가드)을 공유한다.
        반환 :class:`~kis_trader.report.ExecutionReport` 의 ``order_id`` 는 해외예약주문번호,
        ``receipt_date`` 는 아시아 접수일자(미국은 ``None``), ``status`` 는
        :attr:`~kis_trader.report.OrderStatus.PENDING_NEW`. 취소는 미국이 ``kis.account.overseas.
        cancel_reserved_order(예약번호)``, 아시아가 ``kis.orders.cancel(리포트.client_order_id)`` 다
        (아시아는 전용 취소 엔드포인트가 없어 안전코어가 원주문을 복원 재전송한다). 접수 거부는
        ``OrderRejectedError``, 타임아웃(접수 불명)은 ``OrderTimeoutError``(``kis.orders.reconcile``
        로 확인 -- 재조회는 실전전용)."""
        return self._reserve("buy", quantity=quantity, limit_price=limit_price,
                             end_date=end_date, client_order_id=client_order_id,
                             currency=currency, algo=algo)

    def reserve_sell(
        self, *, quantity: Numeric, limit_price: Numeric | None = None, end_date: str | None = None,
        client_order_id: str | None = None, currency: ReservedCurrency | None = None,
        algo: AlgoStrategy | None = None,
    ) -> ExecutionReport:
        """이 해외 종목의 **예약매도**. 계약·안전 규칙은 :meth:`reserve_buy` 와 같다(방향만 매도)."""
        return self._reserve("sell", quantity=quantity, limit_price=limit_price,
                             end_date=end_date, client_order_id=client_order_id,
                             currency=currency, algo=algo)

    def _reserve(  # 해외는 currency/algo 파라미터를 더 받는다(베이스보다 넓힌 오버라이드)
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None, end_date: str | None,
        client_order_id: str | None, currency: ReservedCurrency | None = None,
        algo: AlgoStrategy | None = None,
    ) -> ExecutionReport:
        if end_date is not None:      # 해외 예약: 지정가만, end_date 미지원(미국·아시아 공통)
            raise KISUsageError("해외 예약주문은 end_date 를 지원하지 않는다.")
        if limit_price is None:
            raise KISUsageError("해외 예약주문은 지정가만 지원한다 -- limit_price 를 지정하라.")
        return self._client._place_overseas_reserved_order(
            symbol=self.symbol, side=side, quantity=quantity, limit_price=limit_price,
            exchange=self.exchange, currency=currency, algo_strategy=algo,
            client_order_id=client_order_id,
        )
