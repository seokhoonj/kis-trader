"""국내 종목 핸들 -- :class:`DomesticStock`.

한 국내 종목/ETF 에 대해 행위를 시키는 핸들이다: ``kis.domestic.stock("005930").quote()`` 처럼.
시세·시세분석·재무·투자자수급·ETF·주문(현금/신용/예약)까지 국내 전용 표면을 준다. 해외 전용 조회는
애초에 이 핸들에 없다(:class:`~kis_trader.overseas.stock.OverseasStock` 로 분리 -- 잘못된 조합은
런타임 오류가 아니라 타입체커가 먼저 잡는다).

시세는 세션 인증만으로 되고, 주문은 세션에 묶인 계좌 + 내부 안전엔진(이중체결 방지·재시도 금지)을
쓴다. 핸들은 세션이 만들어 준다(``kis.domestic.stock``) -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from .._stock_base import _StockBase
from ..bar import Bar, Interval
from ..errors import KISUsageError
from ..instrument import DomesticBoard, resolve_market
from ..order import (
    _LIMIT_BASED_DIVISIONS,
    _PRICELESS_DIVISIONS,
    CreditType,
    DomesticDivision,
    Order,
    Side,
    TimeInForce,
)
from ..order_book import OrderBook
from ..orderable import BuyableAmount, SellableQuantity
from ..quote import Quote
from ..report import ExecutionReport
from ..trade import Trade
from ._engine import account as account_api
from ._engine import analysis as analysis_api
from ._engine import etf as etf_api
from ._engine import finance as finance_api
from ._engine import market_data
from .entities.after_hours import AfterHoursConclusion, AfterHoursDailyPrice, AfterHoursQuote
from .entities.analysis import (
    AnalystOpinion,
    CreditBalancePoint,
    DailyTradeVolumePoint,
    EarningsEstimate,
    ExpectedPricePoint,
    ForeignNetBuyPoint,
    IntradayExecutions,
    LoanPoint,
    RecentPricePoint,
    ShortSalePoint,
    TradeAmountBand,
    VolumeProfile,
)
from .entities.broker import BrokerActivitySummary, BrokerDailyActivity, BrokerTradeTicks
from .entities.etf import (
    ETFNAV,
    ETFComponents,
    ETFNAVComparison,
    ETFNAVHistoryPoint,
    ETFNAVMinutePoint,
    ETFOrderBook,
)
from .entities.financials import (
    BalanceSheet,
    FinancialRatio,
    GrowthRatio,
    IncomeStatement,
    OtherRatio,
    ProfitabilityRatio,
    StabilityRatio,
)
from .entities.investor import DetailedInvestorHistory, InvestorEstimate, InvestorFlow
from .entities.program import DailyProgramTradePoint, ProgramTradePoint
from .entities.stock_info import StockProfile, StockStatus

if TYPE_CHECKING:
    from .._literals import Numeric
    from ..client import KISClient


class DomesticStock(_StockBase):
    """국내 종목/ETF 핸들 -- 시세·시세분석·재무·투자자수급·ETF·주문. ``kis.domestic.stock(code)``."""

    is_overseas = False
    market: DomesticBoard

    def __init__(
        self, client: KISClient, symbol: str, *, market: DomesticBoard | None = None
    ) -> None:
        super().__init__(client, symbol)
        self.market = resolve_market(symbol, market=market)

    # --- 시세 ---
    def quote(self) -> Quote:
        """현재가 스냅샷."""
        return market_data.fetch_quote(self._client.transport, symbol=self.symbol, market=self.market)

    def profile(self) -> StockProfile:
        """종목 기본정보(이름·상장주식수·자본금·액면가·업종·상장일)."""
        return market_data.fetch_stock_profile(self._client.transport, symbol=self.symbol)

    def status(self) -> StockStatus:
        """현재가와 거래·규제·경고 상태."""
        return market_data.fetch_stock_status(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    def intraday_executions(self, *, at: str = "235959") -> IntradayExecutions:
        """기준시각 이전의 당일 체결·최우선호가·체결강도."""
        return market_data.fetch_intraday_executions(
            self._client.transport, symbol=self.symbol, market=self.market, at=at
        )

    def bars(
        self, interval: Interval = "1d", *, start: str | date | None = None,
        end: str | date | None = None, adjusted: bool = True, max_bars: int | None = None,
    ) -> list[Bar]:
        """OHLCV 바(과거->현재). ``interval="1m"`` 은 당일 1분봉(``start``/``end``/``adjusted`` 무시,
        ``max_bars`` 로 최근 N개), ``1d``/``1wk``/``1mo`` 는 [start, end] 기간봉(``start`` 필요).

        ``start`` > ``end``, ``max_bars`` <= 0, 기간봉인데 ``start`` 없음이면 :class:`~kis_trader.
        errors.KISUsageError`. 응답 손상(비배열 output2)·페이지 상한 초과는 :class:`~kis_trader.errors.KISError`."""
        return market_data.fetch_bars(
            self._client.transport, symbol=self.symbol, market=self.market,
            interval=interval, start=start, end=end, adjusted=adjusted, max_bars=max_bars,
        )

    def recent_prices(
        self, *, interval: Interval = "1d", adjusted: bool = True
    ) -> list[RecentPricePoint]:
        """최근 30개 일·주·월 주가와 외국인 수급·거래량·권리락 보조지표."""
        return market_data.fetch_recent_prices(
            self._client.transport, symbol=self.symbol, market=self.market,
            interval=interval, adjusted=adjusted,
        )

    def minute_bars_on(self, day: str | date, *, max_bars: int | None = None) -> list[Bar]:
        """특정 과거일 ``day`` 의 1분봉(과거->현재). 당일만 주는 :meth:`bars`\\ ``(interval="1m")`` 과
        달리 지난 영업일의 분봉을 backfill 한다. ``max_bars`` 로 최근 N개."""
        return market_data.fetch_minute_bars_on(
            self._client.transport, symbol=self.symbol, market=self.market,
            day=day, max_bars=max_bars,
        )

    def order_book(self) -> OrderBook:
        """호가창 스냅샷(10단계)."""
        return market_data.fetch_order_book(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    def trades(self) -> list[Trade]:
        """최근 체결 목록(time & sales; 최신순)."""
        return market_data.fetch_trades(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    # --- 투자자 수급 ---
    def investor_flows(self) -> list[InvestorFlow]:
        """일자별 투자자(개인/외국인/기관) 매매동향(최신순)."""
        return market_data.fetch_investor_flows(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    def detailed_investor_history(
        self, *, as_of: str | date | None = None
    ) -> DetailedInvestorHistory:
        """세부 투자자 주체별 매수·매도·순매수 일별 내역(``as_of`` 기준)."""
        return market_data.fetch_detailed_investor_history(
            self._client.transport, symbol=self.symbol, market=self.market, as_of=as_of
        )

    def broker_activity(self) -> BrokerActivitySummary:
        """매도/매수 상위 회원사(증권사) 매매 비중."""
        return market_data.fetch_broker_activity(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    def broker_daily_activity(
        self, member_code: str, *, start: str | date, end: str | date
    ) -> list[BrokerDailyActivity]:
        """회원사 하나의 종목 일별 매수·매도 내역."""
        return market_data.fetch_broker_daily_activity(
            self._client.transport, symbol=self.symbol, member_code=member_code,
            start=start, end=end,
        )

    def broker_trade_ticks(
        self, *, member_code: str = "99999", min_volume: int = 0
    ) -> BrokerTradeTicks:
        """회원사 실시간 매매동향 체결 틱."""
        return market_data.fetch_broker_trade_ticks(
            self._client.transport, symbol=self.symbol,
            member_code=member_code, min_volume=min_volume,
        )

    # --- 시간외 단일가 ---
    def after_hours_quote(self) -> AfterHoursQuote:
        """시간외 단일가 스냅샷(예상체결가·최우선호가)."""
        return market_data.fetch_after_hours_quote(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    def after_hours_conclusions(self) -> list[AfterHoursConclusion]:
        """시간외 단일가 세션의 시간별 체결(시각 리스트). 세션 밖이면 빈 리스트일 수 있다."""
        return market_data.fetch_after_hours_conclusions(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    def after_hours_daily(self) -> list[AfterHoursDailyPrice]:
        """시간외 단일가 세션의 일자별 종가(최근->과거). 세션 밖이면 빈 리스트일 수 있다."""
        return market_data.fetch_after_hours_daily(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    def after_hours_order_book(self) -> OrderBook:
        """시간외 단일가 세션의 10단계 호가창 스냅샷. 세션 밖이면 단계가 비어 올 수 있다."""
        return market_data.fetch_after_hours_order_book(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    # --- 프로그램매매 ---
    def program_trades(self) -> list[ProgramTradePoint]:
        """장중 시간대별 프로그램매매 흐름(매수/매도/순매수 수량·금액, 시간 순)."""
        return market_data.fetch_program_trades(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    def daily_program_trades(
        self, *, as_of: str | date | None = None
    ) -> list[DailyProgramTradePoint]:
        """종목별 프로그램매매 일별 추이."""
        return market_data.fetch_daily_program_trades(
            self._client.transport, symbol=self.symbol, as_of=as_of
        )

    def investor_estimate(self) -> list[InvestorEstimate]:
        """장중 투자자(외국인/기관) 순매수 추정(시간 순, 확정 아닌 가추정)."""
        return market_data.fetch_investor_estimate(self._client.transport, symbol=self.symbol)

    # --- 재무제표/비율 ---
    def balance_sheet(self, *, quarterly: bool = False) -> list[BalanceSheet]:
        """결산기별 대차대조표(최근->과거). ``quarterly=True`` 면 분기, 아니면 연간."""
        return finance_api.fetch_balance_sheet(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def income_statement(self, *, quarterly: bool = False) -> list[IncomeStatement]:
        """결산기별 손익계산서(최근->과거). ``quarterly=True`` 면 분기, 아니면 연간."""
        return finance_api.fetch_income_statement(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def financial_ratios(self, *, quarterly: bool = False) -> list[FinancialRatio]:
        """결산기별 주요 재무비율(ROE·EPS·BPS·부채비율·유보율·증가율; 최근->과거)."""
        return finance_api.fetch_financial_ratios(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def profitability_ratios(self, *, quarterly: bool = False) -> list[ProfitabilityRatio]:
        """결산기별 수익성비율(ROA·ROE·순이익률·총이익률; 최근->과거)."""
        return finance_api.fetch_profitability_ratios(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def stability_ratios(self, *, quarterly: bool = False) -> list[StabilityRatio]:
        """결산기별 안정성비율(부채비율·차입금의존도·유동비율·당좌비율; 최근->과거)."""
        return finance_api.fetch_stability_ratios(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def growth_ratios(self, *, quarterly: bool = False) -> list[GrowthRatio]:
        """결산기별 성장성비율(매출/영업이익/자기자본/총자산 증가율; 최근->과거)."""
        return finance_api.fetch_growth_ratios(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def other_ratios(self, *, quarterly: bool = False) -> list[OtherRatio]:
        """결산기별 기타주요비율(EVA·EBITDA·EV/EBITDA; 최근->과거)."""
        return finance_api.fetch_other_ratios(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    # --- 시세분석(신용/공매도/대차/의견/추정/매물대) ---
    def credit_balance_trend(
        self, *, as_of: str | date | None = None
    ) -> list[CreditBalancePoint]:
        """일별 신용잔고(융자/대주) 추이(기준일에서 과거로). ``as_of`` 없으면 오늘 기준."""
        return analysis_api.fetch_credit_balance_trend(
            self._client.transport, symbol=self.symbol, as_of_date=as_of
        )

    def short_sale_trend(
        self, *, start: str | date | None = None, end: str | date | None = None
    ) -> list[ShortSalePoint]:
        """일별 공매도 추이(기간 [start, end], 최근->과거). 기본은 최근."""
        return analysis_api.fetch_short_sale_trend(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    def volume_profile(self) -> VolumeProfile:
        """가격대별 거래량 분포(매물대)와 요약(현재가·가중평균가·상장주수)."""
        return analysis_api.fetch_volume_profile(self._client.transport, symbol=self.symbol)

    def foreign_net_buy_trend(self) -> list[ForeignNetBuyPoint]:
        """이 종목의 장중 외국계(외국인 회원사) 순매수 추이(시간대별)."""
        return analysis_api.fetch_foreign_net_buy_trend(
            self._client.transport, symbol=self.symbol
        )

    def loan_trend(
        self, *, start: str | date | None = None, end: str | date | None = None
    ) -> list[LoanPoint]:
        """일별 대차거래(대여) 추이(기간 [start, end], 최근->과거). 대차잔고는 공매도 공급 대리지표."""
        return analysis_api.fetch_loan_trend(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    def analyst_opinions(
        self, *, start: str | date | None = None, end: str | date | None = None
    ) -> list[AnalystOpinion]:
        """기간 [start, end] 의 애널리스트 투자의견·목표주가 시계열(최근->과거). start 미지정이면 최근 30일."""
        return analysis_api.fetch_analyst_opinions(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    def earnings_estimate(self) -> EarningsEstimate:
        """월간 추정 손익계산서·투자지표 스냅샷. 리서치 추정 대상 종목만 유효하다."""
        return analysis_api.fetch_earnings_estimate(self._client.transport, symbol=self.symbol)

    def daily_trade_volume(
        self, *, start: str | date | None = None, end: str | date | None = None
    ) -> list[DailyTradeVolumePoint]:
        """일별 매수/매도 체결량 추이(기간 [start, end], 최근->과거). start 미지정이면 최근 30일."""
        return analysis_api.fetch_daily_trade_volume(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    def expected_price_trend(self, *, exclude_zero_volume: bool = False) -> list[ExpectedPricePoint]:
        """동시호가 예상 체결가 추이(시각 리스트, 최근->과거). ``exclude_zero_volume=True`` 면 체결량 0 시각 제외."""
        return analysis_api.fetch_expected_price_trend(
            self._client.transport, symbol=self.symbol, exclude_zero_volume=exclude_zero_volume
        )

    def trade_amount_bands(self) -> list[TradeAmountBand]:
        """당일 체결금액대별 매매비중(금액대 리스트). 각 금액대의 매수/매도/순매수 거래량·비율·건수."""
        return analysis_api.fetch_trade_amount_bands(self._client.transport, symbol=self.symbol)

    # --- ETF/ETN (이 종목이 ETF/ETN 일 때만 유효; 아니면 서버가 거부) ---
    def nav(self) -> ETFNAV:
        """ETF/ETN 순자산가치(NAV) 스냅샷(NAV·괴리율·추적오차율·순자산총액). 시장 체결가는 :meth:`quote`."""
        return etf_api.fetch_etf_nav(self._client.transport, symbol=self.symbol)

    def nav_comparison(self) -> ETFNAVComparison:
        """ETF 시장가격과 NAV의 당일 OHLC 비교."""
        return etf_api.fetch_etf_nav_comparison(self._client.transport, symbol=self.symbol)

    def nav_intraday(self, *, interval_minutes: int = 1) -> list[ETFNAVMinutePoint]:
        """최근 30개 ETF 시장가격-NAV 분별 비교."""
        return etf_api.fetch_etf_nav_intraday(
            self._client.transport, symbol=self.symbol, interval_minutes=interval_minutes
        )

    def etf_order_book(self) -> ETFOrderBook:
        """ETF 10단계 호가와 LP 잔량·잔량 증감·중간가."""
        return etf_api.fetch_etf_order_book(self._client.transport, symbol=self.symbol)

    def etf_components(self) -> ETFComponents:
        """ETF 구성종목(PDF)과 ETF 요약. ``.summary`` 는 ETF 시세·NAV·구성 규모, ``.components`` 는
        각 구성종목의 시세·ETF 내 구성 비중·평가금액."""
        return etf_api.fetch_etf_components(self._client.transport, symbol=self.symbol)

    def nav_history(self, *, start: str | date, end: str | date) -> list[ETFNAVHistoryPoint]:
        """일별 NAV-가격 추이(과거->현재). 각 거래일의 종가·NAV·괴리율로 프리미엄/디스카운트 추이를 본다."""
        return etf_api.fetch_etf_nav_history(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    # --- 계좌 단위(매수/매도 여력; 계좌 정보 필요) ---
    def buyable(self, *, limit_price: Numeric | None = None) -> BuyableAmount:
        """이 종목의 매수가능 여력(현금 기준·미수 포함 최대). ``limit_price`` 없으면 시장가 기준.

        계좌 정보 없이 생성한 세션이면 :class:`~kis_trader.errors.KISUsageError`.
        """
        cano, product_code = self._client._require_account()
        return account_api.fetch_buyable_amount(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, symbol=self.symbol, limit_price=limit_price,
        )

    def credit_buyable(
        self, *, credit_type: CreditType = "21", limit_price: Numeric | None = None
    ) -> BuyableAmount:
        """이 종목의 신용(융자/대주) 매수가능 여력. ``credit_type`` 신용유형(기본 21 자기융자신규,
        22 유통대주신규/23 유통융자신규/24 자기대주신규/25~28 각 상환), ``limit_price`` 없으면 시장가
        기준. 현금 :meth:`buyable` 과 같은 :class:`~kis_trader.orderable.BuyableAmount` 를 주며, 신용
        전용 필드는 ``_raw`` 로 본다. **모의투자 미지원**."""
        cano, product_code = self._client._require_account()
        return account_api.fetch_credit_buyable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, symbol=self.symbol,
            credit_type=credit_type, limit_price=limit_price,
        )

    def sellable(self) -> SellableQuantity:
        """이 종목의 매도가능 수량. **모의투자 미지원**(demo면 :class:`~kis_trader.errors.KISUsageError`)."""
        cano, product_code = self._client._require_account()
        return account_api.fetch_sellable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, symbol=self.symbol,
        )

    # --- 신용주문(국내 전용) ---
    def credit_buy(
        self, *, quantity: Numeric, credit_type: CreditType, limit_price: Numeric | None = None,
        loan_date: str | None = None, time_in_force: TimeInForce = "day",
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 신용(융자/대주)으로 매수한다.

        ``quantity`` 주문수량(주 단위 정수), ``credit_type`` 매수 신용유형(21 자기융자신규/23 유통융자
        신규/26 유통대주상환/28 자기대주상환), ``limit_price`` 지정가(생략 시 시장가), ``loan_date``(YYYYMMDD)
        상환유형(26/28)일 때 대상 대출일자(필수)·신규유형(21/23)이면 생략(전송 시 오늘로 채움),
        ``time_in_force`` 현재 ``"day"`` 만, ``client_order_id`` 멱등키(생략 시 자동 발행).

        현금 :meth:`buy` 와 같은 안전 엔진(이중체결 방지·타임아웃 재시도 금지)을 공유한다. **모의투자
        미지원**. **신용주문은 기본 비활성**이라 ``KISClient(..., allow_credit=True)`` 로 명시적으로 켜야
        한다(고위험 보호). 잘못된 조합/계좌 미설정/비활성은 ``KISUsageError``, 접수 거부는
        ``OrderRejectedError``, 타임아웃(체결 불명)은 ``OrderTimeoutError``(``kis.orders.reconcile`` 로 확인)."""
        self._client._require_credit_enabled()
        self._require_krx_board("신용주문")
        return self._client._place_order(Order.credit(
            self.symbol, side="buy", quantity=quantity, credit_type=credit_type, limit_price=limit_price,
            loan_date=loan_date, time_in_force=time_in_force, client_order_id=client_order_id,
        ))

    def credit_sell(
        self, *, quantity: Numeric, credit_type: CreditType, limit_price: Numeric | None = None,
        loan_date: str | None = None, time_in_force: TimeInForce = "day",
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 신용(대주/상환)으로 매도한다.

        ``credit_type`` 매도 신용유형(22 유통대주신규/24 자기대주신규/25 자기융자상환/27 유통융자상환),
        ``loan_date``(YYYYMMDD) 상환유형(25/27)일 때 대상 대출일자(필수)·신규유형(22/24)이면 생략(오늘로
        채움). 나머지 인자·안전 규칙·예외는 :meth:`credit_buy` 와 같다. **모의투자 미지원**."""
        self._client._require_credit_enabled()
        self._require_krx_board("신용주문")
        return self._client._place_order(Order.credit(
            self.symbol, side="sell", quantity=quantity, credit_type=credit_type, limit_price=limit_price,
            loan_date=loan_date, time_in_force=time_in_force, client_order_id=client_order_id,
        ))

    # --- 주문 실행(국내 현금; KRX 주문구분 division 지원) -- _StockBase.buy/sell 을 오버라이드 ---
    def buy(
        self, *, quantity: Numeric, limit_price: Numeric | None = None,
        time_in_force: TimeInForce = "day", division: DomesticDivision | None = None,
        stop_price: Numeric | None = None, client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 매수한다 -- ``limit_price`` 를 주면 지정가, 없으면 시장가.

        ``division`` 으로 KRX 고유 주문구분을 고른다(국내 현금 전용):
        ``conditional_limit`` 조건부지정가(장중 지정가->마감 시장가, ``limit_price`` 필요),
        ``immediate_limit`` 최유리지정가(접수 시점 상대편 최우선호가에 지정가로 즉시 체결 -- 매도면 최우선
        매수호가, 매수면 최우선 매도호가; ``limit_price`` 없음), ``priority_limit`` 최우선지정가(같은 방향 최우선
        호가에 지정가로 대기, 체결 우선순위 확보; ``limit_price`` 없음),
        ``midpoint`` 중간가(수량만; 호가 중간값으로 시장이 가격 결정, 전 보드, IOC/FOK 가능),
        ``pre_market_close`` 장전 시간외(전일 종가, KRX 전용), ``post_market_close`` 장후 시간외(당일
        종가, KRX 전용), ``after_hours_single`` 시간외 단일가(``limit_price`` 필수, KRX 전용).
        IOC/FOK 는 ``time_in_force="ioc"/"fok"``
        로 조합한다(지정가/시장가/최유리/중간가에서). ``immediate_limit`` 은 시장가의 슬리피지 없이 즉시 체결하려는
        안전 대안이다(얕은 호가에서 시장가는 나쁜 가격까지 쓸어담을 수 있다).

        ``stop_price`` 를 ``limit_price`` 와 함께 주면 스톱지정가(트리거 도달 시 지정가 접수, KRX 전용).

        이중체결 방지·타임아웃 재시도 금지가 안전 엔진에서 자동 적용된다. 계좌 미설정은
        :class:`~kis_trader.errors.KISUsageError`, 조회전용 계좌면 :class:`~kis_trader.errors.
        AccountNotOrderableError`, 접수 거부는 ``OrderRejectedError``, 타임아웃(체결 불명)은
        ``OrderTimeoutError`` -- 후자는 ``kis.orders.reconcile`` 로 확인한다."""
        return self._client._place_order(self._make_domestic_order(
            "buy", quantity=quantity, limit_price=limit_price, time_in_force=time_in_force,
            division=division, stop_price=stop_price, client_order_id=client_order_id,
        ))

    def sell(
        self, *, quantity: Numeric, limit_price: Numeric | None = None,
        time_in_force: TimeInForce = "day", division: DomesticDivision | None = None,
        stop_price: Numeric | None = None, client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 매도한다 -- 계약·``division`` 은 :meth:`buy` 와 동일(방향만 매도)."""
        return self._client._place_order(self._make_domestic_order(
            "sell", quantity=quantity, limit_price=limit_price, time_in_force=time_in_force,
            division=division, stop_price=stop_price, client_order_id=client_order_id,
        ))

    def _make_order(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None,
        time_in_force: TimeInForce, client_order_id: str | None,
    ) -> Order:
        return self._make_domestic_order(
            side, quantity=quantity, limit_price=limit_price, time_in_force=time_in_force,
            division=None, stop_price=None, client_order_id=client_order_id,
        )

    def _make_domestic_order(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None,
        time_in_force: TimeInForce, division: DomesticDivision | None,
        stop_price: Numeric | None, client_order_id: str | None,
    ) -> Order:
        # 스톱지정가: 트리거(stop_price)+지정가(limit_price) 둘 다 필요. 스톱은 주문구분(division)이
        # 아니라 order_type 이라 division 과 배타. 국내엔 스톱시장가가 없어 limit_price 필수.
        if stop_price is not None:
            if division is not None:
                raise KISUsageError("stop_price 와 division 은 함께 줄 수 없다(스톱은 주문구분이 아니다).")
            if limit_price is None:
                raise KISUsageError("스톱지정가는 limit_price 가 필요하다(국내엔 스톱시장가 없음).")
            return Order.stop_limit(self.symbol, side=side, quantity=quantity,
                                    limit_price=limit_price, stop_price=stop_price,
                                    time_in_force=time_in_force, board=self.market,
                                    client_order_id=client_order_id)
        # 가격없는 구분(최유리/최우선/중간가/장전·장후 시간외)은 시장이 가격을 정하므로 limit_price 없음
        # (order_type="market" 기반), 지정가 기반 구분(조건부/시간외 단일가)은 가격 필요(order_type="limit" 기반).
        # division 없으면 기존 동작(limit_price 유무로 시장가/지정가). 결합 불변식은 Order.__post_init__ 에도
        # 있으나, 여기서 미리 막아 division 을 지목하는 명확한 메시지를 준다. 두 집합은 order.py 가 원장이라
        # (DomesticDivision 을 남김없이 분할), 새 division 이 어느 한쪽에 없으면 plain 으로 조용히 새지 않는다.
        if division in _PRICELESS_DIVISIONS:
            if limit_price is not None:
                raise KISUsageError(
                    f"{division} 은 시장이 가격을 정하므로 limit_price 를 줄 수 없다(시장 결정 가격)."
                )
            return Order.market(self.symbol, side=side, quantity=quantity,
                                time_in_force=time_in_force, division=division,
                                board=self.market, client_order_id=client_order_id)
        if division in _LIMIT_BASED_DIVISIONS:
            if limit_price is None:
                raise KISUsageError(f"{division} 은 limit_price 가 필요하다(지정가 기반).")
            return Order.limit(self.symbol, side=side, quantity=quantity, limit_price=limit_price,
                               time_in_force=time_in_force, division=division,
                               board=self.market, client_order_id=client_order_id)
        # 여기 도달 = division is None (위 두 집합이 DomesticDivision 을 남김없이 덮으므로).
        if limit_price is None:
            return Order.market(self.symbol, side=side, quantity=quantity,
                                time_in_force=time_in_force, board=self.market,
                                client_order_id=client_order_id)
        return Order.limit(self.symbol, side=side, quantity=quantity, limit_price=limit_price,
                           time_in_force=time_in_force, board=self.market,
                           client_order_id=client_order_id)

    def _reserve(
        self, side: Side, *, quantity: Numeric, limit_price: Numeric | None, end_date: str | None,
        client_order_id: str | None,
    ) -> ExecutionReport:
        self._require_krx_board("예약주문")
        return self._client._place_reserved_order(
            symbol=self.symbol, side=side, quantity=quantity, limit_price=limit_price, end_date=end_date,
            client_order_id=client_order_id,
        )

    def _require_krx_board(self, what: str) -> None:
        # 신용·예약 주문은 아직 보드(EXCG_ID_DVSN_CD) 배선이 없어 KRX 전용이다 -- NXT/UN 종목 핸들에서
        # 부르면 조용히 KRX 로 나가지 않도록 fail-closed(즉시 현금주문은 보드를 반영하지만 이 둘은 차기).
        if self.market != "KRX":
            raise KISUsageError(
                f"{what}은 아직 KRX 보드만 지원한다(board={self.market!r}). NXT/통합(SOR)은 차기 지원."
            )
