"""퇴직연금 네임스페이스 -- ``kis.pension`` (:class:`PensionNamespace`).

세션 :class:`~kis_trader.client.KISClient` 아래 퇴직연금 계좌 행위(예수금/매수가능/잔고/체결)를
모은다. 전부 실전전용이며, 각 메서드는 세션이 쥔 전송/계좌/환경으로 퇴직연금 엔진을 직접 호출한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import _engine as pension_api

if TYPE_CHECKING:
    from ..client import KISClient
    from .entities import (
        PensionBalance,
        PensionBuyableAmount,
        PensionDeposit,
        PensionOrder,
        PensionPresentBalance,
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
