"""기업행위 캘린더 네임스페이스 -- :class:`CalendarQueries`.

``kis.domestic.calendar.dividends(start=..., end=...)`` 처럼, 한국예탁결제원(KSD)이 제공하는 기업행위 일정을
기간 조회로 돌려준다. 종목 하나가 아니라 시장 전체(또는 지정 종목)의 예정 이벤트를 다루므로 종목 핸들
(:class:`~kis_trader.domestic.stock.DomesticStock`)이 아니라 세션에 달린 질의 네임스페이스다.

직접 만들지 않고 ``kis.domestic.calendar`` 로 얻는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._engine import ksd as ksd_api

if TYPE_CHECKING:
    from datetime import date

    from .entities.calendar import (
        AppraisalRights,
        BonusIssue,
        CapitalReduction,
        DividendEvent,
        ForfeitedShares,
        IPOSubscription,
        ListingEvent,
        MandatoryDeposit,
        MergerSplit,
        ParValueChange,
        RightsOffering,
        ShareholderMeeting,
    )
    from ..client import KISClient


class CalendarQueries:
    """세션에 달린 기업행위 캘린더 질의 네임스페이스. ``kis.domestic.calendar`` 가 만들어 준다.

    모든 조회가 기간(``start`` ~ ``end``, YYYYMMDD 또는 ``date``)을 받고, 대부분 ``symbol`` 로 특정
    종목만 좁힐 수 있다(생략하면 전체).
    """

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def dividends(
        self, *, start: str | date, end: str | date,
        symbol: str | None = None, dividend_kind: str = "all",
    ) -> list[DividendEvent]:
        """기간 [start, end] 의 배당 일정. ``symbol`` 지정 시 그 종목만, ``dividend_kind`` 는
        ``"all"``(전체) / ``"final"``(결산배당) / ``"interim"``(중간배당)."""
        return ksd_api.fetch_dividends(
            self._client.transport, start=start, end=end, symbol=symbol, dividend_kind=dividend_kind
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
        symbol: str | None = None, offering_date_basis: str = "subscription",
    ) -> list[RightsOffering]:
        """기간 [start, end] 의 유상증자 일정. ``offering_date_basis`` 는 ``"subscription"``(청약일별) /
        ``"record"``(기준일별). ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_rights_offerings(
            self._client.transport, start=start, end=end, symbol=symbol,
            offering_date_basis=offering_date_basis,
        )

    def bonus_issues(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[BonusIssue]:
        """기간 [start, end] 의 무상증자 일정. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_bonus_issues(
            self._client.transport, start=start, end=end, symbol=symbol
        )

    def capital_reductions(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[CapitalReduction]:
        """기간 [start, end] 의 자본감소 일정. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_capital_reductions(
            self._client.transport, start=start, end=end, symbol=symbol
        )

    def merger_splits(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[MergerSplit]:
        """기간 [start, end] 의 합병분할 일정. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_merger_splits(
            self._client.transport, start=start, end=end, symbol=symbol
        )

    def shareholder_meetings(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[ShareholderMeeting]:
        """기간 [start, end] 의 주주총회 일정. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_shareholder_meetings(
            self._client.transport, start=start, end=end, symbol=symbol
        )

    def mandatory_deposits(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[MandatoryDeposit]:
        """기간 [start, end] 의 의무예치 내역. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_mandatory_deposits(
            self._client.transport, start=start, end=end, symbol=symbol
        )

    def listings(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[ListingEvent]:
        """기간 [start, end] 의 상장정보. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_listings(
            self._client.transport, start=start, end=end, symbol=symbol
        )

    def par_value_changes(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[ParValueChange]:
        """기간 [start, end] 의 액면교체 일정. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_par_value_changes(
            self._client.transport, start=start, end=end, symbol=symbol
        )

    def forfeited_shares(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[ForfeitedShares]:
        """기간 [start, end] 의 실권주 일정. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_forfeited_shares(
            self._client.transport, start=start, end=end, symbol=symbol
        )

    def appraisal_rights(
        self, *, start: str | date, end: str | date, symbol: str | None = None
    ) -> list[AppraisalRights]:
        """기간 [start, end] 의 주식매수청구 일정. ``symbol`` 지정 시 그 종목만."""
        return ksd_api.fetch_appraisal_rights(
            self._client.transport, start=start, end=end, symbol=symbol
        )
