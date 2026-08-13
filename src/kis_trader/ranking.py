"""시장 전체 순위 네임스페이스 -- :class:`RankingQueries`.

``kis.domestic.ranking.by_change(direction="gainers")`` 처럼, 종목이 아니라 **시장 전체**를 어떤 기준으로 줄
세운 결과(:class:`~kis_trader.ranking_items.RankedStock` 리스트)를 돌려준다. KIS가 순위마다 URL을
다른 섹션(``/ranking/``, ``/quotations/``)에 두지만, 사용자에겐 "ranking" 하나로 모은다(섹션 계층
미러링 금지). 직접 만들지 않고 ``kis.domestic.ranking`` 로 얻는다.

각 순위는 한 번에 상위 한 페이지(대개 30건)만 주고 다음 조회가 없다(KIS 제약).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._domestic import ranking as ranking_api
from .ranking_items import (
    AfterHoursBalanceRanking,
    CreditBalanceRanking,
    DividendRanking,
    NearHighLowRanking,
    OvertimeRanking,
    RankedStock,
    ShortSaleRanking,
    TopViewedStock,
)

if TYPE_CHECKING:
    from datetime import date

    from .client import KISClient


class RankingQueries:
    """세션에 달린 순위 질의 네임스페이스. ``kis.domestic.ranking`` 이 만들어 준다."""

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def by_change(self, *, direction: str = "gainers") -> list[RankedStock]:
        """등락률 순위. ``direction="gainers"`` 상승률 상위 / ``"losers"`` 하락률 상위(최대 30건)."""
        return ranking_api.fetch_fluctuation(
            self._client.transport, direction=direction, market="KRX"
        )

    def by_volume(self) -> list[RankedStock]:
        """거래량 순위(최대 30건)."""
        return ranking_api.fetch_volume(self._client.transport, market="KRX")

    def by_market_cap(self) -> list[RankedStock]:
        """시가총액 순위(최대 30건). 시가총액 값은 각 항목의 ``_raw['stck_avls']``."""
        return ranking_api.fetch_market_cap(self._client.transport, market="KRX")

    def by_disparity(self, *, extreme: str = "highest", period: int = 20) -> list[RankedStock]:
        """이격도 순위. ``extreme="highest"`` 이격도 상위 / ``"lowest"`` 하위. ``period`` 는 이동평균
        일수(5/10/20/60/120). 이격도 값은 각 항목의 ``_raw['d{period}_dsrt']``(%)(최대 30건)."""
        return ranking_api.fetch_disparity(
            self._client.transport, extreme=extreme, period=period, market="KRX"
        )

    def by_quote_balance(self, *, metric: str = "net_buy") -> list[RankedStock]:
        """호가잔량 순위. ``metric`` = ``"net_buy"`` 순매수잔량 / ``"net_sell"`` 순매도잔량 /
        ``"buy_ratio"`` 매수비율 / ``"sell_ratio"`` 매도비율. 잔량 지표는 ``_raw``(최대 30건)."""
        return ranking_api.fetch_quote_balance(self._client.transport, metric=metric, market="KRX")

    def by_volume_power(self) -> list[RankedStock]:
        """체결강도 순위(최대 30건). 당일 체결강도는 각 항목의 ``_raw['tday_rltv']``."""
        return ranking_api.fetch_volume_power(self._client.transport, market="KRX")

    def by_bulk_trades(self, *, side: str = "buy") -> list[RankedStock]:
        """대량체결건수 순위. ``side="buy"`` 매수상위 / ``"sell"`` 매도상위. 체결건수는 각 항목의
        ``_raw``(shnu_cntg_csnu/seln_cntg_csnu 등)(최대 30건)."""
        return ranking_api.fetch_bulk_trades(self._client.transport, side=side, market="KRX")

    def by_interest(self) -> list[RankedStock]:
        """관심종목 등록상위 순위(최대 30건). 관심등록 건수는 각 항목의 ``_raw['inter_issu_reg_csnu']``."""
        return ranking_api.fetch_interest(self._client.transport, market="KRX")

    def by_preferred_disparity(self) -> list[RankedStock]:
        """우선주 괴리율 순위(최대 30건). 공통필드는 본주 기준, 짝 우선주 시세와 괴리율은 각 항목의
        ``_raw``(prst_* / dprt 괴리율)."""
        return ranking_api.fetch_preferred_disparity(self._client.transport, market="KRX")

    def by_finance_ratio(
        self, *, analysis: str = "profitability", year: int, quarter: str = "annual"
    ) -> list[RankedStock]:
        """재무비율 순위. ``analysis`` = ``"profitability"`` 수익성 / ``"stability"`` 안정성 /
        ``"growth"`` 성장성 / ``"activity"`` 활동성. ``year`` 회계연도(예: 2023), ``quarter`` =
        ``"q1"``/``"h1"``/``"q3"``/``"annual"``(결산). 비율값은 각 항목의 ``_raw``(최대 30건)."""
        return ranking_api.fetch_finance_ratio(
            self._client.transport, analysis=analysis, year=year, quarter=quarter, market="KRX"
        )

    def by_valuation(
        self, *, metric: str = "per", year: int, quarter: str = "annual"
    ) -> list[RankedStock]:
        """시장가치(밸류에이션) 순위. ``metric`` = per/pbr/pcr/psr/eps/eva/ebitda/ev_ebitda/
        ebitda_ratio. ``year`` 회계연도, ``quarter`` = q1/h1/q3/annual. 지표값은 각 항목의
        ``_raw``(per/pbr/...)(최대 30건). 시가총액 순위는 :meth:`by_market_cap`."""
        return ranking_api.fetch_valuation(
            self._client.transport, metric=metric, year=year, quarter=quarter, market="KRX"
        )

    def by_profit_asset(
        self, *, metric: str = "net_income", year: int, quarter: str = "annual"
    ) -> list[RankedStock]:
        """수익자산지표 순위. ``metric`` = ``"sales_profit"`` 매출이익 / ``"operating_profit"`` 영업이익 /
        ``"ordinary_profit"`` 경상이익 / ``"net_income"`` 당기순이익 / ``"total_assets"`` 자산총계 /
        ``"total_liabilities"`` 부채총계 / ``"total_equity"`` 자본총계. ``year`` 회계연도, ``quarter`` =
        q1/h1/q3/annual. 금액은 각 항목의 ``_raw``(최대 30건)."""
        return ranking_api.fetch_profit_asset(
            self._client.transport, metric=metric, year=year, quarter=quarter, market="KRX"
        )

    def by_company_trades(
        self, *, side: str = "buy", start: str | date, end: str | date
    ) -> list[RankedStock]:
        """당사매매종목 순위(기간). ``side="buy"`` 매수상위 / ``"sell"`` 매도상위. ``start``/``end`` 는
        조회 기간(YYYYMMDD 문자열 또는 ``date``). 당사 매수/매도/순매수 수량은 각 항목의
        ``_raw``(shnu_cnqn_smtn/seln_cnqn_smtn/ntby_cnqn)(최대 30건)."""
        return ranking_api.fetch_company_trades(
            self._client.transport, side=side, start=start, end=end, market="KRX"
        )

    def by_dividend(
        self, *, kind: str = "cash", start: str | date, end: str | date,
        market: str = "all", settlement: str = "all",
    ) -> list[DividendRanking]:
        """배당률 순위. ``kind="cash"`` 현금배당 / ``"stock"`` 주식배당. ``start``/``end`` 는 배당
        기준일 범위(YYYYMMDD 문자열 또는 ``date``). ``market`` = ``"all"``/``"kospi"``/``"kospi200"``/
        ``"kosdaq"``, ``settlement`` = ``"all"``/``"final"``(결산)/``"interim"``(중간). 시세가 없어
        :class:`~kis_trader.ranking_items.DividendRanking` 항목을 돌려준다(최대 30건).

        ``dividend_rate`` 는 액면가 기준 배당률(%)이지 시장가 기준 배당수익률이 아니다."""
        return ranking_api.fetch_dividend(
            self._client.transport, kind=kind, start=start, end=end,
            market=market, settlement=settlement,
        )

    def by_short_sale(self, *, window: str = "1d") -> list[ShortSaleRanking]:
        """공매도 순위. ``window`` 조회기간 = ``"1d"``/``"2d"``/``"3d"``/``"4d"``/``"1w"``/``"2w"``/
        ``"3w"`` (일 단위) 또는 ``"1mo"``/``"2mo"``/``"3mo"`` (월 단위). 공매도 체결수량·거래량 비중·
        거래대금·평균가를 담은 :class:`~kis_trader.ranking_items.ShortSaleRanking` 를
        돌려준다(순위는 응답 순서, 최대 30건)."""
        return ranking_api.fetch_short_sale(self._client.transport, window=window, market="KRX")

    def by_credit_balance(
        self, *, metric: str = "margin_ratio", days: int = 2
    ) -> list[CreditBalanceRanking]:
        """신용잔고 순위. ``metric`` = 융자 ``"margin_ratio"``/``"margin_shares"``/``"margin_amount"``/
        ``"margin_ratio_increase"``/``"margin_ratio_decrease"`` 또는 대주 ``"loan_ratio"``/
        ``"loan_shares"``/``"loan_amount"``/``"loan_ratio_increase"``/``"loan_ratio_decrease"``.
        ``days`` 는 증가율 계산 기간(2~999). 융자/대주 잔고를 담은
        :class:`~kis_trader.ranking_items.CreditBalanceRanking` 를 돌려준다(최대 30건).

        융자잔고는 신용융자(빚내서 매수) 보유 잔고, 대주잔고는 대주(주식 빌려 매도) 잔고다."""
        return ranking_api.fetch_credit_balance(
            self._client.transport, metric=metric, days=days, market="KRX"
        )

    def by_near_high_low(self, *, side: str = "high") -> list[NearHighLowRanking]:
        """신고/신저 근접 순위. ``side="high"`` 신고가 근접 / ``"low"`` 신저가 근접. 신 최고/최저가와
        근접 비율을 담은 :class:`~kis_trader.ranking_items.NearHighLowRanking` 를 돌려준다(최대 30건)."""
        return ranking_api.fetch_near_high_low(self._client.transport, side=side, market="KRX")

    def by_expected_execution_change(self, *, direction: str = "up") -> list[RankedStock]:
        """장 시작 전 예상체결 기준 상승/하락 상위. ``direction="up"`` 상승 / ``"down"`` 하락. 예상체결가·
        예상체결량을 담은 :class:`~kis_trader.ranking_items.RankedStock` 로 돌려준다(최대 30건)."""
        return ranking_api.fetch_expected_execution_change(
            self._client.transport, direction=direction, market="KRX"
        )

    def by_expected_close(
        self,
        *,
        filter: str = "all",
        market: str = "all",
        extended_range: bool = False,
    ) -> list[RankedStock]:
        """장마감 예상체결 종목. ``filter`` 는 전체·상한·하한·상승·하락 필터의 영문 코드."""
        return ranking_api.fetch_expected_close(
            self._client.transport,
            filter_=filter,
            market=market,
            extended_range=extended_range,
        )

    def by_overtime_change(self, *, direction: str = "up") -> list[OvertimeRanking]:
        """시간외 단일가 등락률 순위. ``direction="up"`` 상승 / ``"down"`` 하락
        (:class:`~kis_trader.ranking_items.OvertimeRanking`, 최대 30건)."""
        return ranking_api.fetch_overtime_change(
            self._client.transport, direction=direction, market="KRX"
        )

    def by_overtime_volume(self) -> list[OvertimeRanking]:
        """시간외 단일가 거래량 순위(:class:`~kis_trader.ranking_items.OvertimeRanking`, 최대 30건)."""
        return ranking_api.fetch_overtime_volume(self._client.transport, market="KRX")

    def by_overtime_expected_change(self, *, direction: str = "up") -> list[OvertimeRanking]:
        """시간외 예상체결 등락률 순위. ``direction="up"`` 상승 / ``"down"`` 하락. 시간외 예상체결가·예상
        체결량을 담아 돌려준다(:class:`~kis_trader.ranking_items.OvertimeRanking`, 최대 30건)."""
        return ranking_api.fetch_overtime_expected_change(
            self._client.transport, direction=direction, market="KRX"
        )

    def by_after_hour_balance(self, *, side: str = "ask") -> list[AfterHoursBalanceRanking]:
        """시간외 잔량 순위. ``side="ask"`` 매도잔량 상위 / ``"bid"`` 매수잔량 상위. 시간외 총 매도/
        매수 잔량과 장전/장후 체결량을 담아 돌려준다
        (:class:`~kis_trader.ranking_items.AfterHoursBalanceRanking`, 최대 30건)."""
        return ranking_api.fetch_after_hour_balance(self._client.transport, side=side, market="KRX")

    def by_views(self) -> list[TopViewedStock]:
        """HTS 조회 상위 종목(관심 상위). 코드와 시장구분만 담은
        :class:`~kis_trader.ranking_items.TopViewedStock` 를 돌려준다."""
        return ranking_api.fetch_most_viewed(self._client.transport)
