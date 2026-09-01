"""``kis.account`` (국내선물옵션 03) 뷰 -- 선물옵션 계좌 조회.

위탁(01)이 시장별 뷰(``.domestic`` / ``.overseas``)를 갖는 것과 달리 선물옵션은 단일 도메인이라
조회 메서드를 뷰에 바로 둔다. 계좌 식별정보·환경은 세션에서 온다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from ._engine.derivative_account import (
    fetch_balance,
    fetch_base_date_fills,
    fetch_commissions,
    fetch_deposit,
    fetch_night_balance,
    fetch_night_margin,
    fetch_open_orders,
    fetch_settlement_pl,
    fetch_valuation_pl,
)
from .entities.derivative_account import (
    DerivativeBalance,
    DerivativeCommissionHistory,
    DerivativeDeposit,
    DerivativeFillHistory,
    DerivativeNightBalance,
    DerivativeNightMargin,
    DerivativeOpenOrder,
    DerivativeSettlementBalance,
    DerivativeValuationBalance,
)

if TYPE_CHECKING:
    from ..client import KISClient

_KST = timezone(timedelta(hours=9))


class DomesticDerivativesAccount:
    """``kis.account`` (국내선물옵션 03) -- 선물옵션 계좌 조회 뷰."""

    #: ``kis.account`` 판별자(isinstance 대신 ``account.kind`` 로 분기).
    kind: Literal["domestic_derivatives"] = "domestic_derivatives"

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

    def open_orders(
        self, *, order_date: str | None = None, side: str = "all", symbol: str | None = None,
    ) -> list[DerivativeOpenOrder]:
        """선물옵션 미체결(정정·취소 가능) 주문. ``order_date`` (YYYYMMDD, 생략하면 오늘 KST),
        ``side`` = ``"all"``/``"buy"``/``"sell"``, ``symbol`` 생략하면 전체 종목.

        브로커 측 미체결 목록이라 세션이 발주한 ``client_order_id`` 는 없고 거래소 주문번호(odno)만
        온다 -- 재시작 등으로 세션 dedup store 를 잃었을 때 서버측 미체결을 확인하는 용도다.
        ``GET .../domestic-futureoption/v1/trading/inquire-ccnl`` (주간, 실전 ``TTTO5201R`` / 모의
        ``VTTO5201R``). 모의투자 지원.
        """
        cano, product_code = self._client._require_account()
        return fetch_open_orders(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment,
            order_date=order_date or datetime.now(_KST).strftime("%Y%m%d"),
            side=side, symbol=symbol,
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

    def settlement_pl(self, *, base_date: str) -> DerivativeSettlementBalance:
        """선물옵션 잔고정산손익내역(정산 보유내역 + 예수금·증거금·수수료 요약).

        ``base_date`` 는 조회 기준일자(YYYYMMDD, 8자리 숫자). ``GET .../domestic-futureoption/v1/
        trading/inquire-balance-settlement-pl`` (``CTFO6117R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_settlement_pl(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, base_date=base_date,
        )

    def base_date_fills(
        self, *, order_date: str, start_time: str = "000000", end_time: str = "240000"
    ) -> DerivativeFillHistory:
        """선물옵션 기준일체결내역(체결내역 + 기간 합계 요약).

        ``order_date`` 는 주문일자(YYYYMMDD, 8자리 숫자), ``start_time``/``end_time`` 은 조회 시각
        구간(HHMMSS, 기본 하루 전체). ``GET .../domestic-futureoption/v1/trading/
        inquire-ccnl-bstime`` (``CTFO5139R``). **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_base_date_fills(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment,
            order_date=order_date, start_time=start_time, end_time=end_time,
        )

    def commissions(self, *, start: str, end: str) -> DerivativeCommissionHistory:
        """선물옵션 기간약정수수료일별(일별 내역 + 기간 합계 요약).

        ``start``/``end`` 는 조회 기간의 시작·종료일(YYYYMMDD, 8자리 숫자). ``GET .../
        domestic-futureoption/v1/trading/inquire-daily-amount-fee`` (``CTFO6119R``).
        **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_commissions(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, start=start, end=end,
        )

    def night_balance(self) -> DerivativeNightBalance:
        """(야간)선물옵션 잔고현황(보유내역 + 예수금·증거금·손익 요약 + 야간 전용 유지증거금).

        ``GET .../domestic-futureoption/v1/trading/inquire-ngt-balance`` (``CTFN6118R``).
        계좌비밀번호(ACNT_PWD)가 필요해 세션에서 읽어 전달한다 -- 비밀번호가 없으면
        :class:`KISUsageError`, 값 자체는 절대 노출하지 않는다. **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        account_password = self._client._require_account_password()
        return fetch_night_balance(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, account_password=account_password,
        )

    def night_margin(self, margin_division: str = "01") -> DerivativeNightMargin:
        """(야간)선물옵션 증거금상세(개시/유지 증거금 + 예수금 요약).

        ``margin_division`` 은 증거금구분코드(MGNA_DVSN_CD, 기본 "01" 개시). ``GET .../
        domestic-futureoption/v1/trading/ngt-margin-detail`` (``CTFN7107R``).
        **실전전용**(모의투자 미지원).
        """
        cano, product_code = self._client._require_account()
        return fetch_night_margin(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, margin_division=margin_division,
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
