"""``kis.account`` (해외선물옵션 08) 뷰 -- 해외선물옵션 계좌 조회.

위탁(01)이 시장별 뷰(``.domestic`` / ``.overseas``)를 갖는 것과 달리 해외선물옵션은 단일 도메인이라
조회 메서드를 뷰에 바로 둔다(국내선물옵션 03 뷰와 대칭). 계좌 식별정보·환경은 세션에서 온다.
**모든 조회는 실전 전용**(모의투자 미지원)이며, 금액·수량은 조회 통화의 Decimal(원화 아님)이다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._internal._datetime import _today_kst
from ..errors import KISUsageError
from ._engine.derivative_account import fetch_deposit
from .entities.derivative_account import OverseasDerivativeDeposit

if TYPE_CHECKING:
    from ..client import KISClient


class OverseasDerivativesAccount:
    """``kis.account`` (해외선물옵션 08) -- 해외선물옵션 계좌 조회 뷰."""

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def deposit(self, currency: str = "USD", date: str | None = None) -> OverseasDerivativeDeposit:
        """해외선물옵션 예수금현황(예수금·자산·증거금·손익 요약).

        ``currency`` 조회 통화(기본 USD), ``date`` 조회일자(YYYYMMDD, 8자리 숫자) -- 생략하면
        오늘. 금액은 그 통화의 Decimal(원화 아님). ``GET .../overseas-futureoption/v1/trading/
        inquire-deposit`` (``OTFM1411R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_deposit(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment,
            currency=currency, date=_resolve_query_date(date),
        )


def _resolve_query_date(date: str | None) -> str:
    """조회일자를 KIS 와이어 정본(YYYYMMDD)으로 -- ``None`` 이면 오늘, 주어지면 8자리 숫자 검증.
    잘못된 일자로 조회를 날리는 대신 호출 즉시 :class:`KISUsageError` 로 fail-closed 한다."""
    if date is None:
        return _today_kst()
    if len(date) != 8 or not date.isdigit():
        raise KISUsageError(f"date 는 8자리 숫자(YYYYMMDD)여야 한다: {date!r}")
    return date
