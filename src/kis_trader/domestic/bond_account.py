"""``kis.account.domestic.bonds`` 뷰 -- 장내채권 계좌 조회.

위탁(01) 계좌를 주식과 함께 쓰되 조회는 ``domestic-bond`` 전용 엔드포인트로 한다. 계좌 식별정보·
환경은 세션에서 온다. 모든 조회는 **실전전용**(모의투자 미지원)이라 ``environment="paper"`` 면
와이어 이전에 :class:`~kis_trader.errors.KISUsageError` 로 fail-closed 한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._engine.bonds import fetch_bond_balance

if TYPE_CHECKING:
    from ..client import KISClient
    from .entities.bond_account import BondPosition


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
