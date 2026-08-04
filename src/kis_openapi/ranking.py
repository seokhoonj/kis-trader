"""시장 전체 순위 네임스페이스 -- :class:`RankingQueries`.

``kis.ranking.by_change(top="gainers")`` 처럼, 종목이 아니라 **시장 전체**를 어떤 기준으로 줄
세운 결과(:class:`~kis_openapi.ranked_stock.RankedStock` 리스트)를 돌려준다. KIS가 순위마다 URL을
다른 섹션(``/ranking/``, ``/quotations/``)에 두지만, 사용자에겐 "ranking" 하나로 모은다(섹션 계층
미러링 금지). 직접 만들지 않고 :attr:`~kis_openapi.client.KisClient.ranking` 로 얻는다.

각 순위는 한 번에 상위 한 페이지(대개 30건)만 주고 다음 조회가 없다(KIS 제약).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._domestic import ranking as ranking_api
from .ranked_stock import RankedStock

if TYPE_CHECKING:
    from .client import KisClient


class RankingQueries:
    """세션에 달린 순위 질의 네임스페이스. :meth:`KisClient.ranking` 이 만들어 준다."""

    def __init__(self, client: KisClient) -> None:
        self._client = client

    def by_change(self, *, top: str = "gainers") -> list[RankedStock]:
        """등락률 순위. ``top="gainers"`` 상승률 상위 / ``"losers"`` 하락률 상위(최대 30건)."""
        return ranking_api.fetch_fluctuation(self._client.transport, top=top, market="KRX")

    def by_volume(self) -> list[RankedStock]:
        """거래량 순위(최대 30건)."""
        return ranking_api.fetch_volume_rank(self._client.transport, market="KRX")

    def by_market_cap(self) -> list[RankedStock]:
        """시가총액 순위(최대 30건). 시가총액 값은 각 항목의 ``_raw['stck_avls']``."""
        return ranking_api.fetch_market_cap(self._client.transport, market="KRX")

    def by_disparity(self, *, top: str = "highest", period: int = 20) -> list[RankedStock]:
        """이격도 순위. ``top="highest"`` 이격도 상위 / ``"lowest"`` 하위. ``period`` 는 이동평균
        일수(5/10/20/60/120). 이격도 값은 각 항목의 ``_raw['d{period}_dsrt']``(%)(최대 30건)."""
        return ranking_api.fetch_disparity(
            self._client.transport, top=top, period=period, market="KRX"
        )

    def by_quote_balance(self, *, top: str = "net_buy") -> list[RankedStock]:
        """호가잔량 순위. ``top`` = ``"net_buy"`` 순매수잔량 / ``"net_sell"`` 순매도잔량 /
        ``"buy_ratio"`` 매수비율 / ``"sell_ratio"`` 매도비율. 잔량 지표는 ``_raw``(최대 30건)."""
        return ranking_api.fetch_quote_balance(self._client.transport, top=top, market="KRX")

    def by_volume_power(self) -> list[RankedStock]:
        """체결강도 순위(최대 30건). 당일 체결강도는 각 항목의 ``_raw['tday_rltv']``."""
        return ranking_api.fetch_volume_power(self._client.transport, market="KRX")

    def by_bulk_trades(self, *, top: str = "buy") -> list[RankedStock]:
        """대량체결건수 순위. ``top="buy"`` 매수상위 / ``"sell"`` 매도상위. 체결건수는 각 항목의
        ``_raw``(shnu_cntg_csnu/seln_cntg_csnu 등)(최대 30건)."""
        return ranking_api.fetch_bulk_trades(self._client.transport, top=top, market="KRX")

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
