"""국내 자산군 네임스페이스 -- ``kis.domestic`` (:class:`DomesticNamespace`) 와 그 계좌 하위
(:class:`DomesticAccount`, ``kis.account.domestic``).

세션 :class:`~kis_trader.client.KISClient` 아래 국내 주식·지수·채권·ELW·파생의 시세/계좌/순위/
시장/일정 행위를 모은다. 각 메서드는 세션이 쥔 전송/계좌/환경으로 국내 엔진을 직접 호출한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import KISUsageError
from ._engine import account as account_api
from ._engine import derivatives as derivatives_api
from ._engine import market_data as market_data_api
from ._engine import product as product_api
from ._engine import reserved_orders as reserved_orders_api
from ._engine import saved_screen as saved_screen_api
from .bond import Bond
from .calendar import CalendarQueries
from .derivative import FuturesContract, OptionContract
from .elw import ELW
from .elw_ranking import ELWRankingQueries
from .elw_screener import ELWScreenerQueries
from .index import Index
from .market import MarketQueries
from .ranking import RankingQueries
from .stock import DomesticStock

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .._internal._masters import DomesticListing, SearchMarket
    from .._literals import Numeric
    from ..client import KISClient
    from ..instrument import DomesticBoard
    from ..open_order import OpenOrder
    from ..order import Right, Side
    from ..quote import Quote
    from ..reserved_order import ReservedOrder
    from .entities.account_reports import IntegratedMargin, RealizedProfitBalance
    from .entities.account_right import AccountRight
    from .entities.balance import AccountAssets, Balance, Portfolio, Position
    from .entities.derivative import FuturesBoardQuote, OptionBoard, OptionExpiry
    from .entities.product import ProductInfo
    from .entities.saved_screen import SavedScreen, SavedScreenStock, Watchlist, WatchlistGroup
    from .entities.trade_profit import DailyProfitHistory, TradeProfitHistory


class DomesticAccount:
    """``kis.account.domestic`` -- 국내 계좌 조회·계좌 단위 주문(잔고/손익/예약주문).

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
        self, sequence: str, *, symbol: str, side: Side, quantity: Numeric,
        limit_price: Numeric | None = None, end_date: str | None = None, order_date: str | None = None,
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


class DomesticNamespace:
    """``kis.domestic`` -- 국내 자산(주식·지수·채권·ELW·파생) 시세/계좌/순위/시장/일정."""

    def __init__(self, client: KISClient) -> None:
        self._c = client

    # -- 종목/상품 핸들 --
    def stock(self, code: str, *, market: DomesticBoard | None = None) -> DomesticStock:
        """국내 종목/ETF 핸들. 시장은 심볼로 자동 판별한다(6자리 숫자 -> KRX; ``market`` 로 보드 지정 가능)."""
        return DomesticStock(self._c, code, market=market)

    def search(self, query: str, *, market: SearchMarket = "all") -> list[DomesticListing]:
        """국내 상장 종목을 **이름(부분/정확)** 또는 6자리 코드로 찾아 후보를 돌려준다.

        이름은 모호할 수 있어("삼성전자" 는 "삼성전자우" 도 매치) **후보를 하나로 좁히지 않고 모두**
        준다 -- 코드를 골라 :meth:`stock` 에 넘겨라(``stock()`` 은 이름을 안 받는다; 조용한 오확정 방지).
        ``market`` 은 ``"all"``/``"KOSPI"``/``"KOSDAQ"``. 첫 호출은 KOSPI/KOSDAQ 마스터를 받아 캐시한다
        (이후는 캐시). 빈 검색어/잘못된 ``market`` 은 :class:`~kis_trader.errors.KISUsageError`."""
        return self._c._ensure_domestic_index().search(query, market=market)

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

    def option(self, code: str, *, right: Right | None = None) -> OptionContract:
        """지수옵션 계약 핸들. ``code`` 는 계약코드. ``right``(call/put)은 발주(매수/매도)에 필요하다 --
        조회(시세·호가 등)만 할 땐 생략 가능하고, 발주하려면 ``option(code, right="call")`` 처럼 지정한다."""
        return OptionContract(self._c, code, right)

    # -- 파생 보드/조회 --
    def option_expiries(self) -> list[OptionExpiry]:
        """상장된 지수옵션 만기 월물 목록. 옵션 계약코드를 만들기 전에 유효한 만기를 확인하는 용도."""
        return derivatives_api.fetch_option_expiries(self._c.transport)

    def option_board(
        self, expiry: OptionExpiry | str, *, underlying: str = "KOSPI200"
    ) -> OptionBoard:
        """한 만기월의 옵션 콜/풋 전광판(행사가별 시세·그릭스). ``expiry`` 는 :meth:`option_expiries`
        가 준 :class:`~kis_trader.domestic.entities.derivative.OptionExpiry`(그대로 넘기면 된다) 또는
        그 만기 년월 문자열 ``"YYYYMM"``(예: ``"202609"``) -- 만기코드(``OptionExpiry.code``)가 아니다."""
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
        :meth:`~kis_trader.overseas.namespace.OverseasNamespace.quotes`."""
        requests = [
            item if isinstance(item, tuple) else (market, item) for item in symbols
        ]
        return market_data_api.fetch_multi_quotes(self._c.transport, requests=requests)

    def product_info(self, symbol: str, *, product_type: str = "300") -> ProductInfo:
        """상품 공통 등록·판매 기본정보. 상품유형 기본값 ``"300"`` 은 국내 주식군이다."""
        return product_api.fetch_product_info(
            self._c.transport, symbol=symbol, product_type=product_type
        )

    def _resolve_user_id(self, user_id: str | None) -> str:
        """조건검색·관심종목 조회의 ``user_id``(HTS 아이디)를 정한다. 명시하면 그대로, 없으면
        세션의 ``hts_id``(사용자당 하나, 모든 프로필 공유)를 쓴다. 둘 다 없으면 :class:`KISUsageError`."""
        resolved = user_id if user_id is not None else self._c.hts_id
        if not resolved:
            raise KISUsageError(
                "user_id(HTS 아이디)가 필요하다 -- 직접 넘기거나 세션의 hts_id 를 저장하라 "
                "(KISConfig.set_hts_id('...'); 또는 credentials.json 최상위 \"hts_id\" / KIS_HTS_ID 환경변수)."
            )
        return resolved

    def saved_screens(self, user_id: str | None = None) -> list[SavedScreen]:
        """HTS에 서버 저장된 종목검색 조건 목록. ``user_id`` 생략 시 세션의 ``hts_id`` 사용."""
        return saved_screen_api.fetch_saved_screens(self._c.transport, user_id=self._resolve_user_id(user_id))

    def saved_screen_stocks(self, sequence: str, *, user_id: str | None = None) -> list[SavedScreenStock]:
        """저장 조건 하나에 일치하는 종목 시세(최대 100건). ``user_id`` 생략 시 세션의 ``hts_id``."""
        return saved_screen_api.fetch_saved_screen_stocks(
            self._c.transport, user_id=self._resolve_user_id(user_id), sequence=sequence
        )

    def watchlist_groups(self, user_id: str | None = None) -> list[WatchlistGroup]:
        """HTS 관심종목 그룹 목록. ``user_id`` 생략 시 세션의 ``hts_id`` 사용."""
        return saved_screen_api.fetch_watchlist_groups(self._c.transport, user_id=self._resolve_user_id(user_id))

    def watchlist(self, group_code: str, *, user_id: str | None = None) -> Watchlist:
        """HTS 관심종목 그룹 하나의 요약과 구성 종목(최대 30개). ``user_id`` 생략 시 세션의 ``hts_id``."""
        return saved_screen_api.fetch_watchlist(
            self._c.transport, user_id=self._resolve_user_id(user_id), group_code=group_code
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


