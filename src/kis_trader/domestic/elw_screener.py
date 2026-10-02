"""ELW 스크리닝/기초자산 조회 네임스페이스 -- :class:`ELWScreenerQueries`.

``kis.domestic.elw_screener.underlyings()`` 처럼, 개별 ELW 가 아니라 **시장에서 ELW 를 찾는** 조회를 모은다:
ELW 가 상장된 기초자산 목록, 한 기초자산의 ELW 들, 신규상장/만기예정 ELW, 비교대상 ELW. 순위
(``kis.domestic.elw_ranking``)가 "줄 세우기"라면 스크리너는 "골라내기"다.

직접 만들지 않고 ``kis.domestic.elw_screener`` 로 얻는다. 목록 행은
:class:`~kis_trader.domestic.entities.elw.ELWListing`(기초자산 목록만 :class:`~kis_trader.domestic.entities.elw.ELWUnderlying`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ._engine import elw as elw_api

if TYPE_CHECKING:
    from ..client import KISClient
    from .entities.elw import ELWListing, ELWUnderlying


#: 콜풋 구분 -- 전체(all)/콜(call)/풋(put). 콜풋 코드는 조회마다 다르지만 어휘는 동일하다.
ELWRight = Literal["all", "call", "put"]

#: 기초자산 목록 정렬 기준.
UnderlyingSort = Literal["name", "call_count", "put_count", "gainers", "losers", "price"]


class ELWScreenerQueries:
    """세션에 달린 ELW 스크리닝 질의 네임스페이스. ``kis.domestic.elw_screener`` 가 만들어 준다.

    필터는 ``underlying``(기초자산 코드)/``issuer``(발행사 코드)/``right``(``"all"``/``"call"``/
    ``"put"``)로 좁힌다. 콜풋 코드는 KIS 가 조회마다 다르게 쓰지만(신규상장 02/00/01, 만기 2/0/1)
    사용자에겐 동일한 ``right`` 로 감춘다.
    """

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def underlyings(
        self, *, sort: UnderlyingSort = "name", issuer: str = "00000"
    ) -> list[ELWUnderlying]:
        """ELW 가 상장된 기초자산 목록. ``sort``: name/call_count/put_count/gainers/losers/price."""
        return elw_api.fetch_underlyings(self._client.transport, sort=sort, issuer=issuer)

    def by_underlying(
        self, underlying: str, *, issuer: str = "00000"
    ) -> list[ELWListing]:
        """한 기초자산에 상장된 ELW 목록(시세 포함). ``underlying`` 은 기초자산 코드(삼성전자 005930,
        KOSPI200 2001 등)."""
        return elw_api.fetch_by_underlying(
            self._client.transport, underlying=underlying, issuer=issuer
        )

    def comparables(self, underlying: str) -> list[ELWListing]:
        """한 기초자산의 비교대상 ELW 목록(코드/이름만). ``underlying`` 은 기초자산 코드."""
        return elw_api.fetch_comparables(self._client.transport, underlying=underlying)

    def newly_listed(
        self, *, date: str, right: ELWRight = "all",
        underlying: str = "000000", issuer: str | None = None,
    ) -> list[ELWListing]:
        """신규상장 ELW 목록. ``date`` 는 기준일(YYYYMMDD). ``issuer`` 는 발행사 코드로 KIS 가 이
        조회에서만 **필수**로 요구하며 '전 발행사'(``"00000"``)는 거부된다 -- 생략하면 라이브러리 기본
        발행사(한국투자증권)를 쓰고, 다른 발행사는 그 코드(예: ``"00017"`` KB증권)를 준다."""
        # issuer 기본은 엔진(fetch_newly_listed)이 쥔다 -- 여기서 "00000" 을 재기술하면 거부값을
        # 덮어씌운다. 준 값만 전달하고, 안 주면 엔진 기본이 적용되게 한다.
        extra = {} if issuer is None else {"issuer": issuer}
        return elw_api.fetch_newly_listed(
            self._client.transport, date=date, right=right, underlying=underlying, **extra,
        )

    def expiring(
        self, *, start: str, end: str, right: ELWRight = "all",
        underlying: str = "000000", issuer: str = "00000",
    ) -> list[ELWListing]:
        """만기예정 ELW 목록. ``[start, end]`` 는 만기일 구간(YYYYMMDD)."""
        return elw_api.fetch_expiring(
            self._client.transport, start=start, end=end, right=right,
            underlying=underlying, issuer=issuer,
        )

    def search(self, *, underlying: str = "", issuer: str = "") -> list[ELWListing]:
        """조건검색으로 ELW 목록(그릭스·지표 포함). ``underlying``/``issuer`` 로 좁힐 수 있고(공백=
        전체), 세부 수치 필터는 무필터. 그릭스·지표·LP 보유 등 풍부한 필드는 각 행의 ``_raw`` 에 있다."""
        return elw_api.fetch_search(
            self._client.transport, underlying=underlying, issuer=issuer
        )
