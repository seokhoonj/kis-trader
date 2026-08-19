"""``kis.account`` (해외선물옵션 08) 뷰 -- 해외선물옵션 계좌 조회.

위탁(01)이 시장별 뷰(``.domestic`` / ``.overseas``)를 갖는 것과 달리 해외선물옵션은 단일 도메인이라
조회 메서드를 뷰에 바로 둔다(국내선물옵션 03 뷰와 대칭). 계좌 식별정보·환경은 세션에서 온다.
**모든 조회는 실전 전용**(모의투자 미지원)이며, 금액·수량은 조회 통화의 Decimal(원화 아님)이다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._internal._datetime import _today_kst
from ..errors import KISUsageError
from ._engine.derivative_account import (
    fetch_deposit,
    fetch_margin_detail,
    fetch_orderable,
    fetch_positions,
    fetch_today_orders,
)
from .entities.derivative_account import (
    OverseasDerivativeDeposit,
    OverseasDerivativeMargin,
    OverseasDerivativeOrder,
    OverseasDerivativeOrderable,
    OverseasDerivativePosition,
)

if TYPE_CHECKING:
    from .._literals import Numeric
    from ..client import KISClient
    from ..order import Side


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

    def margin_detail(
        self, currency: str = "USD", date: str | None = None
    ) -> OverseasDerivativeMargin:
        """해외선물옵션 증거금상세(주문가능·위탁/정산/미결제/유지/주문/추가 증거금 요약).

        ``currency`` 조회 통화(기본 USD), ``date`` 조회일자(YYYYMMDD, 8자리 숫자) -- 생략하면
        오늘. 금액은 그 통화의 Decimal(원화 아님). SPAN/EUREX 등 상세 증거금 내역은 ``_raw``.
        ``GET .../overseas-futureoption/v1/trading/margin-detail`` (``OTFM3115R``).
        **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_margin_detail(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment,
            currency=currency, date=_resolve_query_date(date),
        )

    def positions(self, fuop: str = "00") -> list[OverseasDerivativePosition]:
        """해외선물옵션 미결제내역(보유 종목 전체).

        ``fuop`` 선물옵션구분(FUOP_DVSN, 기본 "00" 전체). 금액·수량은 각 종목 통화의 Decimal
        (원화 아님). ``GET .../overseas-futureoption/v1/trading/inquire-unpd`` (``OTFM1412R``).
        **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_positions(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, fuop=fuop,
        )

    def today_orders(self) -> list[OverseasDerivativeOrder]:
        """해외선물옵션 당일 주문내역(체결+미체결 전체).

        체결여부·매매·선물옵션 구분 필터 없이 당일 전체 주문을 돌려준다. 금액·수량은 각 계약
        통화의 Decimal(원화 아님). ``GET .../overseas-futureoption/v1/trading/inquire-ccld``
        (``OTFM3116R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_today_orders(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment,
        )

    def orderable(
        self, symbol: str, side: Side, *, price: Numeric | None = None,
        exercise_reserved: bool = False,
    ) -> OverseasDerivativeOrderable:
        """해외선물옵션 계약의 주문가능수량(신규/총/시장가 등).

        ``symbol`` 해외선물FX상품번호, ``side`` 매수/매도, ``price`` 있으면 그 단가 기준·없으면
        시장가, ``exercise_reserved`` 행사예약주문 여부. 수량은 그 계약 통화 기준. ``GET .../
        overseas-futureoption/v1/trading/inquire-psamount`` (``OTFM3304R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_orderable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment,
            symbol=symbol, side=side, price=price, exercise_reserved=exercise_reserved,
        )


def _resolve_query_date(date: str | None) -> str:
    """조회일자를 KIS 와이어 정본(YYYYMMDD)으로 -- ``None`` 이면 오늘, 주어지면 8자리 숫자 검증.
    잘못된 일자로 조회를 날리는 대신 호출 즉시 :class:`KISUsageError` 로 fail-closed 한다."""
    if date is None:
        return _today_kst()
    if len(date) != 8 or not date.isdigit():
        raise KISUsageError(f"date 는 8자리 숫자(YYYYMMDD)여야 한다: {date!r}")
    return date
