"""자산 무관 네임스페이스 -- ``kis.orders`` (:class:`OrdersNamespace`) 와 ``kis.pension``
(:class:`PensionNamespace`).

주문 lifecycle(``client_order_id`` 로 동작, 자산 무관)과 퇴직연금 계좌 행위를 담는다. 자산군별
시세/계좌 표면은 각 자산군 패키지의 네임스페이스(:class:`~kis_trader.domestic.namespace.
DomesticNamespace` / :class:`~kis_trader.overseas.namespace.OverseasNamespace`)가 담당한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .domestic._engine import pension as pension_api

if TYPE_CHECKING:
    from .client import KISClient
    from .pension_items import (
        PensionBalance,
        PensionBuyableAmount,
        PensionDeposit,
        PensionOrder,
        PensionPresentBalance,
    )
    from .report import ExecutionReport


class OrdersNamespace:
    """``kis.orders`` -- client_order_id 로 동작하는 주문 lifecycle(자산 무관). 안전 dedup/reconcile 코어."""

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def reconcile(self, client_order_id: str) -> ExecutionReport | None:
        """접수 여부가 불확실한 주문을 KIS 서버에 실제로 조회해 상태를 확정한다(불확실하면 미확정 유지)."""
        return self._c._reconcile(client_order_id)

    def cancel(
        self, client_order_id: str, *, quantity: object | None = None, request_id: str | None = None
    ) -> ExecutionReport:
        """접수된 주문을 취소한다(부분 취소는 ``quantity``)."""
        return self._c._change_order(
            client_order_id, action="cancel", quantity=quantity, limit_price=None, request_id=request_id
        )

    def modify(
        self, client_order_id: str, *, limit_price: object, quantity: object | None = None,
        request_id: str | None = None,
    ) -> ExecutionReport:
        """접수된 주문의 가격(또는 수량)을 정정한다.

        정정이 성공하면 KIS 가 원주문에 새 거래소 주문번호(ODNO)를 부여하므로, 이 정정된 주문을
        같은 ``client_order_id`` 가 계속 가리키도록 재바인딩한다 -- 이후 ``cancel``/``modify`` 는
        정정된 주문을 지목하고, ``report_for(client_order_id)`` 의 ``order_id`` 는 새 ODNO,
        상태는 ``PENDING_REPLACE`` 가 된다(place 시점 ODNO 를 캐시했다면 갱신 필요).

        **주의**: 정정 후 ``report_for(client_order_id).filled_quantity`` 는 **0 으로 리셋된다**
        -- 새 ODNO 는 정정 수량만큼의 신규 대기주문이라서다(이 값이 재바인딩된 지문 수량과 짝을
        이뤄 이후 잔량 계산이 맞는다). 원주문의 누적 체결량을 이 id 로만 읽으면 과소 집계되니,
        정정 이전 체결은 정정이 반환한 리포트/기존 실행에서 확인하라."""
        return self._c._change_order(
            client_order_id, action="modify", quantity=quantity, limit_price=limit_price,
            request_id=request_id,
        )


class PensionNamespace:
    """``kis.pension`` -- 퇴직연금 계좌(예수금/매수가능/잔고/체결). 전부 실전전용.

    모든 메서드는 계좌 미설정 시, 그리고 ``environment="paper"`` 에서(전부 모의 미지원)
    :class:`~kis_trader.errors.KISUsageError` 를 던진다. 조회 실패·응답 부재·파싱 실패는
    :class:`~kis_trader.errors.KISError`. 계좌 상품코드가 퇴직연금이어야 정상 응답한다.
    """

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def deposit(self) -> PensionDeposit:
        """예수금 총액·익일/2익일 정산·결제금액. 일반 위탁계좌 예수금과 별개인 퇴직연금 전용 조회다.
        계좌 상품코드가 퇴직연금이어야 정상 응답한다. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return pension_api.fetch_deposit(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def buyable(self, symbol: str, *, limit_price: object | None = None) -> PensionBuyableAmount:
        """매수가능 여력 -- 주문가능현금·재사용가능금액·최대 매수금액/수량. ``limit_price`` 없으면 시장가 기준.
        **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return pension_api.fetch_buyable_amount(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            symbol=symbol, limit_price=limit_price,
        )

    def balance(self) -> PensionBalance:
        """잔고 -- 보유종목과 예수금 기준 계좌 요약을 한 조회로. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return pension_api.fetch_balance(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def present_balance(self) -> PensionPresentBalance:
        """체결기준잔고 -- 체결기준 보유종목과 손익 요약. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return pension_api.fetch_present_balance(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def orders(self, *, only_unfilled: bool = False) -> list[PensionOrder]:
        """당일 주문 내역(체결/미체결). ``only_unfilled=True`` 면 미체결만. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return pension_api.fetch_orders(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            only_unfilled=only_unfilled,
        )
