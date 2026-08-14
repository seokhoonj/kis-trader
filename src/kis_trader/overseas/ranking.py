"""해외주식 순위 네임스페이스 -- :class:`OverseasRankingQueries`.

``kis.overseas.ranking.by_volume(exchange="NAS")`` 처럼, 한 해외 거래소의 시장 전체 순위를
:class:`~kis_trader.overseas.entities.ranking.RankedOverseasStock` 리스트로 돌려준다. 국내 순위
(``kis.domestic.ranking``)의 해외 판이다 -- 해외는 거래소별로 조회하므로 ``exchange`` 를 준다.

직접 만들지 않고 ``kis.overseas.ranking`` 로 얻는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._engine import ranking as overseas_ranking_api

if TYPE_CHECKING:
    from ..client import KISClient
    from .entities.ranking import RankedOverseasStock


class OverseasRankingQueries:
    """세션에 달린 해외주식 순위 질의 네임스페이스. ``kis.overseas.ranking`` 이 만들어 준다.

    모든 순위가 거래소(``exchange``)를 받는다 -- 거래소코드는 NAS/NYS/AMS(미국), HKS(홍콩),
    SHS/SZS(중국), TSE(일본), HNX/HSX(베트남).
    """

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def by_volume(self, *, exchange: str) -> list[RankedOverseasStock]:
        """한 거래소의 거래량 순위."""
        return overseas_ranking_api.fetch_by_volume(self._client.transport, exchange=exchange)

    def by_amount(self, *, exchange: str) -> list[RankedOverseasStock]:
        """한 거래소의 거래대금 순위."""
        return overseas_ranking_api.fetch_by_amount(self._client.transport, exchange=exchange)

    def by_trade_growth(self, *, exchange: str) -> list[RankedOverseasStock]:
        """한 거래소의 거래증가율 순위."""
        return overseas_ranking_api.fetch_by_trade_growth(self._client.transport, exchange=exchange)

    def by_market_cap(self, *, exchange: str) -> list[RankedOverseasStock]:
        """한 거래소의 시가총액 순위."""
        return overseas_ranking_api.fetch_by_market_cap(self._client.transport, exchange=exchange)

    def by_change(self, *, exchange: str, top: str = "gainers") -> list[RankedOverseasStock]:
        """한 거래소의 등락률 순위. ``top="gainers"`` 상승률 상위 / ``"losers"`` 하락률 상위."""
        return overseas_ranking_api.fetch_by_change(
            self._client.transport, exchange=exchange, top=top
        )

    def by_volume_surge(self, *, exchange: str) -> list[RankedOverseasStock]:
        """한 거래소의 거래량 급증 순위."""
        return overseas_ranking_api.fetch_by_volume_surge(self._client.transport, exchange=exchange)

    def by_buy_strength(self, *, exchange: str) -> list[RankedOverseasStock]:
        """한 거래소의 매수 체결강도 순위."""
        return overseas_ranking_api.fetch_by_buy_strength(self._client.transport, exchange=exchange)

    def by_turnover(self, *, exchange: str) -> list[RankedOverseasStock]:
        """한 거래소의 거래 회전율 순위."""
        return overseas_ranking_api.fetch_by_turnover(self._client.transport, exchange=exchange)

    def by_price_fluctuation(
        self, *, exchange: str, top: str = "risers"
    ) -> list[RankedOverseasStock]:
        """한 거래소의 가격 급등/급락 순위. ``top="risers"`` 급등 상위 / ``"fallers"`` 급락 상위."""
        return overseas_ranking_api.fetch_by_price_fluctuation(
            self._client.transport, exchange=exchange, top=top
        )

    def by_new_highlow(
        self, *, exchange: str, extreme: str = "high", sustained: bool = True
    ) -> list[RankedOverseasStock]:
        """한 거래소의 신고가/신저가 순위. ``extreme="high"`` 신고 / ``"low"`` 신저,
        ``sustained=True`` 돌파유지 / ``False`` 일시돌파."""
        return overseas_ranking_api.fetch_by_new_highlow(
            self._client.transport, exchange=exchange, extreme=extreme, sustained=sustained
        )
