"""세션 계좌 파사드의 뷰 -- ``kis.account`` 가 상품코드에 따라 반환한다.

위탁(01)은 국내주식과 해외주식을 한 계좌에서 다루므로(멀티 도메인) 시장별 뷰
``.domestic`` / ``.overseas`` 를 갖는다. 각 뷰는 기존 계좌 조회 클래스를 그대로 재사용한다
(경로만 이동, 동작 불변). 단일 도메인 계좌(파생 등)는 별도 뷰로 Plan B 이후 추가된다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .domestic.namespace import DomesticAccount
from .overseas.namespace import OverseasAccount

if TYPE_CHECKING:
    from .client import KISClient


class StockAccounts:
    """``kis.account`` (위탁 01) -- 국내/해외 주식 계좌의 시장별 뷰."""

    def __init__(self, client: KISClient) -> None:
        self._domestic = DomesticAccount(client)
        self._overseas = OverseasAccount(client)

    @property
    def domestic(self) -> DomesticAccount:
        """국내주식 계좌 조회(잔고/손익/예약주문)."""
        return self._domestic

    @property
    def overseas(self) -> OverseasAccount:
        """해외주식 계좌 조회(잔고/미체결/기간손익 등)."""
        return self._overseas
