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

from typing import TYPE_CHECKING, ClassVar, Literal

from ..errors import KISUsageError
from ..order import _DERIVATIVE_EXCHANGE, Order, coerce_decimal, mint_client_order_id
from ._engine import derivative_account as derivative_account_api
from ._engine import derivatives as derivatives_api
from .entities.derivative import DerivativeQuote, ExpectedExecutionTrend, UnderlyingQuote

if TYPE_CHECKING:
    from datetime import date

    from .._literals import DerivativeMarket, Numeric
    from ..bar import Bar, Interval
    from ..client import KISClient
    from ..order import DerivativeDivision, OrderType, Right, Side, TimeInForce
    from ..order_book import OrderBook
    from ..report import ExecutionReport
    from .entities.derivative_account import DerivativeOrderable


class _ContractBase:
    """파생 계약 핸들 공통 베이스 -- 선물/옵션이 공유하는 조회(현재가·호가·예상체결·봉)와 세션·계약코드·
    시장구분 배선. 시장구분은 서브클래스가 :attr:`_MARKET` 로 고정한다(선물 F / 옵션 O). 계약종류별
    핸들(:class:`FuturesContract` / :class:`OptionContract`)로만 만든다."""

    #: 서브클래스가 고정하는 파생 시장(보드) 구분 코드. FID_COND_MRKT_DIV_CODE 로 나간다.
    _MARKET: DerivativeMarket

    #: 서브클래스가 고정하는 계약코드 길이(선물 6 / 옵션 9). 발주 전 형상검증에 쓴다 -- 조회용
    #: 종목코드나 오타를 발주 경로에서 조용히 통과시키지 않도록 :meth:`_make_order` 가 확인한다.
    _SYMBOL_LENGTH: ClassVar[int]

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

    # --- 발주(선물/옵션 공통; 계좌 + 안전 엔진 -- 종목 핸들 buy/sell 과 대칭) ---
    def buy(
        self, *, quantity: Numeric, limit_price: Numeric | None = None,
        order_type: OrderType | None = None, time_in_force: TimeInForce = "day",
        division: DerivativeDivision | None = None, night: bool = False,
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 계약을 매수한다 -- ``limit_price`` 를 주면 지정가, 없으면 시장가(``order_type`` 으로
        명시 가능). ``division`` 은 파생 주문구분(조건부지정가/최유리지정가; 최우선지정가는 파생에
        없다), ``night=True`` 면 KRX 파생 야간장(STTN, **모의투자 미지원**), ``time_in_force`` 는
        day/ioc/fok, ``client_order_id`` 는 멱등키(생략 시 자동 발행).

        이중체결 방지·타임아웃 재시도 금지가 안전 엔진에서 자동 적용된다. 계좌 미설정은
        :class:`~kis_trader.errors.KISUsageError`, 접수 거부는 ``OrderRejectedError``, 타임아웃(체결
        불명)은 ``OrderTimeoutError`` -- 후자는 ``kis.orders.reconcile`` 로 확인한다."""
        return self._client._place_order(self._make_order(
            "buy", quantity=quantity, limit_price=limit_price, order_type=order_type,
            time_in_force=time_in_force, division=division, night=night,
            client_order_id=client_order_id,
        ))

    def sell(
        self, *, quantity: Numeric, limit_price: Numeric | None = None,
        order_type: OrderType | None = None, time_in_force: TimeInForce = "day",
        division: DerivativeDivision | None = None, night: bool = False,
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 계약을 매도한다 -- 계약·``division``·``night`` 은 :meth:`buy` 와 동일(방향만 매도)."""
        return self._client._place_order(self._make_order(
            "sell", quantity=quantity, limit_price=limit_price, order_type=order_type,
            time_in_force=time_in_force, division=division, night=night,
            client_order_id=client_order_id,
        ))

    def orderable(self, side: Side, *, limit_price: Numeric | None = None) -> DerivativeOrderable:
        """이 계약의 주문가능수량(주간). ``side`` 는 매수/매도, ``limit_price`` 를 주면 지정가 기준,
        없으면 시장가 기준이다. 총가능·청산가능 수량과 기준지수를 함께 담는다.

        KIS URL/TR-ID: ``GET .../trading/inquire-psbl-order`` (실전 ``TTTO5105R`` / 모의 ``VTTO5105R``).
        계좌 미설정은 :class:`~kis_trader.errors.KISUsageError`."""
        cano, product_code = self._client._require_account()
        return derivative_account_api.fetch_derivative_orderable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, code=self.code, side=side, limit_price=limit_price,
        )

    def night_orderable(
        self, side: Side, *, limit_price: Numeric | None = None
    ) -> DerivativeOrderable:
        """이 계약의 야간장(EUREX 연계) 주문가능수량. 파라미터는 :meth:`orderable` 과 같되 야간
        세션 기준이며 **실전 전용**(``environment="paper"`` 는 :class:`~kis_trader.errors.KISUsageError`).

        KIS URL/TR-ID: ``GET .../trading/inquire-psbl-ngt-order`` (``STTN5105R``, 모의투자 미지원).
        계좌 미설정도 :class:`~kis_trader.errors.KISUsageError`."""
        cano, product_code = self._client._require_account()
        return derivative_account_api.fetch_derivative_night_orderable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, code=self.code, side=side, limit_price=limit_price,
        )

    def _make_order(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None = None,
        order_type: OrderType | None = None, time_in_force: TimeInForce = "day",
        division: DerivativeDivision | None = None, night: bool = False,
        client_order_id: str | None = None,
    ) -> Order:
        """파생(XKFE) 발주 :class:`Order` 를 조립한다 -- 계약코드 길이 형상검증 후 세션(주간/야간)·
        상품구분(선물 01 / 콜 02 / 풋 03)을 실어 준다. ``order_type`` 미지정 시 ``limit_price`` 유무로
        시장가/지정가를 정한다."""
        if len(self.code) != self._SYMBOL_LENGTH:
            raise KISUsageError(
                f"{type(self).__name__} 계약코드 길이는 {self._SYMBOL_LENGTH} 여야 한다: {self.code!r}"
            )
        resolved_type: OrderType = order_type or ("market" if limit_price is None else "limit")
        return Order(
            symbol=self.code, side=side, order_type=resolved_type,
            quantity=coerce_decimal(quantity, "quantity"),
            limit_price=None if limit_price is None else coerce_decimal(limit_price, "limit_price"),
            time_in_force=time_in_force, exchange=_DERIVATIVE_EXCHANGE,
            session="night" if night else "regular", division=division,
            derivative_item=self._derivative_item(),
            client_order_id=client_order_id or mint_client_order_id(),
        )

    def _derivative_item(self) -> Literal["01", "02", "03"]:
        """이 계약의 파생 상품구분 코드(FUOP_ITEM_DVSN_CD). 서브클래스가 구현한다."""
        raise NotImplementedError


class FuturesContract(_ContractBase):
    """지수선물 계약 조회 핸들 -- ``kis.domestic.futures(code)``. 시장구분 F.

    공통 조회(:meth:`quote`·:meth:`order_book`·:meth:`expected_execution_trend`·:meth:`bars`)에 더해
    선물 전용 :meth:`underlying_quote`(선물과 기초자산을 나란히 보는 베이시스 스냅샷)를 가진다.
    발주(:meth:`buy`/:meth:`sell`)는 상품구분 "01"(선물)로 나간다."""

    _MARKET: DerivativeMarket = "F"
    _SYMBOL_LENGTH: ClassVar[int] = 6

    def underlying_quote(self) -> UnderlyingQuote:
        """선물과 그 기초자산(지수)을 나란히 담는 스냅샷(베이시스 판단용). 선물 최근월물 계약에서 쓴다."""
        return derivatives_api.fetch_underlying_quote(
            self._client.transport, code=self.code, market="F"
        )

    def _derivative_item(self) -> Literal["01", "02", "03"]:
        return "01"


class OptionContract(_ContractBase):
    """지수옵션 계약 조회 핸들 -- ``kis.domestic.option(code, right=...)``. 시장구분 O.

    공통 조회(:meth:`quote`·:meth:`order_book`·:meth:`expected_execution_trend`·:meth:`bars`)에 더해
    발주(:meth:`buy`/:meth:`sell`)를 가진다. 기초자산 나란히 보기(``underlying_quote``)는 선물 전용이라
    옵션 핸들엔 아예 없다(호출 시 구조적 ``AttributeError``, 타입체커가 먼저 잡는다).

    ``right``(call/put)은 발주의 상품구분("02" 콜 / "03" 풋)을 정한다. 조회는 계약코드만으로 충분해
    ``right`` 없이도 만들 수 있고(``kis.domestic.option(code)``), 그 상태로 발주하면 fail-closed 다 --
    발주엔 ``option(code, right=...)`` 로 방향을 지정해야 한다."""

    _MARKET: DerivativeMarket = "O"
    _SYMBOL_LENGTH: ClassVar[int] = 9

    right: Right | None

    def __init__(self, client: KISClient, code: str, right: Right | None = None) -> None:
        super().__init__(client, code)
        self.right = right

    def _derivative_item(self) -> Literal["01", "02", "03"]:
        if self.right is None:
            raise KISUsageError(
                "옵션 발주에는 right(call/put)가 필요하다 -- kis.domestic.option(code, right=...) 로 지정하라."
            )
        return "02" if self.right == "call" else "03"      # 콜 02 / 풋 03
