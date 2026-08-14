"""ELW 시장 순위 네임스페이스 -- :class:`ELWRankingQueries`.

``kis.domestic.elw_ranking.by_volume()`` 처럼, 개별 ELW 가 아니라 **시장 전체 ELW** 를 어떤 기준으로 줄
세운 결과(:class:`~kis_trader.domestic.entities.elw.RankedELW` 리스트)를 돌려준다. 종목 순위(``kis.domestic.ranking``)
와 나란한 ELW 판이되, ELW 순위는 지표(그릭스·레버리지·변동성)가 고유해 별도 네임스페이스로 둔다.

직접 만들지 않고 ``kis.domestic.elw_ranking`` 로 얻는다. 각 순위는 한 번에
상위 한 페이지만 준다(KIS 제약). 필터는 기초자산(``underlying``)/발행사(``issuer``)/콜풋(``right``)로
좁힐 수 있다(기본은 전체).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._engine import elw as elw_api

if TYPE_CHECKING:
    from ..client import KISClient
    from .entities.elw import RankedELW


class ELWRankingQueries:
    """세션에 달린 ELW 순위 질의 네임스페이스. ``kis.domestic.elw_ranking`` 이 만들어 준다.

    공통 필터: ``underlying`` 은 기초자산 코드(``"000000"`` 전체, ``"005930"`` 삼성전자 등),
    ``issuer`` 는 발행사 코드(``"00000"`` 전체), ``right`` 는 ``"all"``/``"call"``/``"put"``.
    각 행의 순위 고유 지표(그릭스·레버리지·회전율 등)는 ``RankedELW._raw`` 에 있다.
    """

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def by_volume(
        self, *, sort: str = "volume",
        underlying: str = "000000", issuer: str = "00000", right: str = "all",
    ) -> list[RankedELW]:
        """거래량 순위. ``sort``: volume/turnover_growth/turnover_rate/amount/
        net_buy_balance/net_sell_balance."""
        return elw_api.fetch_ranking_by_volume(
            self._client.transport, sort=sort,
            underlying=underlying, issuer=issuer, right=right,
        )

    def by_change(
        self, *, sort: str = "gainers",
        underlying: str = "000000", issuer: str = "00000", right: str = "all",
    ) -> list[RankedELW]:
        """등락률 순위. ``sort``: gainers/losers/from_open_up/from_open_down/fluctuation."""
        return elw_api.fetch_ranking_by_change(
            self._client.transport, sort=sort,
            underlying=underlying, issuer=issuer, right=right,
        )

    def by_sensitivity(
        self, *, sort: str = "delta",
        underlying: str = "000000", issuer: str = "00000", right: str = "all",
    ) -> list[RankedELW]:
        """민감도 순위. ``sort``: theoretical/delta/gamma/rho/vega/implied_volatility/
        hist_volatility. 그릭스 값은 각 행의 ``_raw`` 에 있다."""
        return elw_api.fetch_ranking_by_sensitivity(
            self._client.transport, sort=sort,
            underlying=underlying, issuer=issuer, right=right,
        )

    def by_indicator(
        self, *, sort: str = "leverage",
        underlying: str = "000000", issuer: str = "00000", right: str = "all",
    ) -> list[RankedELW]:
        """투자지표 순위. ``sort``: conversion_ratio/leverage/strike/intrinsic_value/
        time_value. 지표 값은 각 행의 ``_raw`` 에 있다."""
        return elw_api.fetch_ranking_by_indicator(
            self._client.transport, sort=sort,
            underlying=underlying, issuer=issuer, right=right,
        )

    def quick_change(
        self, *, sort: str = "price_surge", window: str = "day",
        underlying: str = "000000", issuer: str = "00000",
    ) -> list[RankedELW]:
        """당일 급변 종목. ``sort``: price_surge/price_plunge/volume_surge/bid_surge/
        ask_surge, ``window``: ``"minute"``/``"day"``. 콜풋 필터는 없다."""
        return elw_api.fetch_ranking_quick_change(
            self._client.transport, sort=sort, window=window,
            underlying=underlying, issuer=issuer,
        )
