"""``kis.account.domestic.bonds`` 뷰 -- 장내채권 계좌 조회.

위탁(01) 계좌를 주식과 함께 쓰되 조회는 ``domestic-bond`` 전용 엔드포인트로 한다. 계좌 식별정보·
환경은 세션에서 온다. 모든 조회는 **실전전용**(모의투자 미지원)이라 ``environment="paper"`` 면
와이어 이전에 :class:`~kis_trader.errors.KISUsageError` 로 fail-closed 한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._engine.bonds import (
    fetch_bond_balance,
    fetch_bond_buyable,
    fetch_bond_fills,
    fetch_bond_open_orders,
)

if TYPE_CHECKING:
    from .._literals import Numeric
    from ..client import KISClient
    from .entities.bond_account import (
        BondBuyable,
        BondFillHistory,
        BondOpenOrder,
        BondPosition,
    )


class DomesticBondAccount:
    """``kis.account.domestic.bonds`` -- 장내채권 계좌 조회(잔고/매수가능/미체결/체결). 모두 실전전용."""

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def balance(self) -> list[BondPosition]:
        """장내채권 보유 잔고(매수 lot 별 -- 같은 종목이 매수일·매수순번으로 여러 lot 일 수 있다).

        ``GET .../domestic-bond/v1/trading/inquire-balance`` (``CTSC8407R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_bond_balance(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment,
        )

    def buyable(self, code: str, *, price: Numeric | None = None) -> BondBuyable:
        """장내채권 매수가능조회(주문가능현금·대용, 재사용가능금액, 매수가능금액·수량, CMA평가금액).

        ``code`` 는 표준코드(ISIN), ``price`` 는 주문 단가(생략하면 시장가 기준). ``GET .../
        domestic-bond/v1/trading/inquire-psbl-order`` (``TTTC8910R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_bond_buyable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, code=code, price=price,
        )

    def open_orders(self, order_date: str) -> list[BondOpenOrder]:
        """장내채권 정정·취소 가능한 미체결 주문. ``order_date`` 는 주문일자(YYYYMMDD, 8자리 숫자).

        브로커 측 뷰라 ``client_order_id`` 는 없고 ``order_id`` 로 식별한다. ``GET .../
        domestic-bond/v1/trading/inquire-psbl-rvsecncl`` (``CTSC8035R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_bond_open_orders(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, order_date=order_date,
        )

    def fills(
        self, *, start: str, end: str, side: str = "all", symbol: str | None = None,
        unfilled_only: bool = False,
    ) -> BondFillHistory:
        """장내채권 일별 주문·체결 내역(개별 행 + 기간 합계). ``start``/``end`` 는 조회 기간의
        시작·종료일(YYYYMMDD, 8자리 숫자), ``side`` = ``"all"``/``"sell"``/``"buy"``, ``symbol``
        생략하면 전체 종목, ``unfilled_only`` 면 미체결만.

        ``GET .../domestic-bond/v1/trading/inquire-daily-ccld`` (``CTSC8013R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_bond_fills(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, start=start, end=end,
            side=side, symbol=symbol, unfilled_only=unfilled_only,
        )
