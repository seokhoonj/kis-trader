"""종목 핸들 공통 베이스 -- :class:`_StockBase`.

국내(:class:`~kis_trader.domestic.stock.DomesticStock`)와 해외
(:class:`~kis_trader.overseas.stock.OverseasStock`) 종목 핸들이 공유하는 주문 안전 흐름만 담는다.
시세는 자산군마다 표면이 달라 각 서브클래스가 자산군 엔진으로 구현한다 -- 그래서 이 베이스는
어떤 자산군 엔진도 import 하지 않는다(추상 베이스는 주문 배관만 안다).
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from .order import Order, Side, TimeInForce
from .report import ExecutionReport

if TYPE_CHECKING:
    from ._literals import Numeric
    from .client import KISClient


class _StockBase(abc.ABC):
    """종목 핸들 공통 베이스 -- 국내/해외가 공유하는 주문 안전 흐름(계좌 + 안전엔진). 시세는 각
    서브클래스가 자산군 엔진으로 구현한다. 추상 베이스라 직접 만들 수 없다(:class:`~kis_trader.domestic.
    stock.DomesticStock` / :class:`~kis_trader.overseas.stock.OverseasStock` 만 생성 가능)."""

    symbol: str

    def __init__(self, client: KISClient, symbol: str) -> None:
        self._client = client
        self.symbol = symbol

    # --- 주문 실행(안전 엔진 위임; 계좌 정보 필요) -- 와이어 조립기만 자산군별로 다르다 ---
    def buy(
        self, *, quantity: Numeric, limit_price: Numeric | None = None,
        time_in_force: TimeInForce = "day", client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 매수한다 -- ``limit_price`` 를 주면 지정가, 없으면 시장가(해외는 지정가만).

        이중체결 방지·타임아웃 재시도 금지가 안전 엔진에서 자동 적용된다. 계좌 정보가 없으면
        :class:`~kis_trader.errors.KISUsageError`, 조회전용(퇴직연금 등) 계좌면
        :class:`~kis_trader.errors.AccountNotOrderableError`. 접수 거부는 ``OrderRejectedError``,
        타임아웃(체결 불명)은 ``OrderTimeoutError`` -- 후자는 ``kis.orders.reconcile`` 로 확인한다.
        """
        return self._client._place_order(
            self._make_order("buy", quantity=quantity, limit_price=limit_price,
                             time_in_force=time_in_force, client_order_id=client_order_id)
        )

    def sell(
        self, *, quantity: Numeric, limit_price: Numeric | None = None,
        time_in_force: TimeInForce = "day", client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 매도한다 -- 계약은 :meth:`buy` 와 동일(방향만 매도)."""
        return self._client._place_order(
            self._make_order("sell", quantity=quantity, limit_price=limit_price,
                             time_in_force=time_in_force, client_order_id=client_order_id)
        )

    def reserve_buy(
        self, *, quantity: Numeric, limit_price: Numeric | None = None, end_date: str | None = None,
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목의 **예약매수** -- 다음 영업일(또는 ``end_date`` 까지) 아침 동시호가에 집행되도록 예약한다.

        즉시 :meth:`buy` 와 같은 안전 규칙(이중발주 방지·재시도 금지·주문가능 계좌 가드)을 공유하되
        라이프사이클이 다르다: 반환 :class:`~kis_trader.report.ExecutionReport` 의 ``order_id`` 는
        예약주문 식별자(정정·취소 시 지목), ``status`` 는 :attr:`~kis_trader.report.OrderStatus.PENDING_NEW`.
        **모의투자 미지원**. 국내 현금 예약이다(``limit_price`` 있으면 지정가·없으면 시장가, ``end_date``
        지원). 해외 종목 핸들은 이 메서드를 재정의해 미국·아시아 예약으로 라우팅한다(그쪽 문서 참조).
        잘못된 인자/계좌 미설정은
        ``KISUsageError``, 조회전용 계좌는 ``AccountNotOrderableError``, 접수 거부는 ``OrderRejectedError``,
        타임아웃(접수 불명)은 ``OrderTimeoutError``(``kis.orders.reconcile`` 로 확인)."""
        return self._reserve("buy", quantity=quantity, limit_price=limit_price,
                             end_date=end_date, client_order_id=client_order_id)

    def reserve_sell(
        self, *, quantity: Numeric, limit_price: Numeric | None = None, end_date: str | None = None,
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목의 **예약매도**. 계약·안전 규칙은 :meth:`reserve_buy` 와 같다(방향만 매도)."""
        return self._reserve("sell", quantity=quantity, limit_price=limit_price,
                             end_date=end_date, client_order_id=client_order_id)

    @abc.abstractmethod
    def _make_order(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None,
        time_in_force: TimeInForce, client_order_id: str | None,
    ) -> Order:
        """자산군별 즉시주문 와이어 조립(서브클래스 구현)."""

    @abc.abstractmethod
    def _reserve(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None, end_date: str | None,
        client_order_id: str | None,
    ) -> ExecutionReport:
        """자산군별 예약주문 발주(서브클래스 구현)."""
