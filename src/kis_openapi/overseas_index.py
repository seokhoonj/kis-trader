"""해외 지수/환율/국채/금선물 핸들 -- :class:`OverseasIndex`.

한 해외 지수류 심볼의 기간봉을 조회하는 핸들이다: ``kis.overseas_index(".DJI").bars(...)`` 처럼.
국내 :class:`~kis_openapi.index.Index` 와 대칭이며 첫 슬라이스에서는 기간봉만 지원한다.

핸들은 :class:`~kis_openapi.client.KISClient` 가 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._overseas import index as overseas_index_api
from .bar import Bar, Interval

if TYPE_CHECKING:
    from datetime import date

    from .client import KISClient


class OverseasIndex:
    """한 해외 지수류 심볼의 조회 핸들. 세션·심볼·시장구분을 안다.

    보통 직접 만들지 않고 :meth:`KISClient.overseas_index` 로 얻는다. ``symbol`` 은 지수코드(예:
    ``.DJI``), 종류는 팩토리의 ``kind`` 로 선택한다.
    """

    symbol: str

    def __init__(self, client: KISClient, symbol: str, *, market_division: str) -> None:
        self._client          = client
        self.symbol           = symbol
        self._market_division = market_division

    def bars(
        self,
        interval: Interval = "1d",
        *,
        start: str | date | None = None,
        end: str | date | None = None,
        max_bars: int | None = None,
    ) -> list[Bar]:
        """해외 지수류 기간봉(과거->현재)을 조회한다.

        ``interval`` 은 ``1d``/``1wk``/``1mo`` 이며 ``start`` 가 필요하고 ``end`` 는 기본 오늘이다.
        OHLCV 는 기존 :class:`Bar` 로 돌려주며 분봉(``1m``)은 아직 지원하지 않는다.
        """
        return overseas_index_api.fetch_bars(
            self._client.transport, symbol=self.symbol,
            market_division=self._market_division, interval=interval,
            start=start, end=end, max_bars=max_bars,
        )
