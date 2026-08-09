"""자산군 최상위 네임스페이스 -- ``kis.domestic`` / ``kis.overseas`` / ``kis.pension`` / ``kis.orders``.

세션 :class:`~kis_openapi.client.KISClient` 의 평평한 verb 를 자산군별로 묶어 노출한다. 국내는 시세·
계좌·순위·시장·일정을, 해외는 시세·계좌·순위·뉴스 등을 각 네임스페이스 아래로 모은다. 계좌는 다시
``.account`` 하위로, 주문 lifecycle(client_order_id 로 동작, 자산 무관)은 ``kis.orders`` 로 둔다.

각 메서드는 세션의 기존 엔진 호출로 위임한다(로직 중복 없음). 이 층은 **API 표면 조직**만 담당하고,
실제 원장 매핑·안전코어는 그대로 세션/엔진에 있다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from .account_reports import IntegratedMargin, RealizedProfitBalance
    from .account_right import AccountRight
    from .balance import AccountAssets, Balance, Portfolio, Position
    from .bond import Bond
    from .calendar import CalendarQueries
    from .client import KISClient
    from .derivative import Derivative
    from .derivative_items import FuturesBoardQuote, OptionBoard, OptionExpiry
    from .elw import ELW
    from .elw_ranking import ELWRankingQueries
    from .elw_screener import ELWScreenerQueries
    from .index import Index
    from .instrument import DomesticBoard
    from .market import MarketQueries
    from .market_items import NewsItem
    from .open_order import OpenOrder
    from .order import Side
    from .overseas_derivative import OverseasDerivative
    from .overseas_derivative_items import (
        OverseasDerivativeDetail,
        OverseasDerivativeMarketHours,
        OverseasFuturesOpenInterest,
    )
    from .overseas_index import OverseasIndex
    from .overseas_items import (
        OverseasAlgoExecution,
        OverseasAlgoOrder,
        OverseasBalance,
        OverseasBuyableAmount,
        OverseasCollateralStock,
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
    from .overseas_ranking import OverseasRankingQueries
    from .pension_items import (
        PensionBalance,
        PensionBuyableAmount,
        PensionDeposit,
        PensionOrder,
        PensionPresentBalance,
    )
    from .product import ProductInfo
    from .quote import Quote
    from .ranking import RankingQueries
    from .report import ExecutionReport
    from .reserved_order import ReservedOrder
    from .saved_screen import SavedScreen, SavedScreenStock, Watchlist, WatchlistGroup
    from .ticker import Ticker
    from .trade_profit import DailyProfitHistory, TradeProfitHistory


class OrdersNamespace:
    """``kis.orders`` -- client_order_id 로 동작하는 주문 lifecycle(자산 무관). 안전 dedup/reconcile 코어."""

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def reconcile(self, client_order_id: str) -> ExecutionReport | None:
        """브로커측 체결과 로컬 상태를 대사한다."""
        return self._c.reconcile(client_order_id)

    def cancel(
        self, client_order_id: str, *, quantity: object | None = None, request_id: str | None = None
    ) -> ExecutionReport:
        """접수된 주문을 취소한다(부분 취소는 ``quantity``)."""
        return self._c.cancel_order(client_order_id, quantity=quantity, request_id=request_id)

    def replace(
        self, client_order_id: str, *, price: object, quantity: object | None = None,
        request_id: str | None = None,
    ) -> ExecutionReport:
        """접수된 주문의 가격(또는 수량)을 정정한다."""
        return self._c.replace_order(
            client_order_id, price=price, quantity=quantity, request_id=request_id
        )


class DomesticAccount:
    """``kis.domestic.account`` -- 국내 계좌 조회·계좌 단위 주문(잔고/손익/예약주문)."""

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def balance(self) -> Balance:
        """계좌 현금·자산 요약."""
        return self._c.balance()

    def positions(self) -> list[Position]:
        """보유 종목."""
        return self._c.positions()

    def portfolio(self) -> Portfolio:
        """보유 종목 + 요약을 한 스냅샷으로."""
        return self._c.portfolio()

    def assets(self) -> AccountAssets:
        """투자계좌 자산현황(총자산·순자산·예수금·대출·외화)."""
        return self._c.account_assets()

    def realized_profit_balance(self) -> RealizedProfitBalance:
        """실현손익 포함 체결기준잔고(HTS [0800])."""
        return self._c.realized_profit_balance()

    def integrated_margin(
        self, *, include_cma: bool = False, won_basis: bool = True
    ) -> IntegratedMargin:
        """원화+외화 통합증거금 현황."""
        return self._c.integrated_margin(include_cma=include_cma, won_basis=won_basis)

    def trade_profits(
        self, *, start: str, end: str, symbol: str | None = None, sort: str = "recent"
    ) -> TradeProfitHistory:
        """종목별 실현손익 + 기간 총계."""
        return self._c.trade_profits(start=start, end=end, symbol=symbol, sort=sort)

    def daily_profits(
        self, *, start: str, end: str, symbol: str | None = None, sort: str = "recent"
    ) -> DailyProfitHistory:
        """일별 실현손익."""
        return self._c.daily_profits(start=start, end=end, symbol=symbol, sort=sort)

    def rights(self, *, start: str, end: str) -> list[AccountRight]:
        """계좌에 배정/신청/환불된 권리 내역."""
        return self._c.account_rights(start=start, end=end)

    def open_orders(self) -> list[OpenOrder]:
        """미체결/정정취소가능 주문."""
        return self._c.open_orders()

    def reserved_orders(
        self, *, start: str, end: str, process: str = "all"
    ) -> list[ReservedOrder]:
        """예약주문 목록."""
        return self._c.reserved_orders(start=start, end=end, process=process)

    def cancel_reserved_order(self, sequence: str, *, order_date: str | None = None) -> None:
        """예약주문을 취소한다(예약순번 지목)."""
        return self._c.cancel_reserved_order(sequence, order_date=order_date)

    def modify_reserved_order(
        self, sequence: str, *, symbol: str, side: Side, quantity: object,
        price: object | None = None, end_date: str | None = None, order_date: str | None = None,
    ) -> None:
        """예약주문을 정정한다(전체 재spec)."""
        return self._c.modify_reserved_order(
            sequence, symbol=symbol, side=side, quantity=quantity, price=price,
            end_date=end_date, order_date=order_date,
        )


class OverseasAccount:
    """``kis.overseas.account`` -- 해외 계좌 조회·계좌 단위 주문(잔고/손익/알고/예약주문)."""

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def positions(self, *, market: str | None = None) -> list[OverseasPosition]:
        """보유 종목. ``market`` 생략 시 전체 시장 그룹 합산."""
        return self._c.overseas_positions(market=market)

    def balance(self, *, market: str) -> OverseasBalance:
        """계좌 손익 요약(시장/통화별). 전체 시장 종합은 :meth:`present_balance`."""
        return self._c.overseas_balance(market=market)

    def buyable(self, symbol: str, *, exchange: str, price: object) -> OverseasBuyableAmount:
        """매수가능 금액/수량."""
        return self._c.overseas_buyable(symbol, exchange=exchange, price=price)

    def foreign_margin(self) -> list[OverseasForeignMargin]:
        """통화별 외화 예수금·증거금·주문가능금액."""
        return self._c.overseas_foreign_margin()

    def present_balance(
        self, *, won_basis: bool = True, nation: str = "all", market_code: str = "00",
        inquiry: str = "00",
    ) -> OverseasPresentBalance:
        """체결기준현재잔고(보유 종목·통화별 예수금·요약)."""
        return self._c.overseas_present_balance(
            won_basis=won_basis, nation=nation, market_code=market_code, inquiry=inquiry
        )

    def settlement_balance(
        self, *, basis_date: str, won_basis: bool = True, inquiry: str = "00"
    ) -> OverseasSettlementBalance:
        """결제기준잔고(기준일자 결제 기준)."""
        return self._c.overseas_settlement_balance(
            basis_date=basis_date, won_basis=won_basis, inquiry=inquiry
        )

    def period_profit(
        self, *, start: str, end: str, exchange: str = "", nation: str = "", currency: str = "",
        symbol: str = "", won_basis: bool = False,
    ) -> OverseasPeriodProfit:
        """기간손익(매도청산 종목별 실현손익 + 총계)."""
        return self._c.overseas_period_profit(
            start=start, end=end, exchange=exchange, nation=nation, currency=currency,
            symbol=symbol, won_basis=won_basis,
        )

    def transactions(
        self, *, start: str, end: str, symbol: str | None = None, side: str = "all"
    ) -> list[OverseasTransaction]:
        """일별 체결 거래내역."""
        return self._c.overseas_transactions(start=start, end=end, symbol=symbol, side=side)

    def open_orders(self, *, market: str | None = None) -> list[OverseasOpenOrder]:
        """미체결 주문. ``market`` 생략 시 전체 시장 그룹 합산."""
        return self._c.overseas_open_orders(market=market)

    def algo_orders(self) -> list[OverseasAlgoOrder]:
        """알고(분할집행) 주문 목록."""
        return self._c.overseas_algo_orders()

    def algo_executions(
        self, order_id: str, *, order_date: str, branch_number: str = ""
    ) -> list[OverseasAlgoExecution]:
        """한 알고주문의 체결내역."""
        return self._c.overseas_algo_executions(
            order_id, order_date=order_date, branch_number=branch_number
        )

    def reserved_orders(self, *, start: str, end: str) -> list[OverseasReservedOrder]:
        """미국 예약주문 목록."""
        return self._c.overseas_reserved_orders(start=start, end=end)

    def cancel_reserved_order(self, reserved_order_id: str, *, receipt_date: str) -> None:
        """미국 예약주문을 취소한다."""
        return self._c.cancel_overseas_reserved_order(reserved_order_id, receipt_date=receipt_date)


class DomesticNamespace:
    """``kis.domestic`` -- 국내 자산(주식·지수·채권·ELW·파생) 시세/계좌/순위/시장/일정."""

    def __init__(self, client: KISClient) -> None:
        self._c = client
        self.account = DomesticAccount(client)

    # -- 종목/상품 핸들 --
    def stock(self, code: str, *, market: DomesticBoard | None = None) -> Ticker:
        """국내 종목/ETF 핸들."""
        return self._c._make_stock(code, market=market)

    def index(self, code: str) -> Index:
        """지수 핸들."""
        return self._c.index(code)

    def bond(self, code: str) -> Bond:
        """장내채권 핸들."""
        return self._c.bond(code)

    def elw(self, code: str) -> ELW:
        """ELW 핸들."""
        return self._c.elw(code)

    def futures(self, code: str) -> Derivative:
        """선물 핸들."""
        return self._c.futures(code)

    def option(self, code: str) -> Derivative:
        """옵션 핸들."""
        return self._c.option(code)

    # -- 파생 보드/조회 --
    def option_expiries(self) -> list[OptionExpiry]:
        """옵션 만기 목록."""
        return self._c.option_expiries()

    def option_board(self, expiry: str, *, underlying: str = "KOSPI200") -> OptionBoard:
        """옵션 전광판."""
        return self._c.option_board(expiry, underlying=underlying)

    def option_board_futures(self, *, market_class: str = "MKI") -> list[FuturesBoardQuote]:
        """선물 전광판."""
        return self._c.option_board_futures(market_class=market_class)

    # -- 다종목/상품 조회 --
    def quotes(
        self, symbols: Sequence[str | tuple[DomesticBoard, str]], *, market: DomesticBoard = "KRX"
    ) -> list[Quote]:
        """다종목 시세."""
        return self._c.quotes(symbols, market=market)

    def product_info(self, symbol: str, *, product_type: str = "300") -> ProductInfo:
        """상품 기본정보."""
        return self._c.product_info(symbol, product_type=product_type)

    def saved_screens(self, user_id: str) -> list[SavedScreen]:
        """저장된 조건검색."""
        return self._c.saved_screens(user_id)

    def saved_screen_stocks(self, user_id: str, sequence: str) -> list[SavedScreenStock]:
        """조건검색 결과 종목."""
        return self._c.saved_screen_stocks(user_id, sequence)

    def watchlist_groups(self, user_id: str) -> list[WatchlistGroup]:
        """관심종목 그룹."""
        return self._c.watchlist_groups(user_id)

    def watchlist(self, user_id: str, group_code: str) -> Watchlist:
        """관심종목."""
        return self._c.watchlist(user_id, group_code)

    # -- 하위 질의 네임스페이스 --
    @property
    def ranking(self) -> RankingQueries:
        """국내 순위 질의."""
        return self._c.ranking

    @property
    def market(self) -> MarketQueries:
        """시장 전체 수급·일정·뉴스 질의."""
        return self._c.market

    @property
    def calendar(self) -> CalendarQueries:
        """기업행위 일정 질의."""
        return self._c.calendar

    @property
    def elw_ranking(self) -> ELWRankingQueries:
        """ELW 순위 질의."""
        return self._c.elw_ranking

    @property
    def elw_screener(self) -> ELWScreenerQueries:
        """ELW 스크리너 질의."""
        return self._c.elw_screener


class OverseasNamespace:
    """``kis.overseas`` -- 해외 자산(주식·지수·파생) 시세/계좌/순위/뉴스/기업행위."""

    def __init__(self, client: KISClient) -> None:
        self._c = client
        self.account = OverseasAccount(client)

    # -- 종목/상품 핸들 --
    def stock(self, symbol: str, *, exchange: str | None = None) -> Ticker:
        """해외 종목 핸들(``exchange`` 없으면 종목 마스터로 자동 해석)."""
        return self._c._make_stock(symbol, exchange=exchange)

    def index(self, symbol: str, *, kind: str = "index") -> OverseasIndex:
        """해외 지수/환율/채권/금 핸들."""
        return self._c.overseas_index(symbol, kind=kind)

    def futures(self, srs_cd: str) -> OverseasDerivative:
        """해외 선물 핸들."""
        return self._c.overseas_futures(srs_cd)

    def option(self, srs_cd: str) -> OverseasDerivative:
        """해외 옵션 핸들."""
        return self._c.overseas_option(srs_cd)

    # -- 파생 배치/조회 --
    def futures_details(self, symbols: Sequence[str]) -> list[OverseasDerivativeDetail]:
        """해외선물 상품기본정보(배치)."""
        return self._c.overseas_futures_details(symbols)

    def option_details(self, symbols: Sequence[str]) -> list[OverseasDerivativeDetail]:
        """해외옵션 상품기본정보(배치)."""
        return self._c.overseas_option_details(symbols)

    def derivatives_market_hours(
        self, *, product_group: str = "", asset_class: str = "", exchange: str = "", kind: str = "%"
    ) -> list[OverseasDerivativeMarketHours]:
        """해외파생 거래시간."""
        return self._c.overseas_derivatives_market_hours(
            product_group=product_group, asset_class=asset_class, exchange=exchange, kind=kind
        )

    def futures_open_interest(
        self, product: str, *, as_of: str | date, mode: str = "quantity"
    ) -> list[OverseasFuturesOpenInterest]:
        """해외선물 미결제추이."""
        return self._c.overseas_futures_open_interest(product, as_of=as_of, mode=mode)

    def settlement_dates(self) -> list[OverseasSettlementDate]:
        """해외 결제일 정보."""
        return self._c.overseas_settlement_dates()

    # -- 다종목/검색/정보 --
    def quotes(self, symbols: Sequence[tuple[str, str]]) -> list[Quote]:
        """다종목 시세."""
        return self._c.overseas_quotes(symbols)

    def search_stocks(
        self, exchange: str, **filters: tuple[object, object] | None
    ) -> OverseasStockSearch:
        """조건 종목검색(가격·등락률·규모·거래·밸류에이션 범위 필터)."""
        return self._c.search_overseas_stocks(exchange, **filters)

    def product_info(self, exchange: str, symbol: str) -> OverseasProductInfo:
        """상품 기본정보."""
        return self._c.overseas_product_info(exchange, symbol)

    def industries(self, exchange: str) -> list[OverseasIndustry]:
        """업종 목록."""
        return self._c.overseas_industries(exchange)

    def industry_stocks(
        self, exchange: str, industry_code: str, *, min_volume: int = 0
    ) -> list[OverseasIndustryStock]:
        """업종 구성종목."""
        return self._c.overseas_industry_stocks(exchange, industry_code, min_volume=min_volume)

    def collateral_stocks(
        self, symbol: str, country: str, *, sort: str = "name", product_type: str = "",
        loanable: bool | None = None,
    ) -> list[OverseasCollateralStock]:
        """담보대출 가능 종목."""
        return self._c.overseas_collateral_stocks(
            symbol, country, sort=sort, product_type=product_type, loanable=loanable
        )

    # -- 뉴스/기업행위 --
    def news(
        self, *, country: str = "", exchange: str = "", symbol: str = "",
        date_: str | date | None = None, time: str = "", category: str = "",
    ) -> list[OverseasNewsHeadline]:
        """해외 뉴스 헤드라인."""
        return self._c.overseas_news(
            country=country, exchange=exchange, symbol=symbol, date_=date_, time=time,
            category=category,
        )

    def breaking_news(
        self, *, symbol: str = "", title: str = "", date_: str | date | None = None, time: str = ""
    ) -> list[NewsItem]:
        """해외 속보."""
        return self._c.overseas_breaking_news(symbol=symbol, title=title, date_=date_, time=time)

    def rights(
        self, *, start: str | date, end: str | date, right_type: str = "%%",
        date_basis: str = "local_base", symbol: str = "", product_type: str = "",
    ) -> list[OverseasRight]:
        """해외 권리 일정."""
        return self._c.overseas_rights(
            start=start, end=end, right_type=right_type, date_basis=date_basis, symbol=symbol,
            product_type=product_type,
        )

    def corporate_actions(
        self, country: str, symbol: str, *, start: str | date | None = None,
        end: str | date | None = None,
    ) -> list[OverseasCorporateAction]:
        """해외 기업행위."""
        return self._c.overseas_corporate_actions(country, symbol, start=start, end=end)

    # -- 하위 질의 네임스페이스 --
    @property
    def ranking(self) -> OverseasRankingQueries:
        """해외 순위 질의."""
        return self._c.overseas_ranking


class PensionNamespace:
    """``kis.pension`` -- 퇴직연금 계좌(예수금/매수가능/잔고/체결). 전부 실전전용."""

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def deposit(self) -> PensionDeposit:
        """예수금 총액·정산·결제."""
        return self._c.pension_deposit()

    def buyable(self, symbol: str, *, limit_price: object | None = None) -> PensionBuyableAmount:
        """주문가능현금/최대매수."""
        return self._c.pension_buyable(symbol, limit_price=limit_price)

    def balance(self) -> PensionBalance:
        """보유 종목 + 예수금 기준 요약."""
        return self._c.pension_balance()

    def present_balance(self) -> PensionPresentBalance:
        """체결기준 잔고 + 손익 요약."""
        return self._c.pension_present_balance()

    def orders(self, *, only_unfilled: bool = False) -> list[PensionOrder]:
        """당일 체결/미체결."""
        return self._c.pension_orders(only_unfilled=only_unfilled)
