"""기업행위 캘린더 네임스페이스 -- :class:`CalendarQueries`.

``kis.calendar.dividends(start=..., end=...)`` 처럼, 한국예탁결제원(KSD)이 제공하는 기업행위 일정을
기간 조회로 돌려준다. 종목 하나가 아니라 시장 전체(또는 지정 종목)의 예정 이벤트를 다루므로 종목 핸들
(:class:`~kis_openapi.ticker.Ticker`)이 아니라 세션에 달린 질의 네임스페이스다.

직접 만들지 않고 :attr:`~kis_openapi.client.KISClient.calendar` 로 얻는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._domestic import ksd as ksd_api

if TYPE_CHECKING:
    from datetime import date

    from .calendar_items import (
        BonusIssue,
        DividendEvent,
        IPOSubscription,
        RightsOffering,
    )
    from .client import KISClient


class CalendarQueries:
    """세션에 달린 기업행위 캘린더 질의 네임스페이스. :attr:`KISClient.calendar` 가 만들어 준다.

    모든 조회가 기간(``start`` ~ ``end``, YYYYMMDD 또는 ``date``)을 받고, 대부분 ``symbol`` 로 특정
    종목만 좁힐 수 있다(생략하면 전체).
    """

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def dividends(
        self, *, start: str | date, end: str | date,
        symbol: str | None = None, kind: str = "all",
    ) -> list[DividendEvent]:
        """기간 [start, end] 의 배당 일정. ``symbol`` 지정 시 그 종목만, ``kind`` 는
        ``"all"``(전체) / ``"final"``(결산배당) / ``"interim"``(중간배당)."""
        return ksd_api.fetch_dividends(
            self._client.transport, start=start, end=end, symbol=symbol, kind=kind
        )

    def ipo_subscriptions(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[IPOSubscription]:
        """기간 [start, end] 의 공모주 청약 일정. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_ipo_subscriptions(
            self._client.transport, start=start, end=end, symbol=symbol
        )

    def rights_offerings(
        self, *, start: str | date, end: str | date,
        symbol: str | None = None, basis: str = "subscription",
    ) -> list[RightsOffering]:
        """기간 [start, end] 의 유상증자 일정. ``basis`` 는 ``"subscription"``(청약일별) /
        ``"record"``(기준일별). ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_rights_offerings(
            self._client.transport, start=start, end=end, symbol=symbol, basis=basis
        )

    def bonus_issues(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[BonusIssue]:
        """기간 [start, end] 의 무상증자 일정. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_bonus_issues(
            self._client.transport, start=start, end=end, symbol=symbol
        )
