"""채권 핸들 -- :class:`Bond`.

한 장내채권에 대해 조회를 시키는 핸들이다: ``kis.domestic.bond("KR2033022D33").quote()`` 처럼. 종목 핸들
:class:`~kis_trader.domestic.stock.DomesticStock` 와 대칭이며, 채권은 표준코드(ISIN)로 조회한다.

핸들은 :class:`~kis_trader.client.KISClient` 가 ``kis.domestic.bond(code)`` 로 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from ._engine import bonds as bonds_api
from ..bar import Bar, Interval
from ..bond_items import (
    BondDailyPrice,
    BondIssuance,
    BondProfile,
    BondQuote,
    BondValuation,
)

if TYPE_CHECKING:
    from ..client import KISClient
    from ..order_book import OrderBook
    from ..trade import Trade


class Bond:
    """한 채권에 대한 조회 핸들. 세션(:class:`KISClient`)과 표준코드를 안다.

    보통 직접 만들지 않고 ``kis.domestic.bond`` 로 얻는다. ``code`` 는 표준코드(ISIN).
    """

    code: str

    def __init__(self, client: KISClient, code: str) -> None:
        self._client = client
        self.code = code

    def profile(self) -> BondProfile:
        """채권 기본/발행 정보(발행일·만기·표면금리·만기수익률·통화)."""
        return bonds_api.fetch_profile(self._client.transport, code=self.code)

    def issuance(self) -> BondIssuance:
        """채권의 상세 발행 조건·발행기관·신용등급·거래 상태."""
        return bonds_api.fetch_issuance(self._client.transport, code=self.code)

    def quote(self) -> BondQuote:
        """채권 현재가 스냅샷(가격·시고저·전일대비·수익률)."""
        return bonds_api.fetch_quote(self._client.transport, code=self.code)

    def bars(self, interval: Interval = "1d") -> list[Bar]:
        """채권 일별 OHLCV를 과거->현재 오름차순으로. ``interval="1d"`` 만 지원한다."""
        return bonds_api.fetch_bars(self._client.transport, code=self.code, interval=interval)

    def daily_prices(self) -> list[BondDailyPrice]:
        """날짜별 채권 현재가·등락·OHLCV를 과거->현재 순으로."""
        return bonds_api.fetch_daily_prices(self._client.transport, code=self.code)

    def valuations(self, *, start: str | date, end: str | date) -> list[BondValuation]:
        """평가기관별 채권 단가·수익률의 일별 시계열을 과거->현재 순으로."""
        return bonds_api.fetch_valuations(
            self._client.transport, code=self.code, start=start, end=end
        )

    def order_book(self) -> OrderBook:
        """채권 호가창(5단계 매수/매도 심도)."""
        return bonds_api.fetch_order_book(self._client.transport, code=self.code)

    def trades(self) -> list[Trade]:
        """채권의 최근 체결 목록(최신순)."""
        return bonds_api.fetch_trades(self._client.transport, code=self.code)
