"""``kis.account`` (국내선물옵션 03) 뷰 -- 선물옵션 계좌 조회.

위탁(01)이 시장별 뷰(``.domestic`` / ``.overseas``)를 갖는 것과 달리 선물옵션은 단일 도메인이라
조회 메서드를 뷰에 바로 둔다. 계좌 식별정보·환경은 세션에서 온다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._engine.derivative_account import fetch_balance, fetch_deposit
from .entities.derivative_account import DerivativeBalance, DerivativeDeposit

if TYPE_CHECKING:
    from ..client import KISClient


class DomesticDerivativesAccount:
    """``kis.account`` (국내선물옵션 03) -- 선물옵션 계좌 조회 뷰."""

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def balance(self) -> DerivativeBalance:
        """선물옵션 잔고(보유내역 + 예수금·증거금·손익 요약).

        ``GET .../domestic-futureoption/v1/trading/inquire-balance``
        (실전 ``CTFO6118R`` / 모의 ``VTFO6118R``). 모의투자 지원.
        """
        cano, product_code = self._client._require_account()
        return fetch_balance(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment,
        )

    def deposit(self) -> DerivativeDeposit:
        """선물옵션 총자산현황(예수금·주문가능·위탁증거금·손익 요약).

        ``GET .../domestic-futureoption/v1/trading/inquire-deposit`` (``CTRP6550R``).
        **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_deposit(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment,
        )
