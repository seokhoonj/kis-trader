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
    fetch_daily_fills,
    fetch_daily_orders,
    fetch_deposit,
    fetch_margin_detail,
    fetch_orderable,
    fetch_period_pnl,
    fetch_positions,
    fetch_today_orders,
    fetch_transactions,
)
from .entities.derivative_account import (
    OverseasDerivativeDailyOrder,
    OverseasDerivativeDeposit,
    OverseasDerivativeFillHistory,
    OverseasDerivativeMargin,
    OverseasDerivativeOrder,
    OverseasDerivativeOrderable,
    OverseasDerivativePNLHistory,
    OverseasDerivativePosition,
    OverseasDerivativeTransaction,
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
            currency=currency, query_date=_resolve_query_date(date),
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
            currency=currency, query_date=_resolve_query_date(date),
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

    def daily_fills(self, start: str, end: str) -> OverseasDerivativeFillHistory:
        """해외선물옵션 일별 체결내역(기간 체결 목록 + 합계 요약).

        ``start``~``end`` (YYYYMMDD, 8자리 숫자) 기간을 전체 통화·전체 매매로 조회한다. 금액·수량은
        각 체결 통화의 Decimal(원화 아님). ``GET .../overseas-futureoption/v1/trading/
        inquire-daily-ccld`` (``OTFM3122R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_daily_fills(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, start=start, end=end,
        )

    def daily_orders(self, start: str, end: str) -> list[OverseasDerivativeDailyOrder]:
        """해외선물옵션 일별 주문내역(기간 주문 목록).

        ``start``~``end`` (YYYYMMDD, 8자리 숫자) 기간을 전체 매매·전체 체결미체결로 조회한다.
        금액·수량은 각 계약 통화의 Decimal(원화 아님). ``GET .../overseas-futureoption/v1/
        trading/inquire-daily-order`` (``OTFM3120R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_daily_orders(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, start=start, end=end,
        )

    def period_pnl(self, start: str, end: str) -> OverseasDerivativePNLHistory:
        """해외선물옵션 기간 손익(통화별 집계 + 종목별 집계).

        ``start``~``end`` (YYYYMMDD, 8자리 숫자) 기간을 전체 통화로 조회한다. 통화별 손익은
        ``by_currency``, 종목별 손익은 ``by_symbol`` 에 담긴다. 금액·수량은 각 행 통화의
        Decimal(원화 아님). ``GET .../overseas-futureoption/v1/trading/inquire-period-ccld``
        (``OTFM3118R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_period_pnl(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, start=start, end=end,
        )

    def transactions(self, start: str, end: str) -> list[OverseasDerivativeTransaction]:
        """해외선물옵션 기간 입출금내역(원장 목록).

        ``start``~``end`` (YYYYMMDD, 8자리 숫자) 기간을 전체 거래유형·전체 통화로 조회한다.
        계좌 비밀번호는 필요 없다. 금액은 각 행 통화의 Decimal(원화 아님). ``GET .../overseas-
        futureoption/v1/trading/inquire-period-trans`` (``OTFM3114R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_transactions(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, start=start, end=end,
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
