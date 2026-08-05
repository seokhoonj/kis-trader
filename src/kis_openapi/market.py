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
        LimitStock,
        Market,
        MarketInvestorFlow,
        ProgramFlowPoint,
        ProgramTradeSummary,
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
        기간이 아니라 앵커 날짜). ``as_of`` 없으면 오늘. 종목 단위는 ``kis.ticker(code).investor_flows()``."""
        return market_api.fetch_market_investor_flows(
            self._client.transport, market=market, as_of=as_of
        )

    def program_trades(
        self, *, market: Market = "KOSPI",
        start: str | date | None = None, end: str | date | None = None,
    ) -> list[ProgramTradeSummary]:
        """시장(``"KOSPI"``/``"KOSDAQ"``) 전체의 일별 프로그램매매 종합(차익/비차익 순매수; 최근->과거).
        ``start`` 미지정이면 최근 30일. 종목 단위는 ``kis.ticker(code).program_trades()``."""
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
