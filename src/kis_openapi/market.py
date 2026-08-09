"""시장 전체 분석 네임스페이스 -- :class:`MarketQueries`.

``kis.market.investor_flows(market="KOSPI")`` 처럼, 종목도 순위도 아닌 **시장(코스피/코스닥) 전체**
상태·수급 분석을 모은다. 순위(``kis.ranking``)가 "종목을 줄 세우기"라면 여기는 "시장 전체가 지금
어떤가"(투자자 수급, 프로그램매매 종합, VI 등)다.

직접 만들지 않고 :attr:`~kis_openapi.client.KISClient.market` 로 얻는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._domestic import market_analysis as market_api

if TYPE_CHECKING:
    from datetime import date

    from .client import KISClient
    from .market_items import (
        BrokerOpinion,
        CreditEligibleStock,
        ForeignBrokerFlow,
        FuturesMarketSchedule,
        InterestRateQuote,
        InvestorNetBuyStock,
        LendableStock,
        LimitStock,
        Market,
        MarketFunds,
        MarketInvestorFlow,
        MarketInvestorSnapshot,
        NewsItem,
        ProgramFlowPoint,
        ProgramInvestorTrade,
        ProgramTradeSummary,
        TradingDay,
        VIEvent,
    )


class MarketQueries:
    """세션에 달린 시장 전체 분석 네임스페이스. :attr:`KISClient.market` 이 만들어 준다."""

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def investor_flows(
        self, *, market: Market = "KOSPI", as_of: str | date | None = None,
    ) -> list[MarketInvestorFlow]:
        """시장(``"KOSPI"``/``"KOSDAQ"``) 전체의 투자자 순매수 최근 히스토리(``as_of`` 기준일에서 과거로;
        기간이 아니라 앵커 날짜). ``as_of`` 없으면 오늘. 종목 단위는 ``kis.domestic.stock(code).investor_flows()``."""
        return market_api.fetch_market_investor_flows(
            self._client.transport, market=market, as_of=as_of
        )

    def investor_snapshot(
        self, *, market_code: str, industry_code: str
    ) -> MarketInvestorSnapshot:
        """시장·업종 코드별 세부 투자자 매수·매도·순매수 총량 스냅샷."""
        return market_api.fetch_market_investor_snapshot(
            self._client.transport, market_code=market_code, industry_code=industry_code
        )

    def investor_net_buy_stocks(
        self, *, market: str = "all", basis: str = "volume",
        direction: str = "buy", investor: str = "all",
    ) -> list[InvestorNetBuyStock]:
        """투자자 순매수·순매도 상위 종목 집계."""
        return market_api.fetch_investor_net_buy_stocks(
            self._client.transport, market=market, basis=basis,
            direction=direction, investor=investor,
        )

    def program_investor_trades(
        self, *, market: Market = "KOSPI"
    ) -> list[ProgramInvestorTrade]:
        """시장별 당일 프로그램매매 투자자 집계."""
        return market_api.fetch_program_investor_trades(
            self._client.transport, market=market
        )

    def funds(self, *, as_of: str | date | None = None) -> list[MarketFunds]:
        """증시자금 종합(고객예탁금·신용융자잔고·펀드유형별 잔고·시가총액)의 최근 일별 추이.
        ``as_of`` 기준일에서 과거로(미지정이면 오늘). 시장 전체."""
        return market_api.fetch_market_funds(self._client.transport, as_of=as_of)

    def interest_rates(self) -> list[InterestRateQuote]:
        """국내·해외 주요 금리와 채권지수의 최신 값·전일대비 스냅샷."""
        return market_api.fetch_interest_rates(self._client.transport)

    def lendable_stocks(
        self, *, market: str = "all", symbol: str = ""
    ) -> list[LendableStock]:
        """회사 대주 가능 종목과 한도·사용·매매가능 수량 목록."""
        return market_api.fetch_lendable_stocks(
            self._client.transport, market=market, symbol=symbol
        )

    def credit_eligible_stocks(
        self, *, market: str = "all", eligible: bool = True, sort: str = "name"
    ) -> list[CreditEligibleStock]:
        """회사 신용주문 가능·불가 종목과 신용비율 목록(최대 100건)."""
        return market_api.fetch_credit_eligible_stocks(
            self._client.transport, market=market, eligible=eligible, sort=sort
        )

    def broker_opinions(
        self,
        *,
        broker: str,
        opinion: str = "all",
        start: str | date | None = None,
        end: str | date | None = None,
    ) -> list[BrokerOpinion]:
        """한 증권사가 낸 여러 종목 투자의견·목표가격(한 호출 최대 20건)."""
        return market_api.fetch_broker_opinions(
            self._client.transport, broker=broker, opinion=opinion, start=start, end=end
        )

    def program_trades(
        self, *, market: Market = "KOSPI",
        start: str | date | None = None, end: str | date | None = None,
    ) -> list[ProgramTradeSummary]:
        """시장(``"KOSPI"``/``"KOSDAQ"``) 전체의 일별 프로그램매매 종합(차익/비차익 순매수; 최근->과거).
        ``start`` 미지정이면 최근 30일. 종목 단위는 ``kis.domestic.stock(code).program_trades()``."""
        return market_api.fetch_program_trade_summary(
            self._client.transport, market=market, start=start, end=end
        )

    def vi_events(self, *, as_of: str | date | None = None) -> list[VIEvent]:
        """전 시장의 VI(변동성완화장치) 발동 이벤트(``as_of`` 기준일; 미지정이면 오늘). 발동/해제 시각·
        발동가·기준가 대비 괴리율·당일 발동횟수를 담는다."""
        return market_api.fetch_vi_events(self._client.transport, as_of=as_of)

    def limit_stocks(self) -> list[LimitStock]:
        """상한가/하한가에 도달한 종목 전체 스냅샷. 각 행의 ``at_upper_limit`` 로 상/하한 구분."""
        return market_api.fetch_limit_stocks(self._client.transport)

    def program_flow(self, *, market: Market = "KOSPI") -> list[ProgramFlowPoint]:
        """당일 시간대별 프로그램매매 순매수 대금(차익/비차익/전체; 시간 순). 일별 종합은
        :meth:`program_trades`."""
        return market_api.fetch_program_flow(self._client.transport, market=market)

    def trading_calendar(self, *, base_date: str | date | None = None) -> list[TradingDay]:
        """거래 캘린더(``base_date`` 기준 한 페이지). 각 날짜의 영업/거래/개장(휴장)/결제 여부."""
        return market_api.fetch_trading_calendar(self._client.transport, base_date=base_date)

    def futures_market_schedule(self) -> FuturesMarketSchedule:
        """국내선물의 인접 영업일 5개와 오늘 장 시작·종료 시각."""
        return market_api.fetch_futures_market_schedule(self._client.transport)

    def news(self, *, symbol: str = "", date: str | date | None = None) -> list[NewsItem]:
        """시황/공시 뉴스 제목 피드(최신순). ``symbol`` 을 주면 그 종목 관련만, ``date`` 를 주면 그
        날짜(없으면 최근 전체). 각 뉴스의 연관 종목은 ``NewsItem.symbols``."""
        return market_api.fetch_news(self._client.transport, symbol=symbol, date_=date)

    def foreign_broker_trades(self, *, sort: str = "amount") -> list[ForeignBrokerFlow]:
        """외국계 창구 매매종목 가집계(전 시장). ``sort``: ``"amount"``(금액순)/``"volume"``(수량순).
        각 행의 ``estimated_net`` 이 외국계 추정 순매수."""
        return market_api.fetch_foreign_broker_trades(self._client.transport, sort=sort)
