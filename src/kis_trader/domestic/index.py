"""지수/업종 핸들 -- :class:`Index`.

한 지수(업종)에 대해 조회를 시키는 핸들이다: ``kis.domestic.index("0001").quote()`` 처럼(0001=KOSPI 종합).
종목 핸들 :class:`~kis_trader.domestic.stock.DomesticStock` 와 대칭이며, 지수는 종목이 아니라 업종코드로 조회한다.

핸들은 :class:`~kis_trader.client.KISClient` 가 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..bar import Bar, Interval, PeriodInterval
from ._engine import index as index_api
from .entities.index import (
    ExpectedIndexPoint,
    ExpectedIndexSnapshot,
    IndexCategories,
    IndexDailyHistory,
    IndexIntradayPoint,
    IndexQuote,
)

if TYPE_CHECKING:
    from datetime import date

    from ..client import KISClient


#: 잘 알려진 지수 이름 -> 업종코드 별칭. 그 밖의 값(업종코드)은 그대로 통과한다.
_INDEX_NAME_TO_CODE = {
    "KOSPI": "0001",
    "코스피": "0001",
    "KOSDAQ": "1001",
    "코스닥": "1001",
    "KOSPI200": "2001",
    "코스피200": "2001",
}


def _resolve_index_code(code: str) -> str:
    """업종코드 또는 잘 알려진 지수 이름을 업종코드로 정규화한다.

    ``kis.domestic.index("KOSPI")`` 처럼 이름으로도 부를 수 있게 한다(영문 대소문자 무관).
    이미 업종코드(예: ``"0001"``)이거나 매핑에 없는 값이면 그대로 돌려준다.
    """
    return _INDEX_NAME_TO_CODE.get(code.strip().upper(), code)


class Index:
    """한 지수(업종)에 대한 조회 핸들. 세션(:class:`KISClient`)과 업종코드를 안다.

    보통 직접 만들지 않고 ``kis.domestic.index`` 로 얻는다. ``code`` 는 업종코드(예: 0001 KOSPI
    종합, 1001 KOSDAQ 종합, 2001 KOSPI200) 또는 잘 알려진 지수 이름(``"KOSPI"``/``"KOSDAQ"``/
    ``"KOSPI200"``, 영문 대소문자 무관)을 받는다 -- 이름은 생성 시점에 업종코드로 정규화되어
    ``code`` 에 저장된다.
    """

    code: str

    def __init__(self, client: KISClient, code: str) -> None:
        self._client = client
        self.code = _resolve_index_code(code)

    def quote(self) -> IndexQuote:
        """지수 현재가 스냅샷(레벨·시고저·등락종목수)."""
        return index_api.fetch_index_quote(self._client.transport, code=self.code)

    def bars(
        self,
        interval: Interval = "1d",
        *,
        start: str | date | None = None,
        end: str | date | None = None,
        max_bars: int | None = None,
    ) -> list[Bar]:
        """지수 봉(과거->현재). ``1m`` 은 최근 최대 102건이며 날짜 범위는 응답 안에서 거른다.
        ``1d``/``1wk``/``1mo`` 는 ``start`` 필요(``end`` 기본 오늘). OHLC는 지수 레벨이다."""
        return index_api.fetch_index_bars(
            self._client.transport, code=self.code,
            interval=interval, start=start, end=end, max_bars=max_bars,
        )

    def intraday(self, *, interval: str = "1m") -> list[IndexIntradayPoint]:
        """지수 당일 시간대별 시계열(과거->현재). ``interval="1m"``/``"5m"``/``"10m"`` 샘플 간격.
        각 점은 그 시각의 지수 레벨·전일대비·거래량이며, OHLC 캔들이 아니라 값 시계열이다."""
        return index_api.fetch_index_intraday(
            self._client.transport, code=self.code, interval=interval
        )

    def ticks(self) -> list[IndexIntradayPoint]:
        """지수 당일 10초 시계열(과거->현재)."""
        return index_api.fetch_index_ticks(self._client.transport, code=self.code)

    def daily_history(
        self,
        *,
        interval: PeriodInterval = "1d",
        as_of: str | date | None = None,
    ) -> IndexDailyHistory:
        """조회 시점 스냅샷과 최근 최대 100건의 일·주·월 지수 통계."""
        return index_api.fetch_index_daily_history(
            self._client.transport,
            code=self.code,
            interval=interval,
            as_of=as_of,
        )

    def expected_trend(
        self, *, session: str = "open", interval: str = "10s"
    ) -> list[ExpectedIndexPoint]:
        """장 시작 전·마감 동시호가의 예상체결 지수 추이."""
        return index_api.fetch_expected_index_trend(
            self._client.transport,
            code=self.code,
            session=session,
            interval=interval,
        )

    def expected_snapshot(
        self, *, market: str = "all", session: str = "open"
    ) -> ExpectedIndexSnapshot:
        """동시호가의 대표 예상체결 지수와 시장별 지수 목록."""
        return index_api.fetch_expected_index_snapshot(
            self._client.transport,
            code=self.code,
            market=market,
            session=session,
        )

    def categories(self) -> IndexCategories:
        """이 시장의 지수 요약(``.summary``)과 하위 업종 지수 목록(``.categories``). 시장 지수(``0001``
        KOSPI / ``1001`` KOSDAQ / ``2001`` KOSPI200) 핸들에서만 쓴다 -- 각 업종의 지수 레벨·전일대비와
        시장 내 거래량/거래대금 비중."""
        return index_api.fetch_index_categories(self._client.transport, code=self.code)
