"""종목 핸들 -- :class:`Ticker`.

한 종목에 대해 행위를 시키는 핸들이다: ``kis.domestic.stock("005930").quote()`` 처럼. 시세는 세션
인증만으로 되고, 주문은 세션에 묶인 계좌 + 내부 안전엔진을 쓴다. 사용자는 KIS
구조(quotations/trading/국내/해외)를 몰라도 되고, 시장은 심볼로 자동 판별된다.

핸들은 :class:`~kis_openapi.client.KISClient` 가 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from ._domestic import account as account_api
from ._domestic import analysis as analysis_api
from ._domestic import etf as etf_api
from ._domestic import finance as finance_api
from ._domestic import market_data
from ._overseas import market_data as overseas_market_data
from .after_hours import AfterHoursConclusion, AfterHoursDailyPrice, AfterHoursQuote
from .analysis import (
    AnalystOpinion,
    CreditBalancePoint,
    DailyExecutionVolume,
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
from .bar import Bar, Interval
from .broker import BrokerActivitySummary, BrokerDailyActivity, BrokerTradeTicks
from .errors import KISUsageError
from .etf_items import (
    ETFNAV,
    ETFComponent,
    ETFNAVComparison,
    ETFNAVHistoryPoint,
    ETFNAVMinutePoint,
    ETFOrderBook,
)
from .financials import (
    BalanceSheet,
    FinancialRatio,
    GrowthRatio,
    IncomeStatement,
    OtherRatio,
    ProfitabilityRatio,
    StabilityRatio,
)
from .instrument import DomesticBoard, resolve_market
from .investor import DetailedInvestorHistory, InvestorEstimate, InvestorFlow
from .order import CreditType, Order, Side, TimeInForce
from .order_book import OrderBook
from .orderable import BuyableAmount, SellableQuantity
from .overseas_items import OverseasCurrentPrice
from .program import DailyProgramTradePoint, ProgramTradePoint
from .quote import Quote
from .report import ExecutionReport
from .stock_info import StockInfo, StockStatus
from .trade import Trade

if TYPE_CHECKING:
    from .client import KISClient


class Ticker:
    """한 종목에 대한 행위 핸들. 세션(:class:`KISClient`)과 심볼/시장을 안다.

    국내는 시장 보드(:attr:`market`), 해외는 거래소코드(:attr:`exchange`)로 식별한다 -- 둘 중 하나만
    있다. 보통 직접 만들지 않고 :meth:`KISClient.ticker` 로 얻는다(세션이 필요하므로).
    """

    symbol: str
    market: DomesticBoard | None      # 국내 보드(해외면 None)
    exchange: str | None              # 해외 거래소코드(국내면 None)

    def __init__(
        self,
        client: KISClient,
        symbol: str,
        *,
        market: DomesticBoard | None = None,
        exchange: str | None = None,
    ) -> None:
        self._client = client
        self.symbol = symbol
        if exchange is not None:      # 해외: 거래소코드로 식별
            self.market = None
            self.exchange = exchange
        else:                         # 국내: 심볼/보드로 판별
            self.market = resolve_market(symbol, market=market)
            self.exchange = None

    @property
    def is_overseas(self) -> bool:
        """해외 종목이면 True(거래소코드로 식별)."""
        return self.exchange is not None

    def _domestic_market(self) -> DomesticBoard:
        """국내 전용 메서드가 쓰는 시장 보드. 해외 티커면 -- 아직 해외 미구현이라 -- 명확히 거부한다."""
        if self.market is None:
            raise KISUsageError(
                f"해외 티커({self.symbol}@{self.exchange})에선 이 기능이 아직 미지원이다 "
                f"-- 해외는 .quote() 만 된다."
            )
        return self.market

    def info(self) -> StockInfo:
        """종목 기본정보(이름·상장주식수·자본금·액면가·업종·상장일). 국내 전용."""
        self._domestic_market()        # 국내 전용
        return market_data.fetch_stock_info(self._client.transport, symbol=self.symbol)

    def quote(self) -> Quote:
        """현재가 스냅샷(국내/해외 자동 라우팅)."""
        if self.exchange is not None:
            return overseas_market_data.fetch_quote(
                self._client.transport, symbol=self.symbol, exchange=self.exchange
            )
        return market_data.fetch_quote(self._client.transport, symbol=self.symbol, market=self.market)

    def current_price(self) -> OverseasCurrentPrice:
        """해외주식 현재체결가와 누적 거래량·거래대금. 해외 티커 전용."""
        if self.exchange is None:
            raise KISUsageError("current_price()는 해외 티커 전용이다.")
        return overseas_market_data.fetch_current_price(
            self._client.transport, symbol=self.symbol, exchange=self.exchange
        )

    def status(self) -> StockStatus:
        """현재가와 거래·규제·경고 상태. 국내 전용."""
        return market_data.fetch_stock_status(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def intraday_executions(self, *, at: str = "235959") -> IntradayExecutions:
        """기준시각 이전의 당일 체결·최우선호가·체결강도. 국내 전용."""
        return market_data.fetch_intraday_executions(
            self._client.transport,
            symbol=self.symbol,
            market=self._domestic_market(),
            at=at,
        )

    def bars(
        self,
        *,
        interval: Interval = "1d",
        start: str | date | None = None,
        end: str | date | None = None,
        adjusted: bool = True,
        max_bars: int | None = None,
    ) -> list[Bar]:
        """OHLCV 바(과거->현재). ``interval="1m"`` 은 당일 1분봉(``start``/``end``/``adjusted``
        무시, 최신 세션; ``max_bars`` 로 최근 N개), ``1d``/``1wk``/``1mo`` 는 [start, end]
        기간봉(``start`` 필요).

        ``start`` > ``end``, ``max_bars`` <= 0, 기간봉인데 ``start`` 없음이면
        :class:`~kis_openapi.errors.KISUsageError`. 응답 손상(비배열 output2)이나 페이지 상한
        초과는 :class:`~kis_openapi.errors.KISError`. 해외 ``1m`` 은 최신 분봉을 뒤로 밀며 모은다
        (``start``/``end`` 무시, ``max_bars`` 로 최근 N개).
        """
        if self.exchange is not None:
            return overseas_market_data.fetch_bars(
                self._client.transport, symbol=self.symbol, exchange=self.exchange,
                interval=interval, start=start, end=end, adjusted=adjusted, max_bars=max_bars,
            )
        return market_data.fetch_bars(
            self._client.transport, symbol=self.symbol, market=self.market,
            interval=interval, start=start, end=end, adjusted=adjusted, max_bars=max_bars,
        )

    def recent_prices(
        self, *, interval: Interval = "1d", adjusted: bool = True
    ) -> list[RecentPricePoint]:
        """최근 30개 일·주·월 주가와 외국인 수급·거래량·권리락 보조지표. 국내 전용."""
        return market_data.fetch_recent_prices(
            self._client.transport,
            symbol=self.symbol,
            market=self._domestic_market(),
            interval=interval,
            adjusted=adjusted,
        )

    def minute_bars_on(
        self, day: str | date, *, max_bars: int | None = None
    ) -> list[Bar]:
        """특정 과거일 ``day`` 의 1분봉(과거->현재). 당일만 주는 :meth:`bars`\\ ``(interval="1m")`` 과
        달리 지난 영업일의 분봉을 backfill 한다. ``max_bars`` 로 최근 N개. 국내 전용."""
        self._domestic_market()        # 국내 전용(해외 분봉 미지원)
        return market_data.fetch_minute_bars_on(
            self._client.transport, symbol=self.symbol, market=self.market,
            day=day, max_bars=max_bars,
        )

    def order_book(self) -> OrderBook:
        """호가창 스냅샷(국내 10단계 / 해외는 미국 10·그 외 1단계). 국내/해외 자동 라우팅."""
        if self.exchange is not None:
            return overseas_market_data.fetch_order_book(
                self._client.transport, symbol=self.symbol, exchange=self.exchange
            )
        return market_data.fetch_order_book(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    def trades(self) -> list[Trade]:
        """최근 체결 목록(time & sales; 최신순). 국내/해외 자동 라우팅."""
        if self.exchange is not None:
            return overseas_market_data.fetch_trades(
                self._client.transport, symbol=self.symbol, exchange=self.exchange
            )
        return market_data.fetch_trades(
            self._client.transport, symbol=self.symbol, market=self.market
        )

    def investor_flows(self) -> list[InvestorFlow]:
        """일자별 투자자(개인/외국인/기관) 매매동향(최신순)."""
        return market_data.fetch_investor_flows(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def detailed_investor_history(
        self, *, as_of: str | date | None = None
    ) -> DetailedInvestorHistory:
        """세부 투자자 주체별 매수·매도·순매수 일별 내역(``as_of`` 기준)."""
        return market_data.fetch_detailed_investor_history(
            self._client.transport,
            symbol=self.symbol,
            market=self._domestic_market(),
            as_of=as_of,
        )

    def broker_activity(self) -> BrokerActivitySummary:
        """매도/매수 상위 회원사(증권사) 매매 비중."""
        return market_data.fetch_broker_activity(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
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
        self._domestic_market()
        return market_data.fetch_broker_trade_ticks(
            self._client.transport, symbol=self.symbol,
            member_code=member_code, min_volume=min_volume,
        )

    def after_hours_quote(self) -> AfterHoursQuote:
        """시간외 단일가 스냅샷(예상체결가·최우선호가)."""
        return market_data.fetch_after_hours_quote(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def after_hours_conclusions(self) -> list[AfterHoursConclusion]:
        """시간외 단일가 세션의 시간별 체결(시각 리스트). 세션 밖이면 빈 리스트일 수 있다."""
        return market_data.fetch_after_hours_conclusions(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def after_hours_daily(self) -> list[AfterHoursDailyPrice]:
        """시간외 단일가 세션의 일자별 종가(최근->과거). 세션 밖이면 빈 리스트일 수 있다."""
        return market_data.fetch_after_hours_daily(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def after_hours_order_book(self) -> OrderBook:
        """시간외 단일가 세션의 10단계 호가창 스냅샷. 세션 밖이면 단계가 비어 올 수 있다."""
        return market_data.fetch_after_hours_order_book(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def program_trades(self) -> list[ProgramTradePoint]:
        """장중 시간대별 프로그램매매 흐름(매수/매도/순매수 수량·금액, 시간 순)."""
        return market_data.fetch_program_trades(
            self._client.transport, symbol=self.symbol, market=self._domestic_market()
        )

    def daily_program_trades(
        self, *, as_of: str | date | None = None
    ) -> list[DailyProgramTradePoint]:
        """종목별 프로그램매매 일별 추이."""
        self._domestic_market()
        return market_data.fetch_daily_program_trades(
            self._client.transport, symbol=self.symbol, as_of=as_of
        )

    def investor_estimate(self) -> list[InvestorEstimate]:
        """장중 투자자(외국인/기관) 순매수 추정(시간 순, 확정 아닌 가추정)."""
        self._domestic_market()        # 국내 전용(해외 티커 거부)
        return market_data.fetch_investor_estimate(self._client.transport, symbol=self.symbol)

    def balance_sheet(self, *, quarterly: bool = False) -> list[BalanceSheet]:
        """결산기별 대차대조표(최근->과거). ``quarterly=True`` 면 분기, 아니면 연간."""
        self._domestic_market()        # 국내 전용
        return finance_api.fetch_balance_sheet(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def income_statement(self, *, quarterly: bool = False) -> list[IncomeStatement]:
        """결산기별 손익계산서(최근->과거). ``quarterly=True`` 면 분기, 아니면 연간."""
        self._domestic_market()        # 국내 전용
        return finance_api.fetch_income_statement(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def financial_ratios(self, *, quarterly: bool = False) -> list[FinancialRatio]:
        """결산기별 주요 재무비율(ROE·EPS·BPS·부채비율·유보율·증가율; 최근->과거). ``quarterly=True``
        면 분기, 아니면 연간."""
        self._domestic_market()        # 국내 전용
        return finance_api.fetch_financial_ratios(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def profitability_ratios(self, *, quarterly: bool = False) -> list[ProfitabilityRatio]:
        """결산기별 수익성비율(ROA·ROE·순이익률·총이익률; 최근->과거). ``quarterly=True`` 면 분기,
        아니면 연간."""
        self._domestic_market()        # 국내 전용
        return finance_api.fetch_profitability_ratios(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def stability_ratios(self, *, quarterly: bool = False) -> list[StabilityRatio]:
        """결산기별 안정성비율(부채비율·차입금의존도·유동비율·당좌비율; 최근->과거). ``quarterly=True``
        면 분기, 아니면 연간."""
        self._domestic_market()        # 국내 전용
        return finance_api.fetch_stability_ratios(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def growth_ratios(self, *, quarterly: bool = False) -> list[GrowthRatio]:
        """결산기별 성장성비율(매출/영업이익/자기자본/총자산 증가율; 최근->과거). ``quarterly=True``
        면 분기, 아니면 연간."""
        self._domestic_market()        # 국내 전용
        return finance_api.fetch_growth_ratios(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def other_ratios(self, *, quarterly: bool = False) -> list[OtherRatio]:
        """결산기별 기타주요비율(EVA·EBITDA·EV/EBITDA; 최근->과거). ``quarterly=True`` 면 분기,
        아니면 연간."""
        self._domestic_market()        # 국내 전용
        return finance_api.fetch_other_ratios(
            self._client.transport, symbol=self.symbol, quarterly=quarterly
        )

    def credit_balance_trend(
        self, *, as_of: str | date | None = None
    ) -> list[CreditBalancePoint]:
        """일별 신용잔고(융자/대주) 추이(기준일에서 과거로). ``as_of`` 없으면 오늘 기준."""
        self._domestic_market()        # 국내 전용
        return analysis_api.fetch_credit_balance_trend(
            self._client.transport, symbol=self.symbol, date_=as_of
        )

    def short_sale_trend(
        self, *, start: str | date | None = None, end: str | date | None = None
    ) -> list[ShortSalePoint]:
        """일별 공매도 추이(기간 [start, end], 최근->과거). 기본은 최근."""
        self._domestic_market()        # 국내 전용
        return analysis_api.fetch_short_sale_trend(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    def volume_profile(self) -> VolumeProfile:
        """가격대별 거래량 분포(매물대)와 요약(현재가·가중평균가·상장주수)."""
        self._domestic_market()        # 국내 전용
        return analysis_api.fetch_volume_profile(self._client.transport, symbol=self.symbol)

    def foreign_net_buy_trend(self) -> list[ForeignNetBuyPoint]:
        """이 종목의 장중 외국계(외국인 회원사) 순매수 추이(시간대별)."""
        self._domestic_market()        # 국내 전용
        return analysis_api.fetch_foreign_net_buy_trend(
            self._client.transport, symbol=self.symbol
        )

    def loan_trend(
        self, *, start: str | date | None = None, end: str | date | None = None
    ) -> list[LoanPoint]:
        """일별 대차거래(대여) 추이(기간 [start, end], 최근->과거). 대차잔고는 공매도 공급 대리지표."""
        self._domestic_market()        # 국내 전용
        return analysis_api.fetch_loan_trend(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    def analyst_opinions(
        self, *, start: str | date | None = None, end: str | date | None = None
    ) -> list[AnalystOpinion]:
        """기간 [start, end] 의 애널리스트 투자의견·목표주가 시계열(최근->과거). start 미지정이면 최근 30일."""
        self._domestic_market()        # 국내 전용
        return analysis_api.fetch_analyst_opinions(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    def earnings_estimate(self) -> EarningsEstimate:
        """월간 추정 손익계산서·투자지표 스냅샷. 리서치 추정 대상 국내 종목만 유효하다."""
        self._domestic_market()
        return analysis_api.fetch_earnings_estimate(
            self._client.transport, symbol=self.symbol
        )

    def daily_trade_volume(
        self, *, start: str | date | None = None, end: str | date | None = None
    ) -> list[DailyExecutionVolume]:
        """일별 매수/매도 체결량 추이(기간 [start, end], 최근->과거). start 미지정이면 최근 30일."""
        self._domestic_market()        # 국내 전용
        return analysis_api.fetch_daily_trade_volume(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    def expected_price_trend(self, *, nonzero_only: bool = False) -> list[ExpectedPricePoint]:
        """동시호가 예상 체결가 추이(시각 리스트, 최근->과거). ``nonzero_only=True`` 면 체결량 0 시각 제외."""
        self._domestic_market()        # 국내 전용
        return analysis_api.fetch_expected_price_trend(
            self._client.transport, symbol=self.symbol, nonzero_only=nonzero_only
        )

    def trade_amount_bands(self) -> list[TradeAmountBand]:
        """당일 체결금액대별 매매비중(금액대 리스트). 각 금액대의 매수/매도/순매수 거래량·비율·건수."""
        self._domestic_market()        # 국내 전용
        return analysis_api.fetch_trade_amount_bands(
            self._client.transport, symbol=self.symbol
        )

    def nav(self) -> ETFNAV:
        """ETF/ETN 순자산가치(NAV) 스냅샷(NAV·괴리율·추적오차율·순자산총액). 이 종목이 ETF/ETN
        일 때만 유효하다(아니면 서버가 거부). 시장 체결가는 :meth:`quote`."""
        self._domestic_market()        # 국내 ETF 전용
        return etf_api.fetch_etf_nav(self._client.transport, symbol=self.symbol)

    def nav_comparison(self) -> ETFNAVComparison:
        """ETF 시장가격과 NAV의 당일 OHLC 비교. 국내 ETF/ETN 전용."""
        self._domestic_market()
        return etf_api.fetch_etf_nav_comparison(self._client.transport, symbol=self.symbol)

    def nav_intraday(self, *, interval_minutes: int = 1) -> list[ETFNAVMinutePoint]:
        """최근 30개 ETF 시장가격-NAV 분별 비교. 국내 ETF/ETN 전용."""
        self._domestic_market()
        return etf_api.fetch_etf_nav_intraday(
            self._client.transport,
            symbol=self.symbol,
            interval_minutes=interval_minutes,
        )

    def etf_order_book(self) -> ETFOrderBook:
        """ETF 10단계 호가와 LP 잔량·잔량 증감·중간가. 국내 ETF 전용."""
        self._domestic_market()
        return etf_api.fetch_etf_order_book(self._client.transport, symbol=self.symbol)

    def components(self) -> list[ETFComponent]:
        """ETF 구성종목(PDF) 목록 -- 각 구성종목의 시세·ETF 내 구성 비중·평가금액. 이 종목이 ETF
        일 때만 유효하다(아니면 서버가 거부)."""
        self._domestic_market()        # 국내 ETF 전용
        return etf_api.fetch_etf_components(self._client.transport, symbol=self.symbol)

    def nav_history(self, *, start: str | date, end: str | date) -> list[ETFNAVHistoryPoint]:
        """일별 NAV-가격 추이(과거->현재). ``start``/``end`` 는 기간(YYYYMMDD 또는 ``date``). 각
        거래일의 종가·NAV·괴리율로 프리미엄/디스카운트 추이를 본다. 이 종목이 ETF/ETN 일 때만 유효."""
        self._domestic_market()        # 국내 ETF 전용
        return etf_api.fetch_etf_nav_history(
            self._client.transport, symbol=self.symbol, start=start, end=end
        )

    def buyable(self, *, limit_price: object | None = None) -> BuyableAmount:
        """이 종목의 매수가능 여력(현금 기준·미수 포함 최대). ``limit_price`` 없으면 시장가 기준.

        계좌 정보 없이 생성한 세션이면 :class:`~kis_openapi.errors.KISUsageError`.
        """
        self._domestic_market()        # 해외 미지원
        cano, product_code = self._client._require_account()
        return account_api.fetch_buyable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, symbol=self.symbol, limit_price=limit_price,
        )

    def credit_buyable(
        self, *, credit_type: str = "21", limit_price: object | None = None
    ) -> BuyableAmount:
        """이 종목의 신용(융자/대주) 매수가능 여력. ``credit_type`` 신용유형(기본 21 자기융자신규,
        22 유통대주신규/23 유통융자신규/24 자기대주신규/25~28 각 상환), ``limit_price`` 없으면
        시장가 기준. 현금 :meth:`buyable` 과 같은 :class:`~kis_openapi.orderable.BuyableAmount` 를
        주며, 신용 전용 필드는 ``_raw`` 로 본다. **모의투자 미지원**."""
        self._domestic_market()        # 해외 미지원
        cano, product_code = self._client._require_account()
        return account_api.fetch_credit_buyable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, symbol=self.symbol,
            credit_type=credit_type, limit_price=limit_price,
        )

    def sellable(self) -> SellableQuantity:
        """이 종목의 매도가능 수량. **모의투자 미지원**(demo면 :class:`~kis_openapi.errors.KISUsageError`)."""
        self._domestic_market()        # 해외 미지원
        cano, product_code = self._client._require_account()
        return account_api.fetch_sellable(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, symbol=self.symbol,
        )

    # --- 주문 실행(안전 엔진 위임; 계좌 정보 필요) --------------------
    def buy(
        self, *, quantity: object, price: object | None = None,
        time_in_force: TimeInForce = "day", client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 매수한다 -- ``price`` 를 주면 지정가, 없으면 시장가.

        이중체결 방지·타임아웃 재시도 금지가 안전 엔진에서 자동 적용된다. 계좌 정보가 없으면
        :class:`~kis_openapi.errors.KISUsageError`, 조회전용(퇴직연금 등) 계좌면
        :class:`~kis_openapi.errors.AccountNotOrderable`. 접수 거부는 ``OrderRejectedError``,
        타임아웃(체결 불명)은 ``OrderTimeoutError`` -- 후자는 :meth:`KISClient.reconcile` 로 확인한다.
        """
        return self._client._place_order(self._make_order("buy", quantity, price, time_in_force, client_order_id))

    def sell(
        self, *, quantity: object, price: object | None = None,
        time_in_force: TimeInForce = "day", client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 매도한다 -- ``price`` 를 주면 지정가, 없으면 시장가(계약은 :meth:`buy` 와 동일)."""
        return self._client._place_order(self._make_order("sell", quantity, price, time_in_force, client_order_id))

    def credit_buy(
        self, *, quantity: object, credit_type: CreditType, price: object | None = None,
        loan_date: str | None = None, time_in_force: TimeInForce = "day",
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 신용(융자/대주)으로 매수한다.

        ``quantity`` 주문수량(주 단위 정수), ``credit_type`` 매수 신용유형(21 자기융자신규/23 유통융자
        신규/26 유통대주상환/28 자기대주상환), ``price`` 지정가(생략 시 시장가), ``loan_date``(YYYYMMDD)
        상환유형(26/28)일 때 대상 대출일자(필수)·신규유형(21/23)이면 생략(전송 시 오늘로 채움),
        ``time_in_force`` 현재 ``"day"`` 만(그 외는 ``NotImplementedError``), ``client_order_id`` 멱등키
        (생략 시 자동 발행).

        현금 :meth:`buy` 와 같은 안전 엔진(이중체결 방지·타임아웃 재시도 금지)을 공유한다. **모의투자
        미지원**, 국내 종목만. 반환은 :class:`~kis_openapi.report.ExecutionReport`. 잘못된 조합/계좌
        미설정은 ``KISUsageError``, 접수 거부는 ``OrderRejectedError``, 타임아웃(체결 불명)은
        ``OrderTimeoutError``(:meth:`KISClient.reconcile` 로 확인)."""
        self._domestic_market()        # 해외 미지원(신용은 국내만)
        return self._client._place_order(Order.credit(
            self.symbol, side="buy", quantity=quantity, credit_type=credit_type, price=price,
            loan_date=loan_date, time_in_force=time_in_force, client_order_id=client_order_id,
        ))

    def credit_sell(
        self, *, quantity: object, credit_type: CreditType, price: object | None = None,
        loan_date: str | None = None, time_in_force: TimeInForce = "day",
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목을 신용(대주/상환)으로 매도한다.

        ``credit_type`` 매도 신용유형(22 유통대주신규/24 자기대주신규/25 자기융자상환/27 유통융자상환),
        ``loan_date``(YYYYMMDD) 상환유형(25/27)일 때 대상 대출일자(필수)·신규유형(22/24)이면 생략(오늘로
        채움). 나머지 인자·안전 규칙·예외는 :meth:`credit_buy` 와 같다. **모의투자 미지원**, 국내 종목만."""
        self._domestic_market()        # 해외 미지원
        return self._client._place_order(Order.credit(
            self.symbol, side="sell", quantity=quantity, credit_type=credit_type, price=price,
            loan_date=loan_date, time_in_force=time_in_force, client_order_id=client_order_id,
        ))

    def reserve_buy(
        self, *, quantity: object, price: object | None = None, end_date: str | None = None,
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목의 **현금 예약매수** -- 다음 영업일(또는 ``end_date`` 까지) 아침 동시호가에 집행되도록
        예약한다. ``price`` 를 주면 지정가, 없으면 시장가. ``end_date``(YYYYMMDD)는 예약 유효 종료일
        (현재 이후, 생략 시 브로커 기본).

        즉시 :meth:`buy` 와 같은 안전 규칙(이중발주 방지·재시도 금지·주문가능 계좌 가드)을 공유하되
        라이프사이클이 다르다: 반환 :class:`~kis_openapi.report.ExecutionReport` 의 ``order_id`` 는
        예약주문순번(정정·취소 시 지목), ``status`` 는 :attr:`~kis_openapi.report.OrderStatus.PENDING_NEW`
        (접수됨·미집행). **모의투자 미지원**. 시장별로 갈린다: **국내**는 현금 예약(``price`` 있으면
        지정가·없으면 시장가, ``end_date`` 지원), **해외(미국)**는 지정가 예약(``price`` 필수, ``end_date``
        미지원)으로 자동 라우팅된다. 잘못된 인자/계좌 미설정은 ``KISUsageError``, 조회전용 계좌는
        ``AccountNotOrderable``, 접수 거부는 ``OrderRejectedError``, 타임아웃(접수 불명)은
        ``OrderTimeoutError``(:meth:`KISClient.reconcile` 로 확인)."""
        return self._reserve("buy", quantity, price, end_date, client_order_id)

    def reserve_sell(
        self, *, quantity: object, price: object | None = None, end_date: str | None = None,
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 종목의 **현금 예약매도**. 계약·안전 규칙은 :meth:`reserve_buy` 와 같다(방향만 매도)."""
        return self._reserve("sell", quantity, price, end_date, client_order_id)

    def _reserve(
        self, side: Side, quantity: object, price: object | None, end_date: str | None,
        client_order_id: str | None,
    ) -> ExecutionReport:
        if self.exchange is not None:  # 미국 해외 예약주문(지정가만, end_date 미지원)
            if end_date is not None:
                raise KISUsageError("해외 예약주문은 end_date 를 지원하지 않는다(미국 예약).")
            if price is None:
                raise KISUsageError("해외 예약주문은 지정가만 지원한다 -- price 를 지정하라.")
            return self._client._place_overseas_reserved_order(
                symbol=self.symbol, side=side, quantity=quantity, price=price,
                exchange=self.exchange, client_order_id=client_order_id,
            )
        return self._client._place_reserved_order(
            symbol=self.symbol, side=side, quantity=quantity, price=price, end_date=end_date,
            client_order_id=client_order_id,
        )

    def daytime_buy(
        self, *, quantity: object, price: object, client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 미국 종목을 **미국주간거래**로 매수한다(한국 낮 시간대). 지정가만(``price`` 필수).

        정규 해외 :meth:`buy` 와 같은 안전 엔진(이중체결 방지·재시도 금지)을 공유하되 세션이 달라
        정정·취소는 미국주간 전용 엔드포인트로 라우팅된다(반환 리포트의 ``client_order_id`` 로 :meth:`
        KISClient.cancel_order`/:meth:`~KISClient.replace_order`). **모의투자 미지원**, 미국(NAS/NYS/AMS)만.
        타임아웃 시 :meth:`KISClient.reconcile` 은 주간 체결이 정규 체결내역에 없어 자동 확정하지 않고
        None(in-flight 유지)을 준다 -- 수동 확인이 필요하다. 예외는 :meth:`buy` 와 같고(접수 거부
        ``OrderRejectedError``·타임아웃 ``OrderTimeoutError``), 비-미국 티커·계좌 미설정은 ``KISUsageError``."""
        return self._client._place_order(self._make_daytime_order("buy", quantity, price, client_order_id))

    def daytime_sell(
        self, *, quantity: object, price: object, client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 미국 종목을 미국주간거래로 매도한다(계약은 :meth:`daytime_buy` 와 동일, 방향만 매도)."""
        return self._client._place_order(self._make_daytime_order("sell", quantity, price, client_order_id))

    def _make_daytime_order(
        self, side: Side, quantity: object, price: object, client_order_id: str | None,
    ) -> Order:
        if self.exchange is None:
            raise KISUsageError(
                "미국주간거래는 해외(미국) 종목만 지원한다 -- kis.overseas.stock(symbol, exchange='NAS') 로 지정하라."
            )
        return Order.limit(self.symbol, side=side, quantity=quantity, limit_price=price,
                           exchange=self.exchange, session="daytime", client_order_id=client_order_id)

    def _make_order(
        self, side: Side, quantity: object, price: object | None,
        time_in_force: TimeInForce, client_order_id: str | None,
    ) -> Order:
        if self.exchange is not None:  # 해외: 지정가만, 거래소코드를 주문 정체성에 담는다
            if price is None:
                raise KISUsageError("해외 주문은 지정가만 지원한다 -- price 를 지정하라(시장가 미지원).")
            return Order.limit(self.symbol, side=side, quantity=quantity, limit_price=price,
                               time_in_force=time_in_force, client_order_id=client_order_id,
                               exchange=self.exchange)
        if price is None:
            return Order.market(self.symbol, side=side, quantity=quantity,
                                time_in_force=time_in_force, client_order_id=client_order_id)
        return Order.limit(self.symbol, side=side, quantity=quantity, limit_price=price,
                          time_in_force=time_in_force, client_order_id=client_order_id)
