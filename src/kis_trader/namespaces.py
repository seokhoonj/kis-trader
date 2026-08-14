"""자산군 최상위 네임스페이스 -- ``kis.domestic`` / ``kis.overseas`` / ``kis.pension`` / ``kis.orders``.

세션 :class:`~kis_trader.client.KISClient` 아래 자산군별 행위를 노출한다. 국내는 시세·계좌·순위·
시장·일정을, 해외는 시세·계좌·순위·뉴스 등을 각 네임스페이스로 모은다. 계좌는 다시 ``.account``
하위로, 주문 lifecycle(client_order_id 로 동작, 자산 무관)은 ``kis.orders`` 로 둔다.

각 메서드는 세션이 쥔 전송/계좌/환경으로 엔드포인트 엔진을 직접 호출한다. 세션(:class:`KISClient`)은
전송·계좌·주문 안전코어(store/risk/place)만 쥐고, 공개 행위 표면은 이 네임스페이스들이 담당한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .domestic._engine import account as account_api
from .domestic._engine import derivatives as derivatives_api
from .domestic._engine import market_data as market_data_api
from .domestic._engine import pension as pension_api
from .domestic._engine import product as product_api
from .domestic._engine import reserved_orders as reserved_orders_api
from .domestic._engine import saved_screen as saved_screen_api
from ._overseas import account as overseas_account
from ._overseas import derivatives as overseas_derivatives_api
from ._overseas import market_data as overseas_market_data_api
from ._overseas import reference as overseas_reference_api
from ._overseas import reserved_orders as overseas_reserved_orders_api
from .bond import Bond
from .calendar import CalendarQueries
from .derivative import FuturesContract, OptionContract
from .domestic.stock import DomesticStock
from .elw import ELW
from .elw_ranking import ELWRankingQueries
from .elw_screener import ELWScreenerQueries
from .errors import KISUsageError
from .index import Index
from .market import MarketQueries
from .overseas.stock import OverseasStock
from .overseas_derivative import OverseasDerivative
from .overseas_index import OverseasIndex
from .overseas_ranking import OverseasRankingQueries
from .ranking import RankingQueries

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from .account_reports import IntegratedMargin, RealizedProfitBalance
    from .account_right import AccountRight
    from .balance import AccountAssets, Balance, Portfolio, Position
    from .client import KISClient
    from .derivative_items import FuturesBoardQuote, OptionBoard, OptionExpiry
    from .instrument import DomesticBoard
    from .news import NewsHeadline
    from .open_order import OpenOrder
    from .order import Side
    from .overseas_derivative_items import (
        OverseasDerivativeDetail,
        OverseasDerivativeMarketHours,
        OverseasFuturesOpenInterest,
    )
    from .overseas_items import (
        OverseasAlgoExecution,
        OverseasAlgoOrder,
        OverseasBalance,
        OverseasBuyableAmount,
        OverseasCollateralStockSearch,
        OverseasCorporateAction,
        OverseasForeignMargin,
        OverseasIndustry,
        OverseasIndustryStock,
        OverseasNewsHeadline,
        OverseasOpenOrder,
        OverseasPeriodProfit,
        OverseasPosition,
        OverseasPresentBalance,
        OverseasReservedOrder,
        OverseasRight,
        OverseasSettlementBalance,
        OverseasSettlementDate,
        OverseasStockSearch,
        OverseasTransaction,
    )
    from .overseas_product import OverseasProductInfo
    from .pension_items import (
        PensionBalance,
        PensionBuyableAmount,
        PensionDeposit,
        PensionOrder,
        PensionPresentBalance,
    )
    from .product import ProductInfo
    from .quote import Quote
    from .report import ExecutionReport
    from .reserved_order import ReservedOrder
    from .saved_screen import SavedScreen, SavedScreenStock, Watchlist, WatchlistGroup
    from .trade_profit import DailyProfitHistory, TradeProfitHistory

# 해외 지수류 kind -> FID_COND_MRKT_DIV_CODE. ``kis.overseas.index`` 가 쓴다.
_OVERSEAS_INDEX_KIND = {"index": "N", "fx": "X", "bond": "I", "gold": "S"}


class OrdersNamespace:
    """``kis.orders`` -- client_order_id 로 동작하는 주문 lifecycle(자산 무관). 안전 dedup/reconcile 코어."""

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def reconcile(self, client_order_id: str) -> ExecutionReport | None:
        """접수 여부가 불확실한 주문을 KIS 서버에 실제로 조회해 상태를 확정한다(불확실하면 미확정 유지)."""
        return self._c._reconcile(client_order_id)

    def cancel(
        self, client_order_id: str, *, quantity: object | None = None, request_id: str | None = None
    ) -> ExecutionReport:
        """접수된 주문을 취소한다(부분 취소는 ``quantity``)."""
        return self._c._change_order(
            client_order_id, action="cancel", quantity=quantity, limit_price=None, request_id=request_id
        )

    def modify(
        self, client_order_id: str, *, limit_price: object, quantity: object | None = None,
        request_id: str | None = None,
    ) -> ExecutionReport:
        """접수된 주문의 가격(또는 수량)을 정정한다.

        정정이 성공하면 KIS 가 원주문에 새 거래소 주문번호(ODNO)를 부여하므로, 이 정정된 주문을
        같은 ``client_order_id`` 가 계속 가리키도록 재바인딩한다 -- 이후 ``cancel``/``modify`` 는
        정정된 주문을 지목하고, ``report_for(client_order_id)`` 의 ``order_id`` 는 새 ODNO,
        상태는 ``PENDING_REPLACE`` 가 된다(place 시점 ODNO 를 캐시했다면 갱신 필요).

        **주의**: 정정 후 ``report_for(client_order_id).filled_quantity`` 는 **0 으로 리셋된다**
        -- 새 ODNO 는 정정 수량만큼의 신규 대기주문이라서다(이 값이 재바인딩된 지문 수량과 짝을
        이뤄 이후 잔량 계산이 맞는다). 원주문의 누적 체결량을 이 id 로만 읽으면 과소 집계되니,
        정정 이전 체결은 정정이 반환한 리포트/기존 실행에서 확인하라."""
        return self._c._change_order(
            client_order_id, action="modify", quantity=quantity, limit_price=limit_price,
            request_id=request_id,
        )


class DomesticAccount:
    """``kis.domestic.account`` -- 국내 계좌 조회·계좌 단위 주문(잔고/손익/예약주문).

    모든 메서드는 계좌 미설정 시 :class:`~kis_trader.errors.KISUsageError` 를 던진다(세션을
    ``KISClient(..., account=...)`` 로 열어야 한다). ``**모의투자 미지원**`` 이라 표시된 메서드는
    ``environment="paper"`` 에서도 :class:`~kis_trader.errors.KISUsageError` 다. 조회 실패·응답
    부재·파싱 실패는 :class:`~kis_trader.errors.KISError`.
    """

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def balance(self) -> Balance:
        """계좌 현금·자산 요약."""
        cano, product_code = self._c._require_account()
        return account_api.fetch_balance(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def positions(self) -> list[Position]:
        """보유 종목(0수량 잔여 lot 포함)."""
        cano, product_code = self._c._require_account()
        return account_api.fetch_positions(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def portfolio(self) -> Portfolio:
        """보유 종목 + 요약을 한 스냅샷으로."""
        cano, product_code = self._c._require_account()
        return account_api.fetch_portfolio(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def assets(self) -> AccountAssets:
        """투자계좌 자산현황(총자산·순자산·예수금·대출·외화). 자산군별 내역은 위치기반이라 ``_raw`` 로만 둔다."""
        cano, product_code = self._c._require_account()
        return account_api.fetch_account_assets(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def realized_profit_balance(self) -> RealizedProfitBalance:
        """실현손익 포함 체결기준잔고(HTS [0800]). **모의투자 미지원**.

        .. note:: 요약 필드는 KIS 응답예시로 확증되지 않았다(레이아웃 기준). 전체 원본은 ``_raw``."""
        cano, product_code = self._c._require_account()
        return account_api.fetch_realized_profit_balance(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def integrated_margin(
        self, *, include_cma: bool = False, won_basis: bool = True
    ) -> IntegratedMargin:
        """원화+외화 통합증거금 현황. ``include_cma`` CMA평가금액 포함, ``won_basis`` 원화(True)/외화(False).
        **모의투자 미지원**.

        .. note:: 필드가 방대해 headline 만 타입화했다. 통화별 세부는 ``_raw`` 참조."""
        cano, product_code = self._c._require_account()
        return account_api.fetch_integrated_margin(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            include_cma=include_cma, won_basis=won_basis,
        )

    def trade_profits(
        self, *, start: str, end: str, symbol: str | None = None, sort: str = "recent"
    ) -> TradeProfitHistory:
        """종목별 실현손익 + 기간 총계(총실현손익·총수익률·수수료·세금). ``symbol`` 없으면 전체,
        ``sort`` = ``"recent"``/``"oldest"``. 금액은 KRW Decimal. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return account_api.fetch_trade_profits(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            start=start, end=end, symbol=symbol, sort=sort,
        )

    def daily_profits(
        self, *, start: str, end: str, symbol: str | None = None, sort: str = "recent"
    ) -> DailyProfitHistory:
        """일별 매매손익 합산(하루 단위 매수/매도금액·실현손익·수익률과 기간 총계). :meth:`trade_profits`
        의 일별 그래뉼래러티(종목 구분 없음). **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return account_api.fetch_daily_profits(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            start=start, end=end, symbol=symbol, sort=sort,
        )

    def rights(self, *, start: str, end: str) -> list[AccountRight]:
        """계좌에 배정/신청/환불된 권리(유상·무상 증자·배당·상환 등) 내역. 시장 전체 일정을 보는
        ``kis.domestic.calendar`` 와 달리 실제 계좌 내역이다. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return account_api.fetch_account_rights(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            start=start, end=end,
        )

    def open_orders(self) -> list[OpenOrder]:
        """미체결(정정·취소 가능) 주문. 브로커 측 뷰라 ``client_order_id`` 는 없고 KIS 주문번호로 식별한다.
        정정/취소 전 ``cancelable_quantity`` 를 확인하라. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return account_api.fetch_open_orders(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def reserved_orders(
        self, *, start: str, end: str, process: str = "all"
    ) -> list[ReservedOrder]:
        """예약주문 목록(다음 영업일 동시호가 등에 걸어둔 예약). ``process`` =
        ``"all"``/``"processed"``/``"unprocessed"``. 각 건의 ``sequence`` 로 정정·취소한다. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return reserved_orders_api.fetch_reserved_orders(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            start=start, end=end, process=process,
        )

    def cancel_reserved_order(self, sequence: str, *, order_date: str | None = None) -> None:
        """예약주문을 취소한다 -- ``sequence`` 는 :meth:`~kis_trader.domestic.stock.DomesticStock.reserve_buy` 리포트의
        ``order_id``(예약주문순번). 정상 처리면 조용히 반환, 아니면 예외. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        reserved_orders_api.cancel_reserved_order(
            self._c.transport, sequence=sequence, order_date=order_date,
            cano=cano, product_code=product_code, environment=self._c.environment,
        )

    def modify_reserved_order(
        self, sequence: str, *, symbol: str, side: Side, quantity: object,
        limit_price: object | None = None, end_date: str | None = None, order_date: str | None = None,
    ) -> None:
        """예약주문을 정정한다 -- 브로커 규격상 종목/방향/수량/단가/종료일을 **전체 재지정**한다.
        ``limit_price`` 를 생략하면 기존 단가 유지가 아니라 **시장가**로 바뀐다. 정정 후 순번이 바뀔 수
        있으니 이후 정정·취소가 필요하면 :meth:`reserved_orders` 로 재확인하라. **모의투자 미지원**, 국내
        현금 예약만."""
        cano, product_code = self._c._require_account()
        reserved_orders_api.modify_reserved_order(
            self._c.transport, sequence=sequence, symbol=symbol, side=side, quantity=quantity,
            limit_price=limit_price, end_date=end_date, order_date=order_date,
            cano=cano, product_code=product_code, environment=self._c.environment,
        )


class OverseasAccount:
    """``kis.overseas.account`` -- 해외 계좌 조회·계좌 단위 주문(잔고/손익/알고/예약주문).

    모든 메서드는 계좌 미설정 시 :class:`~kis_trader.errors.KISUsageError` 를 던진다. ``**모의투자
    미지원**`` 이라 표시된 메서드는 ``environment="paper"`` 에서도 :class:`~kis_trader.errors.
    KISUsageError` 다. 조회 실패·응답 부재·파싱 실패는 :class:`~kis_trader.errors.KISError`.
    """

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def positions(self, *, market: str | None = None) -> list[OverseasPosition]:
        """보유 종목(거래소 그룹+통화별). ``market`` = ``"US"``/``"HK"``/``"CN_SH"``/``"CN_SZ"``/``"JP"``/
        ``"VN_HN"``/``"VN_HCM"``, 생략(``None``)하면 **전체 시장 그룹을 순회해 합친다**. 금액은 종목 통화의
        :class:`~kis_trader.money.Money`."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_positions(
            self._c.transport, cano=cano, product_code=product_code,
            environment=self._c.environment, market=market,
        )

    def balance(self, *, market: str) -> OverseasBalance:
        """계좌 손익 요약(시장/통화별) -- 매입금액·평가/실현/총손익·총수익률을 :class:`~kis_trader.money.Money`
        로. 전체 시장 종합은 :meth:`present_balance`. 예수금(현금)은 별도다."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_balance(
            self._c.transport, cano=cano, product_code=product_code,
            environment=self._c.environment, market=market,
        )

    def buyable(self, symbol: str, *, exchange: str, price: object) -> OverseasBuyableAmount:
        """매수가능금액. ``exchange`` 는 시세 거래소코드(NAS/NYS/AMS/HKS/SHS/SZS/TSE/HNX/HSX), ``price`` 는
        의도한 주문단가. 외화·통합 기준 주문가능금액·최대수량을 :class:`~kis_trader.money.Money` 로 준다.
        **매수 시 수량단위 절사가 필요**하다."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_buyable_amount(
            self._c.transport, cano=cano, product_code=product_code,
            environment=self._c.environment, symbol=symbol, exchange=exchange, price=price,
        )

    def foreign_margin(self) -> list[OverseasForeignMargin]:
        """통화별 외화 예수금·증거금·주문가능금액. 금액은 각 통화의 :class:`~kis_trader.money.Money`.
        **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_foreign_margin(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def present_balance(
        self, *, won_basis: bool = True, nation: str = "all", market_code: str = "00",
        inquiry: str = "00",
    ) -> OverseasPresentBalance:
        """체결기준현재잔고 -- 보유 종목·통화별 예수금·계좌 요약. ``won_basis`` 원화(True)/외화(False),
        ``nation`` 국가(``"all"``/``"US"``/``"HK"``/``"CN"``/``"JP"``/``"VN"``), ``market_code`` 거래시장코드
        (``"00"``=전체), ``inquiry`` 조회구분. 모의는 요약만 온다.

        .. note:: 요약 필드는 KIS 예시가 잘려 레이아웃 기준이다 -- 전체 원본은 ``_raw``."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_present_balance(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            won_basis=won_basis, nation=nation, market_code=market_code, inquiry=inquiry,
        )

    def settlement_balance(
        self, *, basis_date: str, won_basis: bool = True, inquiry: str = "00"
    ) -> OverseasSettlementBalance:
        """결제기준잔고 -- ``basis_date``(YYYYMMDD) 결제 기준의 보유 종목·통화별 예수금·계좌 요약.
        ``won_basis`` 원화(True)/외화(False), ``inquiry`` 조회구분. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_settlement_balance(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            basis_date=basis_date, won_basis=won_basis, inquiry=inquiry,
        )

    def period_profit(
        self, *, start: str, end: str, exchange: str = "", nation: str = "", currency: str = "",
        symbol: str = "", won_basis: bool = False,
    ) -> OverseasPeriodProfit:
        """기간손익 -- ``start``~``end``(YYYYMMDD) 매도청산 종목별 실현손익과 총계. ``exchange`` 거래소
        (공란=전체), ``currency`` 통화(공란=전체), ``symbol`` 종목(공란=전체), ``won_basis`` 원화(True)/외화
        (False). **모의투자 미지원**.

        .. note:: KIS 예시가 비어 있어 필드는 레이아웃 기준이다 -- 전체 원본은 각 행/결과의 ``_raw``."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_period_profit(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            start=start, end=end, exchange=exchange, nation=nation, currency=currency,
            symbol=symbol, won_basis=won_basis,
        )

    def transactions(
        self, *, start: str, end: str, symbol: str | None = None, side: str = "all"
    ) -> list[OverseasTransaction]:
        """일별 거래내역(매매·결제·수수료). ``start``/``end`` 는 등록일자 기간(YYYYMMDD), ``symbol`` 없으면
        전체, ``side`` = ``"all"``/``"sell"``/``"buy"``. 외화 금액은 거래 통화의 :class:`~kis_trader.money.Money`.
        **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_transactions(
            self._c.transport, cano=cano, product_code=product_code,
            environment=self._c.environment, start=start, end=end, symbol=symbol, side=side,
        )

    def open_orders(self, *, market: str | None = None) -> list[OverseasOpenOrder]:
        """미체결(열린) 주문. 거래소 주문번호·미체결 잔량을 준다. ``market`` 생략(``None``)하면 **전체 시장
        그룹을 순회해 합친다**. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_open_orders(
            self._c.transport, cano=cano, product_code=product_code,
            environment=self._c.environment, market=market,
        )

    def algo_orders(self) -> list[OverseasAlgoOrder]:
        """알고(TWAP/VWAP 등 분할집행) 주문 목록. 각 건의 ``order_id``/``branch_number`` 로 :meth:`algo_executions`
        를 조회한다. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_algo_orders(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def algo_executions(
        self, order_id: str, *, order_date: str, branch_number: str = ""
    ) -> list[OverseasAlgoExecution]:
        """한 알고주문의 체결내역. ``order_id`` 는 :meth:`algo_orders` 의 주문번호, ``order_date``(YYYYMMDD)는
        주문일자, ``branch_number`` 는 그 주문의 채번지점번호. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_algo_executions(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            order_date=order_date, order_id=order_id, branch_number=branch_number,
        )

    def reserved_orders(self, *, start: str, end: str) -> list[OverseasReservedOrder]:
        """미국 예약주문 목록(정규장 시작 전 예약). 각 건의 ``reserved_order_id`` 로 취소한다. 아시아
        (일/중/홍/베) 예약은 별 프로토콜이라 미지원. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_reserved_orders_api.fetch_reserved_orders(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            start=start, end=end,
        )

    def cancel_reserved_order(self, reserved_order_id: str, *, receipt_date: str) -> None:
        """미국 예약주문을 취소한다 -- ``reserved_order_id`` 는 :meth:`~kis_trader.overseas.stock.OverseasStock.reserve_buy`
        리포트의 ``order_id``, ``receipt_date``(YYYYMMDD)는 그 예약의 접수일자. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        overseas_reserved_orders_api.cancel_overseas_reserved_order(
            self._c.transport, reserved_order_id=reserved_order_id, receipt_date=receipt_date,
            cano=cano, product_code=product_code, environment=self._c.environment,
        )


class DomesticNamespace:
    """``kis.domestic`` -- 국내 자산(주식·지수·채권·ELW·파생) 시세/계좌/순위/시장/일정."""

    def __init__(self, client: KISClient) -> None:
        self._c = client
        self.account = DomesticAccount(client)

    # -- 종목/상품 핸들 --
    def stock(self, code: str, *, market: DomesticBoard | None = None) -> DomesticStock:
        """국내 종목/ETF 핸들. 시장은 심볼로 자동 판별한다(6자리 숫자 -> KRX; ``market`` 로 보드 지정 가능)."""
        return DomesticStock(self._c, code, market=market)

    def index(self, code: str) -> Index:
        """지수/업종 핸들. ``code`` 는 업종코드(0001 KOSPI 종합, 1001 KOSDAQ 종합, 2001 KOSPI200 등)."""
        return Index(self._c, code)

    def bond(self, code: str) -> Bond:
        """장내채권 핸들. ``code`` 는 표준코드(ISIN, 예: KR2033022D33)."""
        return Bond(self._c, code)

    def elw(self, code: str) -> ELW:
        """ELW 고유 지표 핸들. 기본 시세는 :meth:`stock` 으로, 이 핸들은 그릭스·변동성·투자지표만 얹는다."""
        return ELW(self._c, code)

    def futures(self, code: str) -> FuturesContract:
        """지수선물 계약 핸들. ``code`` 는 계약코드(예: 101W09)."""
        return FuturesContract(self._c, code)

    def option(self, code: str) -> OptionContract:
        """지수옵션 계약 핸들. ``code`` 는 계약코드."""
        return OptionContract(self._c, code)

    # -- 파생 보드/조회 --
    def option_expiries(self) -> list[OptionExpiry]:
        """상장된 지수옵션 만기 월물 목록. 옵션 계약코드를 만들기 전에 유효한 만기를 확인하는 용도."""
        return derivatives_api.fetch_option_expiries(self._c.transport)

    def option_board(self, expiry: str, *, underlying: str = "KOSPI200") -> OptionBoard:
        """한 만기월의 옵션 콜/풋 전광판(행사가별 시세·그릭스). expiry 는 OptionExpiry.year_month."""
        return derivatives_api.fetch_option_board(
            self._c.transport, expiry=expiry, underlying=underlying
        )

    def option_board_futures(self, *, market_class: str = "MKI") -> list[FuturesBoardQuote]:
        """옵션 전광판 하단의 선물 계약별 현재가·호가·미결제약정·예상체결가."""
        return derivatives_api.fetch_futures_board_quotes(
            self._c.transport, market_class=market_class
        )

    # -- 다종목/상품 조회 --
    def quotes(
        self, symbols: Sequence[str | tuple[DomesticBoard, str]], *, market: DomesticBoard = "KRX"
    ) -> list[Quote]:
        """여러 국내 종목의 현재가를 한 번에(최대 30). 원소가 종목코드 문자열이면 보드는 ``market`` 기본
        (KRX), ``(board, symbol)`` 튜플이면 그 보드를 쓴다 -- KRX/NXT/통합(UN) 혼합 가능. 해외는
        :meth:`~kis_trader.namespaces.OverseasNamespace.quotes`."""
        requests = [
            item if isinstance(item, tuple) else (market, item) for item in symbols
        ]
        return market_data_api.fetch_multi_quotes(self._c.transport, requests=requests)

    def product_info(self, symbol: str, *, product_type: str = "300") -> ProductInfo:
        """상품 공통 등록·판매 기본정보. 상품유형 기본값 ``"300"`` 은 국내 주식군이다."""
        return product_api.fetch_product_info(
            self._c.transport, symbol=symbol, product_type=product_type
        )

    def saved_screens(self, user_id: str) -> list[SavedScreen]:
        """HTS에 서버 저장된 종목검색 조건 목록."""
        return saved_screen_api.fetch_saved_screens(self._c.transport, user_id=user_id)

    def saved_screen_stocks(self, user_id: str, sequence: str) -> list[SavedScreenStock]:
        """저장 조건 하나에 일치하는 종목 시세(최대 100건)."""
        return saved_screen_api.fetch_saved_screen_stocks(
            self._c.transport, user_id=user_id, sequence=sequence
        )

    def watchlist_groups(self, user_id: str) -> list[WatchlistGroup]:
        """HTS 관심종목 그룹 목록."""
        return saved_screen_api.fetch_watchlist_groups(self._c.transport, user_id=user_id)

    def watchlist(self, user_id: str, group_code: str) -> Watchlist:
        """HTS 관심종목 그룹 하나의 요약과 구성 종목(최대 30개)."""
        return saved_screen_api.fetch_watchlist(
            self._c.transport, user_id=user_id, group_code=group_code
        )

    # -- 하위 질의 네임스페이스 --
    @property
    def ranking(self) -> RankingQueries:
        """시장 전체 순위 질의 -- ``kis.domestic.ranking.by_change()`` / ``by_volume()`` 등."""
        return RankingQueries(self._c)

    @property
    def market(self) -> MarketQueries:
        """시장 전체 분석 질의 -- ``kis.domestic.market.investor_flows(market="KOSPI")`` 등
        (종목/순위가 아닌 시장 전체 수급·상태)."""
        return MarketQueries(self._c)

    @property
    def calendar(self) -> CalendarQueries:
        """기업행위 캘린더 질의 -- ``kis.domestic.calendar.dividends(start=..., end=...)`` 등(예탁결제원 일정)."""
        return CalendarQueries(self._c)

    @property
    def elw_ranking(self) -> ELWRankingQueries:
        """시장 전체 ELW 순위 질의 -- ``kis.domestic.elw_ranking.by_volume()`` / ``by_sensitivity()`` 등."""
        return ELWRankingQueries(self._c)

    @property
    def elw_screener(self) -> ELWScreenerQueries:
        """ELW 스크리닝 질의 -- ``underlyings()`` / ``by_underlying(code)`` / ``newly_listed(date=...)`` 등."""
        return ELWScreenerQueries(self._c)


class OverseasNamespace:
    """``kis.overseas`` -- 해외 자산(주식·지수·파생) 시세/계좌/순위/뉴스/기업행위."""

    def __init__(self, client: KISClient) -> None:
        self._c = client
        self.account = OverseasAccount(client)

    # -- 종목/상품 핸들 --
    def stock(self, symbol: str, *, exchange: str | None = None) -> OverseasStock:
        """해외 종목 핸들. ``exchange`` (거래소코드 NAS/NYS/AMS/TSE/HKS/...)를 생략하면 KIS 종목 마스터로
        거래소를 자동 해석한다(첫 조회는 마스터를 받아 캐시 -- 느릴 수 있다). 같은 심볼이 여러 거래소면
        ``exchange`` 를 명시해야 한다(:class:`~kis_trader.errors.KISUsageError`)."""
        if exchange is None:
            exchange = self._c.instrument(symbol).exchange
        return OverseasStock(self._c, symbol, exchange=exchange)

    def index(self, symbol: str, *, kind: str = "index") -> OverseasIndex:
        """해외 지수/환율/국채/금선물 핸들. ``kind`` 는 ``index``/``fx``/``bond``/``gold`` 중 하나, ``symbol``
        은 지수코드(예: ``.DJI``). 국내 :meth:`~kis_trader.namespaces.DomesticNamespace.index` 의 해외판이다."""
        division = _OVERSEAS_INDEX_KIND.get(kind)
        if division is None:
            raise KISUsageError(
                f"지원하지 않는 kind: {kind!r} ({'/'.join(_OVERSEAS_INDEX_KIND)})."
            )
        return OverseasIndex(self._c, symbol, market_division=division)

    def futures(self, srs_cd: str) -> OverseasDerivative:
        """해외 선물 계약 핸들. ``srs_cd`` 는 시리즈코드(예: ESZ25 = E-mini S&P 2025.12)."""
        return OverseasDerivative(self._c, srs_cd, market="future")

    def option(self, srs_cd: str) -> OverseasDerivative:
        """해외 옵션 계약 핸들. ``srs_cd`` 는 시리즈코드."""
        return OverseasDerivative(self._c, srs_cd, market="option")

    # -- 파생 배치/조회 --
    def futures_details(self, symbols: Sequence[str]) -> list[OverseasDerivativeDetail]:
        """여러 해외 선물 계약의 명세를 한 번에(최대 32개, 실전만)."""
        return overseas_derivatives_api.fetch_details(
            self._c.transport, srs_codes=list(symbols), market="future",
            environment=self._c.environment,
        )

    def option_details(self, symbols: Sequence[str]) -> list[OverseasDerivativeDetail]:
        """여러 해외 옵션 계약의 명세를 한 번에(최대 30개, 실전만)."""
        return overseas_derivatives_api.fetch_details(
            self._c.transport, srs_codes=list(symbols), market="option",
            environment=self._c.environment,
        )

    def derivatives_market_hours(
        self, *, product_group: str = "", asset_class: str = "", exchange: str = "", kind: str = "%"
    ) -> list[OverseasDerivativeMarketHours]:
        """해외 선물/옵션 상품군별 장운영시간(시장 전체, 계약 무관). 필터를 생략하면 전체를 조회한다.
        **모의투자 미지원**."""
        return overseas_derivatives_api.fetch_market_hours(
            self._c.transport, environment=self._c.environment,
            product_group=product_group, asset_class=asset_class, exchange=exchange, kind=kind,
        )

    def futures_open_interest(
        self, product: str, *, as_of: str | date, mode: str = "quantity"
    ) -> list[OverseasFuturesOpenInterest]:
        """해외선물 상품의 CFTC 미결제약정 수량 또는 증감 추이(실전만)."""
        return overseas_derivatives_api.fetch_open_interest(
            self._c.transport, product=product, as_of=as_of, mode=mode,
            environment=self._c.environment,
        )

    def settlement_dates(self) -> list[OverseasSettlementDate]:
        """해외 각 시장의 현지·국내 결제일자(시장 전체 참조표). **모의투자 미지원**. 결제일이 비어 있거나
        유효하지 않으면 해당 날짜는 ``None`` 이다."""
        return overseas_reference_api.fetch_settlement_dates(
            self._c.transport, environment=self._c.environment
        )

    # -- 다종목/검색/정보 --
    def quotes(self, symbols: Sequence[tuple[str, str]]) -> list[Quote]:
        """여러 해외 종목의 현재가를 한 번에(최대 10). 원소는 ``(exchange, symbol)`` 튜플(거래소코드
        NAS/NYS/AMS/HKS/TSE/... 혼합 가능). 국내는
        :meth:`~kis_trader.namespaces.DomesticNamespace.quotes`."""
        return overseas_market_data_api.fetch_multi_quotes(
            self._c.transport, requests=[tuple(item) for item in symbols]
        )

    def search_stocks(
        self, exchange: str, **filters: tuple[object, object] | None
    ) -> OverseasStockSearch:
        """해외 종목을 가격·등락률·규모·거래·밸류에이션 범위로 검색한다."""
        return overseas_market_data_api.search_stocks(
            self._c.transport, exchange=exchange, **filters
        )

    def product_info(self, exchange: str, symbol: str) -> OverseasProductInfo:
        """해외 종목의 상품기본정보(거래소·통화·상장주식수·SEDOL·블룸버그티커 등). exchange=NAS/NYS/AMS/TSE/HKS/..."""
        return overseas_market_data_api.fetch_product_info(
            self._c.transport, exchange=exchange, symbol=symbol
        )

    def industries(self, exchange: str) -> list[OverseasIndustry]:
        """해외 거래소의 업종(섹터) 코드 목록. **모의투자 미지원**."""
        return overseas_market_data_api.fetch_industries(
            self._c.transport, exchange=exchange, environment=self._c.environment
        )

    def industry_stocks(
        self, exchange: str, industry_code: str, *, min_volume: int = 0
    ) -> list[OverseasIndustryStock]:
        """해외 거래소의 한 업종에 속한 종목 시세. 거래량 하한은 0·100·1천·1만·10만·100만·1천만."""
        return overseas_market_data_api.fetch_industry_stocks(
            self._c.transport, exchange=exchange, industry_code=industry_code,
            min_volume=min_volume, environment=self._c.environment,
        )

    def collateral_stocks(
        self, symbol: str, country: str, *, sort: str = "name", product_type: str = "",
        loanable: bool | None = None,
    ) -> OverseasCollateralStockSearch:
        """해외주식 담보대출 가능종목 목록(``.stocks``)과 조회 요약(``.summary``) -- 대출 가능 여부와
        적용 비율."""
        return overseas_reference_api.fetch_collateral_stocks(
            self._c.transport, symbol=symbol, country=country, sort=sort,
            product_type=product_type, loanable=loanable,
        )

    # -- 뉴스/기업행위 --
    def news(
        self, *, country: str = "", exchange: str = "", symbol: str = "",
        date_: str | date | None = None, time: str = "", category: str = "",
    ) -> list[OverseasNewsHeadline]:
        """해외뉴스 종합 제목 피드."""
        return overseas_reference_api.fetch_news(
            self._c.transport, country=country, exchange=exchange, symbol=symbol,
            date_=date_, time=time, category=category,
        )

    def breaking_news(
        self, *, symbol: str = "", title: str = "", date_: str | date | None = None, time: str = ""
    ) -> list[NewsHeadline]:
        """해외속보 제목 피드(최대 100건)."""
        return overseas_reference_api.fetch_breaking_news(
            self._c.transport, symbol=symbol, title=title, date_=date_, time=time
        )

    def rights(
        self, *, start: str | date, end: str | date, right_type: str = "%%",
        date_basis: str = "local_base", symbol: str = "", product_type: str = "",
    ) -> list[OverseasRight]:
        """기간별 해외증권 배당·증자·합병 등 권리."""
        return overseas_reference_api.fetch_period_rights(
            self._c.transport, start=start, end=end, right_type=right_type,
            date_basis=date_basis, symbol=symbol, product_type=product_type,
        )

    def corporate_actions(
        self, country: str, symbol: str, *, start: str | date | None = None,
        end: str | date | None = None,
    ) -> list[OverseasCorporateAction]:
        """해외종목 권리·기업행사 종합 일정."""
        return overseas_reference_api.fetch_corporate_actions(
            self._c.transport, country=country, symbol=symbol, start=start, end=end
        )

    # -- 하위 질의 네임스페이스 --
    @property
    def ranking(self) -> OverseasRankingQueries:
        """해외주식 시장 순위 질의 -- ``kis.overseas.ranking.by_volume(exchange="NAS")`` 등. 거래소별로
        조회한다(``exchange`` = NAS/NYS/HKS/...)."""
        return OverseasRankingQueries(self._c)


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
