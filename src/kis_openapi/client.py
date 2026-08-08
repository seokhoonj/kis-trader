"""세션 루트 -- :class:`KISClient`.

인증(앱키/시크릿)과 기본 계좌를 쥔 세션이다. 모든 행위가 여기서 시작한다:
``kis.ticker("005930")`` 로 종목 핸들을, ``kis.balance()`` 등으로 계좌를 조회한다.
KIS 토큰은 앱키 단위(24h, 재발급 제한)라 세션이 캐시해 재사용한다.

시세만 볼 거면 ``account`` 없이도 되지만, 주문/잔고엔 계좌 식별정보가 필요하다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from ._domestic import account as account_api
from ._domestic import derivatives as derivatives_api
from ._domestic import market_data as market_data_api
from ._domestic import orders as orders_engine
from ._domestic import pension as pension_api
from ._domestic import product as product_api
from ._domestic import reserved_orders as reserved_orders_api
from ._domestic import saved_screen as saved_screen_api
from ._masters import (
    Fetch,
    MasterIndex,
    MasterRecord,
    load_overseas_index,
    urlopen_fetch,
)
from ._overseas import account as overseas_account
from ._overseas import derivatives as overseas_derivatives_api
from ._overseas import market_data as overseas_market_data_api
from ._overseas import orders as overseas_orders_engine
from ._overseas import reference as overseas_reference_api
from .account_right import AccountRight
from .balance import AccountAssets, Balance, Portfolio, Position
from .bond import Bond
from .calendar import CalendarQueries
from .derivative import Derivative
from .derivative_items import FuturesBoardQuote, OptionBoard, OptionExpiry
from .elw import ELW
from .elw_ranking import ELWRankingQueries
from .elw_screener import ELWScreenerQueries
from .errors import KISUsageError
from .index import Index
from .instrument import DomesticBoard, is_domestic_symbol
from .market import MarketQueries
from .market_items import NewsItem
from .open_order import OpenOrder
from .order import Order, Side, mint_client_order_id
from .overseas_derivative import OverseasDerivative
from .overseas_derivative_items import (
    OverseasDerivativeDetail,
    OverseasDerivativeMarketHours,
    OverseasFuturesOpenInterest,
)
from .overseas_index import OverseasIndex
from .overseas_items import (
    OverseasBalance,
    OverseasBuyableAmount,
    OverseasCollateralStock,
    OverseasCorporateAction,
    OverseasForeignMargin,
    OverseasIndustry,
    OverseasIndustryStock,
    OverseasNewsHeadline,
    OverseasOpenOrder,
    OverseasPosition,
    OverseasRight,
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
from .risk import RiskLimits
from .saved_screen import SavedScreen, SavedScreenStock, Watchlist, WatchlistGroup
from .store import OrderStore
from .ticker import Ticker
from .trade_profit import DailyProfitHistory, TradeProfitHistory
from .transport import Transport

_OVERSEAS_INDEX_KIND = {"index": "N", "fx": "X", "bond": "I", "gold": "S"}


class KISClient:
    """KIS Open API 세션. ``transport`` 는 주입된 전송 구현(실제 HTTP 또는 테스트용 가짜)이다."""

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        account: str | None = None,
        environment: Literal["real", "demo"] = "real",
        transport: Transport | None = None,
        store: OrderStore | None = None,
        orderable: bool = True,
        risk: RiskLimits | None = None,
        master_index: MasterIndex | None = None,
        master_fetch: Fetch | None = None,
    ) -> None:
        """세션을 연다.

        ``store`` 는 주문 멱등 dedup 저장소 -- 생략하면 세션 인메모리(프로세스 재시작에 dedup
        유지 안 됨). 실거래는 ``store=OrderStore(path=...)`` 로 영속 저장소를 주는 것을 강력히
        권장한다(재시작 후에도 이중체결 장벽 유지). ``orderable=False`` 면 모든 주문을 와이어
        전에 :class:`~kis_openapi.errors.AccountNotOrderable` 로 막는다(조회전용 계좌 보호).
        ``risk`` 를 주면 모든 buy/sell 이 전송 전에 그 사전 리스크 한도
        (:class:`~kis_openapi.risk.RiskLimits`)를 통과해야 한다(fat-finger 방지).

        해외 심볼 조회(:meth:`instrument`)는 KIS 종목 마스터로 심볼->거래소를 찾는다. ``master_index``
        를 주면 그 인덱스를 쓰고(테스트/고급), 없으면 첫 조회 때 마스터를 받아 캐시한다. ``master_fetch``
        로 다운로더를 바꿀 수 있다(기본은 KIS 배포 서버).
        """
        self._app_key = app_key
        self._app_secret = app_secret
        self._environment = environment
        if transport is None:
            from ._auth import TokenManager
            from ._http import RequestsTransport

            transport = RequestsTransport(
                app_key=app_key,
                app_secret=app_secret,
                environment=environment,
                token_manager=TokenManager(
                    app_key=app_key,
                    app_secret=app_secret,
                    environment=environment,
                ),
            )
        self._transport = transport
        self._cano, self._product_code = _split_account(account)
        # 주문 멱등 dedup 저장소. 기본은 세션 인메모리 -- 프로세스 재시작에도 dedup 을 유지하려면
        # store=OrderStore(path=...) 로 영속 저장소를 주입하라(권장, 이중체결 장벽 지속).
        self._store = store if store is not None else OrderStore()
        self._orderable = orderable
        self._risk = risk
        # 해외 심볼->거래소 해석용 마스터 인덱스. 주입 없으면 첫 instrument() 호출 때 지연 로드.
        self._master_index = master_index
        self._master_fetch = master_fetch if master_fetch is not None else urlopen_fetch

    @property
    def transport(self) -> Transport:
        """저수준 전송(내부 조회 계층이 사용)."""
        return self._transport

    @property
    def environment(self) -> Literal["real", "demo"]:
        """실전(real) / 모의(demo). 계좌·주문 TR 선택에 쓰인다."""
        return self._environment

    def ticker(
        self, symbol: str, *, market: DomesticBoard | None = None, exchange: str | None = None
    ) -> Ticker:
        """종목 핸들을 만든다. 국내는 심볼로 시장 자동 판별(6자리 숫자 -> KRX), 해외는 ``exchange``
        (거래소코드 NAS/NYS/AMS/TSE/HKS/...)를 준다.

        해외 심볼을 ``exchange`` 없이 주면(6자리 숫자가 아니면) KIS 종목 마스터로 거래소를 자동
        해석한다(첫 조회는 마스터를 받아 캐시 -- 느릴 수 있다). 같은 심볼이 여러 거래소면
        ``exchange`` 를 명시해야 한다."""
        if exchange is None and market is None and not is_domestic_symbol(symbol):
            exchange = self.instrument(symbol).exchange     # 해외 바-심볼 -> 마스터로 거래소 해석
        return Ticker(self, symbol, market=market, exchange=exchange)

    def instrument(self, symbol: str, *, exchange: str | None = None) -> MasterRecord:
        """해외 심볼을 KIS 종목 마스터로 조회한다 -- 거래소코드/통화/종목유형/이름을 돌려준다.

        같은 심볼이 여러 거래소에 있으면 ``exchange`` 를 명시해야 한다(:class:`~kis_openapi.errors.
        KISUsageError`). 첫 호출은 마스터를 받아 캐시하므로 느릴 수 있다(이후는 캐시)."""
        if self._master_index is None:
            self._master_index = load_overseas_index(fetch=self._master_fetch)
        return self._master_index.resolve(symbol, exchange=exchange)

    def quotes(
        self, symbols: Sequence[str | tuple[DomesticBoard, str]], *, market: DomesticBoard = "KRX"
    ) -> list[Quote]:
        """여러 국내 종목의 현재가를 한 번에(최대 30). 원소가 종목코드 문자열이면 보드는 ``market``
        기본(KRX), ``(board, symbol)`` 튜플이면 그 보드를 쓴다 -- KRX/NXT/통합(UN) 혼합 가능.
        해외는 :meth:`overseas_quotes` (엔드포인트가 분리돼 한 번에 국내+해외는 불가)."""
        requests = [
            item if isinstance(item, tuple) else (market, item) for item in symbols
        ]
        return market_data_api.fetch_multi_quotes(self.transport, requests=requests)

    def overseas_quotes(self, symbols: Sequence[tuple[str, str]]) -> list[Quote]:
        """여러 해외 종목의 현재가를 한 번에(최대 10). 원소는 ``(exchange, symbol)`` 튜플
        (거래소코드 NAS/NYS/AMS/HKS/TSE/... 혼합 가능). 국내는 :meth:`quotes`."""
        return overseas_market_data_api.fetch_multi_quotes(
            self.transport, requests=[tuple(item) for item in symbols]
        )

    def search_overseas_stocks(
        self, exchange: str, **filters: tuple[object, object] | None
    ) -> OverseasStockSearch:
        """해외 종목을 가격·등락률·규모·거래·밸류에이션 범위로 검색한다."""
        return overseas_market_data_api.search_stocks(
            self.transport, exchange=exchange, **filters
        )

    def overseas_product_info(self, exchange: str, symbol: str) -> OverseasProductInfo:
        """해외 종목의 상품기본정보(거래소·통화·상장주식수·SEDOL·블룸버그티커 등). exchange=NAS/NYS/AMS/TSE/HKS/..."""
        return overseas_market_data_api.fetch_product_info(
            self.transport, exchange=exchange, symbol=symbol
        )

    def product_info(self, symbol: str, *, product_type: str = "300") -> ProductInfo:
        """상품 공통 등록·판매 기본정보. 상품유형 기본값 ``"300"`` 은 국내 주식군이다."""
        return product_api.fetch_product_info(
            self.transport, symbol=symbol, product_type=product_type
        )

    def saved_screens(self, user_id: str) -> list[SavedScreen]:
        """HTS에 서버 저장된 종목검색 조건 목록."""
        return saved_screen_api.fetch_saved_screens(self.transport, user_id=user_id)

    def saved_screen_stocks(self, user_id: str, sequence: str) -> list[SavedScreenStock]:
        """저장 조건 하나에 일치하는 종목 시세(최대 100건)."""
        return saved_screen_api.fetch_saved_screen_stocks(
            self.transport, user_id=user_id, sequence=sequence
        )

    def watchlist_groups(self, user_id: str) -> list[WatchlistGroup]:
        """HTS 관심종목 그룹 목록."""
        return saved_screen_api.fetch_watchlist_groups(self.transport, user_id=user_id)

    def watchlist(self, user_id: str, group_code: str) -> Watchlist:
        """HTS 관심종목 그룹 하나의 요약과 구성 종목(최대 30개)."""
        return saved_screen_api.fetch_watchlist(
            self.transport, user_id=user_id, group_code=group_code
        )

    def overseas_industries(self, exchange: str) -> list[OverseasIndustry]:
        """해외 거래소의 업종(섹터) 코드 목록. ``exchange`` 는 거래소코드(NAS/NYS/...).

        모의투자는 지원하지 않으며 실전 환경에서만 사용할 수 있다.
        """
        return overseas_market_data_api.fetch_industries(
            self.transport, exchange=exchange, environment=self._environment
        )

    def overseas_industry_stocks(
        self, exchange: str, industry_code: str, *, min_volume: int = 0
    ) -> list[OverseasIndustryStock]:
        """해외 거래소의 한 업종에 속한 종목 시세. 거래량 하한은 0·100·1천·1만·10만·100만·1천만."""
        return overseas_market_data_api.fetch_industry_stocks(
            self.transport,
            exchange=exchange,
            industry_code=industry_code,
            min_volume=min_volume,
            environment=self._environment,
        )

    def index(self, code: str) -> Index:
        """지수/업종 핸들을 만든다. ``code`` 는 업종코드(0001 KOSPI 종합, 1001 KOSDAQ 종합,
        2001 KOSPI200 등)."""
        return Index(self, code)

    def overseas_index(self, symbol: str, *, kind: str = "index") -> OverseasIndex:
        """해외 지수/환율/국채/금선물 핸들을 만든다.

        ``kind`` 는 ``index``/``fx``/``bond``/``gold`` 중 하나이고, ``symbol`` 은 지수코드(예:
        ``.DJI``)다. 국내 :meth:`index` 의 해외판이다.
        """
        division = _OVERSEAS_INDEX_KIND.get(kind)
        if division is None:
            raise KISUsageError(
                f"지원하지 않는 kind: {kind!r} ({'/'.join(_OVERSEAS_INDEX_KIND)})."
            )
        return OverseasIndex(self, symbol, market_division=division)

    def bond(self, code: str) -> Bond:
        """장내채권 핸들을 만든다. ``code`` 는 표준코드(ISIN, 예: KR2033022D33)."""
        return Bond(self, code)

    def elw(self, code: str) -> ELW:
        """ELW(주식워런트증권) 고유 지표 핸들을 만든다. ``code`` 는 ELW 표준코드(6자리, 예: 58J297).

        기본 시세(현재가/호가/체결)는 ``kis.ticker(code)`` 로 조회한다 -- 이 핸들은 민감도(그릭스)·
        변동성·투자지표 같은 ELW 고유 옵션 분석 지표만 얹는다."""
        return ELW(self, code)

    def futures(self, code: str) -> Derivative:
        """지수선물 계약 핸들을 만든다. ``code`` 는 계약코드(예: 101W09)."""
        return Derivative(self, code, market="F")

    def option(self, code: str) -> Derivative:
        """지수옵션 계약 핸들을 만든다. ``code`` 는 계약코드."""
        return Derivative(self, code, market="O")

    def option_expiries(self) -> list[OptionExpiry]:
        """상장된 지수옵션 만기 월물 목록. 옵션 계약코드를 만들기 전에 유효한 만기를 확인하는 용도."""
        return derivatives_api.fetch_option_expiries(self.transport)

    def option_board(self, expiry: str, *, underlying: str = "KOSPI200") -> OptionBoard:
        """한 만기월의 옵션 콜/풋 전광판(행사가별 시세·그릭스). expiry 는 OptionExpiry.year_month."""
        return derivatives_api.fetch_option_board(
            self.transport, expiry=expiry, underlying=underlying
        )

    def option_board_futures(self, *, market_class: str = "MKI") -> list[FuturesBoardQuote]:
        """옵션 전광판 하단의 선물 계약별 현재가·호가·미결제약정·예상체결가."""
        return derivatives_api.fetch_option_board_futures(
            self.transport, market_class=market_class
        )

    def overseas_futures(self, srs_cd: str) -> OverseasDerivative:
        """해외 선물 계약 핸들을 만든다. ``srs_cd`` 는 시리즈코드(예: ESZ25 = E-mini S&P 2025.12)."""
        return OverseasDerivative(self, srs_cd, market="future")

    def overseas_option(self, srs_cd: str) -> OverseasDerivative:
        """해외 옵션 계약 핸들을 만든다. ``srs_cd`` 는 시리즈코드."""
        return OverseasDerivative(self, srs_cd, market="option")

    def overseas_futures_details(
        self, symbols: Sequence[str]
    ) -> list[OverseasDerivativeDetail]:
        """여러 해외 선물 계약의 명세를 한 번에 조회한다(최대 32개, 실전만)."""
        return overseas_derivatives_api.fetch_details(
            self.transport, srs_codes=list(symbols), market="future",
            environment=self._environment,
        )

    def overseas_option_details(
        self, symbols: Sequence[str]
    ) -> list[OverseasDerivativeDetail]:
        """여러 해외 옵션 계약의 명세를 한 번에 조회한다(최대 30개, 실전만)."""
        return overseas_derivatives_api.fetch_details(
            self.transport, srs_codes=list(symbols), market="option",
            environment=self._environment,
        )

    def overseas_derivatives_market_hours(
        self,
        *,
        product_group: str = "",
        asset_class: str = "",
        exchange: str = "",
        kind: str = "%",
    ) -> list[OverseasDerivativeMarketHours]:
        """해외 선물/옵션 상품군별 장운영시간(시장 전체, 계약 무관).

        상품군·클래스·거래소·선물옵션 구분 필터는 생략하면 전체를 조회한다. 모의투자는
        지원하지 않으며 실전 환경에서만 사용할 수 있다.
        """
        return overseas_derivatives_api.fetch_market_hours(
            self.transport,
            environment=self._environment,
            product_group=product_group,
            asset_class=asset_class,
            exchange=exchange,
            kind=kind,
        )

    def overseas_futures_open_interest(
        self, product: str, *, as_of: str | date, mode: str = "quantity"
    ) -> list[OverseasFuturesOpenInterest]:
        """해외선물 상품의 CFTC 미결제약정 수량 또는 증감 추이(실전만)."""
        return overseas_derivatives_api.fetch_open_interest(
            self.transport, product=product, as_of=as_of, mode=mode,
            environment=self._environment,
        )

    def overseas_settlement_dates(self) -> list[OverseasSettlementDate]:
        """해외 각 시장의 현지·국내 결제일자(시장 전체 참조표).

        종목과 무관하게 해외 거래시장별 현지 결제일과 국내 결제일을 조회한다.

        KIS 해외주식 국가별 휴장일 조회를 사용하며 별도 조회 조건은 없다.

        모의투자는 지원하지 않으며 실전 환경에서만 사용할 수 있다.

        결제일이 비어 있거나 유효하지 않으면 해당 날짜는 ``None`` 이다.
        """
        return overseas_reference_api.fetch_settlement_dates(
            self.transport, environment=self._environment
        )

    def overseas_rights(
        self, *, start: str | date, end: str | date, right_type: str = "%%",
        date_basis: str = "local_base", symbol: str = "", product_type: str = "",
    ) -> list[OverseasRight]:
        """기간별 해외증권 배당·증자·합병 등 권리."""
        return overseas_reference_api.fetch_period_rights(
            self.transport, start=start, end=end, right_type=right_type,
            date_basis=date_basis, symbol=symbol, product_type=product_type,
        )

    def overseas_corporate_actions(
        self, country: str, symbol: str, *, start: str | date | None = None,
        end: str | date | None = None,
    ) -> list[OverseasCorporateAction]:
        """해외종목 권리·기업행사 종합 일정."""
        return overseas_reference_api.fetch_corporate_actions(
            self.transport, country=country, symbol=symbol, start=start, end=end
        )

    def overseas_news(
        self, *, country: str = "", exchange: str = "", symbol: str = "",
        date_: str | date | None = None, time: str = "", category: str = "",
    ) -> list[OverseasNewsHeadline]:
        """해외뉴스 종합 제목 피드."""
        return overseas_reference_api.fetch_news(
            self.transport, country=country, exchange=exchange, symbol=symbol,
            date_=date_, time=time, category=category,
        )

    def overseas_breaking_news(
        self, *, symbol: str = "", title: str = "",
        date_: str | date | None = None, time: str = "",
    ) -> list[NewsItem]:
        """해외속보 제목 피드(최대 100건)."""
        return overseas_reference_api.fetch_breaking_news(
            self.transport, symbol=symbol, title=title, date_=date_, time=time
        )

    def overseas_collateral_stocks(
        self, symbol: str, country: str, *, sort: str = "name",
        product_type: str = "", loanable: bool | None = None,
    ) -> list[OverseasCollateralStock]:
        """해외주식 담보대출 가능 여부와 적용 비율."""
        return overseas_reference_api.fetch_collateral_stocks(
            self.transport, symbol=symbol, country=country, sort=sort,
            product_type=product_type, loanable=loanable,
        )

    @property
    def ranking(self) -> RankingQueries:
        """시장 전체 순위 네임스페이스 -- ``kis.ranking.by_change()`` / ``by_volume()`` 등."""
        return RankingQueries(self)

    @property
    def market(self) -> MarketQueries:
        """시장 전체 분석 네임스페이스 -- ``kis.market.investor_flows(market="KOSPI")`` 등
        (종목/순위가 아닌 시장 전체 수급·상태). 종목 단위는 ``kis.ticker(code)``."""
        return MarketQueries(self)

    @property
    def calendar(self) -> CalendarQueries:
        """기업행위 캘린더 네임스페이스 -- ``kis.calendar.dividends(start=..., end=...)`` 등
        (예탁결제원 배당/증자/주총 등 일정). 기간 조회다."""
        return CalendarQueries(self)

    @property
    def overseas_ranking(self) -> OverseasRankingQueries:
        """해외주식 시장 순위 네임스페이스 -- ``kis.overseas_ranking.by_volume(exchange="NAS")`` 등.
        거래소별로 조회한다(``exchange`` = NAS/NYS/HKS/...)."""
        return OverseasRankingQueries(self)

    @property
    def elw_ranking(self) -> ELWRankingQueries:
        """시장 전체 ELW 순위 네임스페이스 -- ``kis.elw_ranking.by_volume()`` /
        ``by_sensitivity()`` 등. 지표가 ELW 고유라 종목 순위와 별도로 둔다."""
        return ELWRankingQueries(self)

    @property
    def elw_screener(self) -> ELWScreenerQueries:
        """ELW 스크리닝 네임스페이스 -- ``kis.elw_screener.underlyings()`` /
        ``by_underlying(code)`` / ``newly_listed(date=...)`` / ``expiring(start=, end=)`` 등."""
        return ELWScreenerQueries(self)

    # --- 계좌 단위 조회(계좌 정보 필요) ------------------------------
    # 계좌 미설정이면 :class:`~kis_openapi.errors.KISUsageError`, 실패/응답 부재/파싱 실패는
    # :class:`~kis_openapi.errors.KISError`.
    def balance(self) -> Balance:
        """계좌의 현금·자산 요약."""
        cano, product_code = self._require_account()
        return account_api.fetch_balance(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def positions(self) -> list[Position]:
        """보유 종목 전체(0수량 잔여 lot 포함)."""
        cano, product_code = self._require_account()
        return account_api.fetch_positions(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def trade_profits(
        self, *, start: str, end: str, symbol: str | None = None, sort: str = "recent"
    ) -> TradeProfitHistory:
        """기간별 매매손익(실현손익) -- 종목별 실현손익과 기간 총계(총실현손익·총수익률·수수료·세금).
        ``start``/``end`` 는 기간(YYYYMMDD), ``symbol`` 없으면 전체, ``sort`` = ``"recent"``/``"oldest"``.
        미실현 평가손익(:meth:`positions`)과 달리 매도로 확정된 손익이다. 금액은 KRW Decimal. **모의투자
        미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return account_api.fetch_trade_profits(
            self._transport, cano=cano, product_code=product_code, environment=self._environment,
            start=start, end=end, symbol=symbol, sort=sort,
        )

    def daily_profits(
        self, *, start: str, end: str, symbol: str | None = None, sort: str = "recent"
    ) -> DailyProfitHistory:
        """기간별 일별 매매손익 합산 -- 하루 단위 매수/매도금액·실현손익·수익률과 기간 총계.
        :meth:`trade_profits` 의 일별 그래뉼래러티 버전(종목 구분 없음). ``start``/``end`` 는
        기간(YYYYMMDD), ``symbol`` 없으면 전체, ``sort`` = ``"recent"``/``"oldest"``. **모의투자
        미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return account_api.fetch_daily_profits(
            self._transport, cano=cano, product_code=product_code, environment=self._environment,
            start=start, end=end, symbol=symbol, sort=sort,
        )

    def pension_deposit(self) -> PensionDeposit:
        """퇴직연금(IRP/DC) 예수금 요약 -- 예수금총액·익일/2익일 정산·결제금액. 일반 위탁계좌 예수금
        (:meth:`balance`)과 별개인 퇴직연금 전용 조회다. 계좌 상품코드가 퇴직연금이어야 정상 응답한다.
        **모의투자 미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return pension_api.fetch_deposit(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def pension_buyable(self, symbol: str, *, limit_price: object | None = None) -> PensionBuyableAmount:
        """퇴직연금 매수가능 여력 -- 주문가능현금·재사용가능금액·최대 매수금액/수량. ``limit_price``
        없으면 시장가 기준. **모의투자 미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`;
        계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return pension_api.fetch_buyable(
            self._transport, cano=cano, product_code=product_code, environment=self._environment,
            symbol=symbol, limit_price=limit_price,
        )

    def pension_balance(self) -> PensionBalance:
        """퇴직연금 잔고 -- 보유종목과 예수금 기준 계좌 요약을 한 조회로. **모의투자 미지원**
        (demo면 :class:`~kis_openapi.errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return pension_api.fetch_balance(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def pension_present_balance(self) -> PensionPresentBalance:
        """퇴직연금 체결기준잔고 -- 체결기준 보유종목과 손익 요약. **모의투자 미지원**(demo면
        :class:`~kis_openapi.errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return pension_api.fetch_present_balance(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def pension_orders(self, *, only_unfilled: bool = False) -> list[PensionOrder]:
        """퇴직연금 당일 주문 내역(체결/미체결). ``only_unfilled=True`` 면 미체결만. **모의투자
        미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return pension_api.fetch_orders(
            self._transport, cano=cano, product_code=product_code, environment=self._environment,
            only_unfilled=only_unfilled,
        )

    def account_rights(self, *, start: str, end: str) -> list[AccountRight]:
        """기간별 계좌 권리현황 -- 이 계좌에 배정/신청/환불된 권리(유상·무상 증자·배당·상환 등).
        ``start``/``end`` 는 기간(YYYYMMDD). 시장 전체 일정을 보는 :attr:`calendar` 와 달리 실제 계좌
        내역이다. **모의투자 미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return account_api.fetch_account_rights(
            self._transport, cano=cano, product_code=product_code, environment=self._environment,
            start=start, end=end,
        )

    def account_assets(self) -> AccountAssets:
        """투자계좌 자산현황 요약 -- 총자산·순자산·예수금·대출·외화까지 계좌 전반. 주식 잔고 요약
        :meth:`balance` 보다 넓다. 자산군별 내역은 위치기반이라 반환값 ``_raw`` 로만 둔다. **모의투자
        미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return account_api.fetch_account_assets(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def reserved_orders(
        self, *, start: str, end: str, process: str = "all"
    ) -> list[ReservedOrder]:
        """예약주문 목록 -- 다음 영업일 동시호가 등에 걸어둔 예약주문. ``start``/``end`` 는 예약주문일자
        기간(YYYYMMDD), ``process`` = ``"all"``/``"processed"``/``"unprocessed"``. 각 건의 ``sequence``
        (예약순번)로 정정·취소한다. **모의투자 미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`;
        계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return reserved_orders_api.fetch_reserved_orders(
            self._transport, cano=cano, product_code=product_code, environment=self._environment,
            start=start, end=end, process=process,
        )

    def open_orders(self) -> list[OpenOrder]:
        """미체결(정정·취소 가능) 주문 목록. 브로커 측 뷰라 우리 ``client_order_id`` 는 없고
        KIS 주문번호로 식별한다. 정정/취소 전 ``cancelable_quantity`` 를 확인하라. **모의투자
        미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return account_api.fetch_open_orders(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def overseas_positions(self, *, market: str) -> list[OverseasPosition]:
        """해외 보유 종목(거래소 그룹+통화별). ``market`` = ``"US"``/``"HK"``/``"CN_SH"``/``"CN_SZ"``/
        ``"JP"``/``"VN_HN"``/``"VN_HCM"``. 금액은 종목 통화의 :class:`~kis_openapi.money.Money`.

        국내와 달리 해외는 시장/통화별로 조회하므로 ``market`` 을 지정한다(계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return overseas_account.fetch_positions(
            self._transport, cano=cano, product_code=product_code,
            environment=self._environment, market=market,
        )

    def overseas_balance(self, *, market: str) -> OverseasBalance:
        """해외 계좌 손익 요약(시장/통화별) -- 매입금액·평가/실현/총손익·총수익률을 :class:`~kis_openapi.
        money.Money` 로. ``market`` 은 :meth:`overseas_positions` 와 같다(계좌 정보 필요). 예수금(현금)은
        별도다."""
        cano, product_code = self._require_account()
        return overseas_account.fetch_balance(
            self._transport, cano=cano, product_code=product_code,
            environment=self._environment, market=market,
        )

    def overseas_buyable(self, symbol: str, *, exchange: str, price: object) -> OverseasBuyableAmount:
        """해외주식 매수가능금액. ``exchange`` 는 시세 거래소코드(NAS/NYS/AMS/HKS/SHS/SZS/TSE/HNX/HSX),
        ``price`` 는 의도한 주문단가. 국내 :meth:`~kis_openapi.ticker.Ticker.buyable` 의 해외판이며
        외화·통합 기준 주문가능금액·최대수량을 :class:`~kis_openapi.money.Money` 로 준다(계좌 정보
        필요). **매수 시 수량단위 절사가 필요**하다."""
        cano, product_code = self._require_account()
        return overseas_account.fetch_buyable(
            self._transport, cano=cano, product_code=product_code,
            environment=self._environment, symbol=symbol, exchange=exchange, price=price,
        )

    def overseas_foreign_margin(self) -> list[OverseasForeignMargin]:
        """통화별 해외증거금 -- 계좌의 통화별 외화 예수금·증거금·주문가능금액. 금액은 각 통화의
        :class:`~kis_openapi.money.Money`. **모의투자 미지원**(demo면 :class:`~kis_openapi.errors.
        KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return overseas_account.fetch_foreign_margin(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def overseas_transactions(
        self, *, start: str, end: str, symbol: str | None = None, side: str = "all"
    ) -> list[OverseasTransaction]:
        """해외주식 일별 거래내역(매매·결제·수수료). ``start``/``end`` 는 등록일자 기간(YYYYMMDD),
        ``symbol`` 없으면 전체 종목, ``side`` = ``"all"``/``"sell"``/``"buy"``. 외화 금액은 거래
        통화의 :class:`~kis_openapi.money.Money`. **모의투자 미지원**(demo면 :class:`~kis_openapi.
        errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return overseas_account.fetch_transactions(
            self._transport, cano=cano, product_code=product_code,
            environment=self._environment, start=start, end=end, symbol=symbol, side=side,
        )

    def overseas_open_orders(self, *, market: str) -> list[OverseasOpenOrder]:
        """해외 미체결(열린) 주문 목록(시장별). 거래소 주문번호·미체결 잔량을 준다. **모의투자
        미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`; 계좌 정보 필요)."""
        cano, product_code = self._require_account()
        return overseas_account.fetch_open_orders(
            self._transport, cano=cano, product_code=product_code,
            environment=self._environment, market=market,
        )

    def portfolio(self) -> Portfolio:
        """현금·자산 요약과 보유 종목을 한 번의 조회로 함께."""
        cano, product_code = self._require_account()
        return account_api.fetch_portfolio(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def reconcile(self, client_order_id: str) -> ExecutionReport | None:
        """미확인 주문(타임아웃 등)의 실제 상태를 브로커에 재조회한다 -- **보수적**.

        완료 리포트가 있으면 반환. in-flight 면 저장된 주문 종류에 따라 확인처를 고른다: 국내 즉시
        주문은 일별체결조회, 해외 주문은 해외 체결내역, 예약주문은 예약주문조회. 어느 쪽이든 정확히
        1건이면 확정, 모호(0/다건)하면 미접수로 단정하지 않는다(``None`` 또는 :class:`~kis_openapi.
        errors.KISError`). 모르는 id 는 :class:`~kis_openapi.errors.KISUsageError`. 재조회 자체가
        시간초과면 :class:`~kis_openapi.errors.OrderTimeoutError`(in-flight 유지, 잠시 후 재시도).
        """
        cano, product_code = self._require_account()
        # 해외 주문의 미확인(in-flight) 재조회는 국내 일별체결조회가 아니라 해외 체결내역으로 확인해야
        # 한다(엉뚱한 미접수 판정 방지) -- 지문의 거래소로 국내/해외 경로를 가른다. 완료 리포트가 있으면
        # 어느 엔진이든 그대로 반환한다.
        fingerprint = self._store.fingerprint_for(client_order_id)
        if fingerprint is not None and fingerprint.exchange.startswith("action:"):
            raise KISUsageError(
                "정정·취소 요청은 자동 reconcile을 지원하지 않는다. 원주문 상태를 조회해 확인하라."
            )
        if fingerprint is not None and fingerprint.exchange == "reserved":
            # 예약주문은 일별체결이 아니라 예약주문조회로 확인한다.
            return reserved_orders_api.reconcile_reserved_order(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
            )
        if fingerprint is not None and overseas_orders_engine.is_overseas_exchange(fingerprint.exchange):
            return overseas_orders_engine.reconcile(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
            )
        return orders_engine.reconcile(
            self._transport, self._store, client_order_id,
            cano=cano, product_code=product_code, environment=self._environment,
        )

    def cancel_order(
        self, client_order_id: str, *, quantity: object | None = None,
        request_id: str | None = None,
    ) -> ExecutionReport:
        """접수된 국내·해외 주식 주문의 미체결 수량을 취소한다."""
        return self._change_order(
            client_order_id, action="cancel", quantity=quantity, price=None,
            request_id=request_id,
        )

    def replace_order(
        self, client_order_id: str, *, price: object, quantity: object | None = None,
        request_id: str | None = None,
    ) -> ExecutionReport:
        """접수된 국내·해외 주식 주문의 가격 또는 잔량을 정정한다."""
        return self._change_order(
            client_order_id, action="replace", quantity=quantity, price=price,
            request_id=request_id,
        )

    def _change_order(
        self, client_order_id: str, *, action: str, quantity: object | None,
        price: object | None, request_id: str | None,
    ) -> ExecutionReport:
        cano, product_code = self._require_account()
        fingerprint = self._store.fingerprint_for(client_order_id)
        report = self._store.report_for(client_order_id)
        if fingerprint is None or report is None:
            raise KISUsageError(f"확정된 원주문을 찾을 수 없다: {client_order_id!r}")
        original_quantity = Decimal(fingerprint.quantity)
        remaining_quantity = original_quantity - report.filled_quantity
        change_quantity = remaining_quantity if quantity is None else Decimal(str(quantity))
        change_price = None if price is None else Decimal(str(price))
        builder = None
        if overseas_orders_engine.is_overseas_exchange(fingerprint.exchange):
            builder = overseas_orders_engine.make_change_request
        return orders_engine.submit_change(
            self._transport, self._store,
            original_client_order_id=client_order_id,
            request_id=request_id or mint_client_order_id(),
            action=action,
            quantity=change_quantity,
            price=change_price,
            cano=cano,
            product_code=product_code,
            environment=self._environment,
            build_request=builder,
        )

    def _place_order(self, order: Order) -> ExecutionReport:
        """주문을 안전 엔진에 넘겨 전송한다(Ticker.buy/sell 이 호출). 계좌 정보 필요.

        국내/해외 모두 같은 안전 코어(이중체결 방지·재시도 금지)를 쓰되, 와이어 요청 조립기만
        시장별로 바꾼다. 해외 주문엔 아직 사전 리스크 게이트가 없어(참조가가 국내 시세 기반),
        ``risk`` 를 켠 세션에서 해외 주문을 내면 명확히 거부한다."""
        cano, product_code = self._require_account()
        build_request = None
        risk = self._risk
        if overseas_orders_engine.is_overseas_exchange(order.exchange):
            if risk is not None:
                raise KISUsageError(
                    "해외 주문엔 사전 리스크 게이트가 아직 미지원이다 -- risk 없는 세션에서 내거나 "
                    "국내 주문에만 risk 를 쓰라."
                )
            build_request = overseas_orders_engine.make_order_request
        elif order.credit_type is not None:
            # 국내 신용주문 -- 안전 코어(place)는 공유, 와이어 조립기만 신용용으로. risk 는 국내라
            # 그대로 적용된다(참조가=국내 시세).
            build_request = orders_engine._make_credit_order_request
        return orders_engine.place(
            self._transport, self._store, order,
            cano=cano, product_code=product_code, environment=self._environment,
            orderable=self._orderable, risk=risk, build_request=build_request,
        )

    def _place_reserved_order(
        self, *, symbol: str, side: Side, quantity: object, price: object | None,
        end_date: str | None, client_order_id: str | None,
    ) -> ExecutionReport:
        """예약주문을 예약 안전 엔진에 넘긴다(Ticker.reserve_buy/sell 이 호출). 계좌 정보 필요.

        즉시주문 안전 코어와 별개 흐름이되 dedup(OrderStore)·재시도 금지·보수적 재조회·주문가능 계좌
        가드는 공유한다. risk 게이트는 예약주문엔 적용하지 않는다(집행이 향후라 현재가 기준 참조가
        의미 없음)."""
        cano, product_code = self._require_account()
        return reserved_orders_api.place_reserved_order(
            self._transport, self._store,
            symbol=symbol, side=side, quantity=quantity, price=price, end_date=end_date,
            client_order_id=client_order_id or mint_client_order_id(), orderable=self._orderable,
            cano=cano, product_code=product_code, environment=self._environment,
        )

    def _require_account(self) -> tuple[str, str]:
        """계좌 식별정보를 돌려주거나, 없으면 :class:`KISUsageError`."""
        if self._cano is None or self._product_code is None:
            raise KISUsageError(
                "계좌 조회/주문에는 계좌 정보가 필요하다 -- "
                "KISClient(..., account='12345678-01') 로 생성하라."
            )
        return self._cano, self._product_code


def _split_account(account: str | None) -> tuple[str, str] | tuple[None, None]:
    """``"12345678-01"`` -> (계좌번호 ``"12345678"``, 상품코드 ``"01"``). ``None`` 은 (None, None)."""
    if account is None:
        return None, None
    cano, _, product_code = account.partition("-")
    if not cano or not product_code or "-" in product_code:
        raise KISUsageError(
            f"account 형식은 '계좌번호-상품코드'여야 한다(예: '12345678-01'): {account!r}"
        )
    return cano, product_code
