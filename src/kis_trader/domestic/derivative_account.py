"""``kis.account`` (국내선물옵션 03) 뷰 -- 선물옵션 계좌 조회.

위탁(01)이 시장별 뷰(``.domestic`` / ``.overseas``)를 갖는 것과 달리 선물옵션은 단일 도메인이라
조회 메서드를 뷰에 바로 둔다. 계좌 식별정보·환경은 세션에서 온다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._engine.derivative_account import (
    fetch_balance,
    fetch_deposit,
    fetch_settlement_pl,
    fetch_valuation_pl,
)
from .entities.derivative_account import (
    DerivativeBalance,
    DerivativeDeposit,
    DerivativeSettlementBalance,
    DerivativeValuationBalance,
)

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

    def valuation_pl(self) -> DerivativeValuationBalance:
        """선물옵션 잔고평가손익내역(보유내역 + 예수금·증거금·손익 요약).

        ``GET .../domestic-futureoption/v1/trading/inquire-balance-valuation-pl``
        (``CTFO6159R``). 보유내역은 종목별 평가/매매 손익을 함께 싣는다. **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_valuation_pl(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment,
        )

    def settlement_pl(self, date: str) -> DerivativeSettlementBalance:
        """선물옵션 잔고정산손익내역(정산 보유내역 + 예수금·증거금·수수료 요약).

        ``date`` 는 조회 기준일자(YYYYMMDD, 8자리 숫자). ``GET .../domestic-futureoption/v1/
        trading/inquire-balance-settlement-pl`` (``CTFO6117R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_settlement_pl(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, date=date,
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
