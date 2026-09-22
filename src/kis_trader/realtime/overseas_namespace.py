"""해외 실시간 지역 네임스페이스 -- ``kis.realtime().overseas``.

REST ``kis.overseas`` 를 미러한다: ``overseas.stock("AAPL").trades()`` 가
:class:`~kis_trader.realtime.subscription.RealtimeSubscription` 를 반환해 그 계약·그 타입만
소비한다. 해외주식 tr_key 는 RSYM(예: ``DNASAAPL`` = ``D`` + 거래소 + 심볼)이라 종목 마스터로
해석하고(클라이언트에 주입된 resolver -- :meth:`~kis_trader.realtime.client.RealtimeClient._resolve_instrument`),
해외선물옵션은 시리즈코드를 그대로 tr_key 로 쓴다. 해외주식 호가는 ``venue`` 로 글로벌(10호가)/아시아
(1호가) TR 을 고른다. 선물과 옵션은 같은 TR(HDFFF020/010)을 공유해 한 핸들로 다룬다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, cast

from ..errors import KISUsageError

if TYPE_CHECKING:
    from .client import RealtimeClient
    from .parsers.overseas import (
        AsiaDelayedOrderBook,
        DelayedTradeTick,
        FuturesExecutionNotice,
        FuturesOrderBook,
        FuturesOrderNotice,
        FuturesTradeTick,
        OverseasExecutionNotice,
        OverseasOrderBook,
    )
    from .subscription import RealtimeSubscription

#: 해외주식 실시간호가의 거래소권 -- global 미주/유럽 10호가 / asia 아시아 1호가.
OverseasVenue = Literal["global", "asia"]

_STOCK_TRADES_TR = "HDFSCNT0"
_STOCK_ORDER_BOOK_TR: dict[str, str] = {"global": "HDFSASP0", "asia": "HDFSASP1"}
# 체결통보 TR-id 는 모의(paper)에서 다르다 -- 실전 H0GSCNI0 / 모의 H0GSCNI9(원장 명시).
_STOCK_EXECUTION_NOTICE_TR = {"real": "H0GSCNI0", "paper": "H0GSCNI9"}
_FUTURES_TRADES_TR = "HDFFF020"
_FUTURES_ORDER_BOOK_TR = "HDFFF010"
_FUTURES_ORDER_NOTICE_TR = "HDFFF1C0"
_FUTURES_EXECUTION_NOTICE_TR = "HDFFF2C0"


def _pick(table: dict[str, str], key: str, *, label: str) -> str:
    """``table`` 에서 ``key`` 로 TR 을 고른다. 미등록 키는 즉시 fail-closed(구독 전)."""
    try:
        return table[key]
    except KeyError:
        raise KISUsageError(
            f"알 수 없는 {label} '{key}' -- 가능한 값: {', '.join(sorted(table))}"
        ) from None


class OverseasStockHandle:
    """해외주식 실시간 핸들. tr_key 는 종목 마스터로 해석한 RSYM. ``venue`` 로 호가 TR 을 고른다."""

    def __init__(self, client: RealtimeClient, realtime_symbol: str) -> None:
        self._c = client
        self._rsym = realtime_symbol

    def trades(
        self, *, on: Callable[[DelayedTradeTick], None] | None = None
    ) -> RealtimeSubscription[DelayedTradeTick]:
        """해외주식 실시간지연체결가를 구독하고 RealtimeSubscription[DelayedTradeTick] 을 반환한다(HDFSCNT0)."""
        return cast(
            "RealtimeSubscription[DelayedTradeTick]",
            self._c._open_typed(_STOCK_TRADES_TR, self._rsym, on=on),
        )

    def order_book(
        self,
        venue: OverseasVenue = "global",
        *,
        on: Callable[[OverseasOrderBook | AsiaDelayedOrderBook], None] | None = None,
    ) -> RealtimeSubscription[OverseasOrderBook | AsiaDelayedOrderBook]:
        """해외주식 실시간호가를 구독하고 RealtimeSubscription[OverseasOrderBook | AsiaDelayedOrderBook] 을 반환한다. venue: global(10호가 HDFSASP0)/asia(아시아 1호가 HDFSASP1)."""
        tr = _pick(_STOCK_ORDER_BOOK_TR, venue, label="venue")
        return cast(
            "RealtimeSubscription[OverseasOrderBook | AsiaDelayedOrderBook]",
            self._c._open_typed(tr, self._rsym, on=on),
        )


class OverseasFuturesHandle:
    """해외선물옵션 실시간 핸들. 선물/옵션이 같은 TR(HDFFF020/010)을 공유한다. tr_key 는 시리즈코드."""

    def __init__(self, client: RealtimeClient, series_code: str) -> None:
        self._c = client
        self._series_code = series_code

    def trades(
        self, *, on: Callable[[FuturesTradeTick], None] | None = None
    ) -> RealtimeSubscription[FuturesTradeTick]:
        """해외선물옵션 실시간체결가를 구독하고 RealtimeSubscription[FuturesTradeTick] 을 반환한다(HDFFF020)."""
        return cast(
            "RealtimeSubscription[FuturesTradeTick]",
            self._c._open_typed(_FUTURES_TRADES_TR, self._series_code, on=on),
        )

    def order_book(
        self, *, on: Callable[[FuturesOrderBook], None] | None = None
    ) -> RealtimeSubscription[FuturesOrderBook]:
        """해외선물옵션 실시간호가를 구독하고 RealtimeSubscription[FuturesOrderBook] 을 반환한다(HDFFF010)."""
        return cast(
            "RealtimeSubscription[FuturesOrderBook]",
            self._c._open_typed(_FUTURES_ORDER_BOOK_TR, self._series_code, on=on),
        )


class OverseasExecutionNotices:
    """해외 실시간 통보 -- hts_id 단위(계약 아님). 자산군을 잎에서 고른다. 암호화 프레임은 연결 계층이 복호화."""

    def __init__(self, client: RealtimeClient) -> None:
        self._c = client

    def stock(
        self, hts_id: str, *, on: Callable[[OverseasExecutionNotice], None] | None = None
    ) -> RealtimeSubscription[OverseasExecutionNotice]:
        """해외주식 실시간체결통보를 구독하고 RealtimeSubscription[OverseasExecutionNotice] 을 반환한다(H0GSCNI0). hts_id 단위."""
        return cast(
            "RealtimeSubscription[OverseasExecutionNotice]",
            self._c._open_typed(_STOCK_EXECUTION_NOTICE_TR[self._c.environment], hts_id, on=on),
        )

    def derivative_orders(
        self, hts_id: str, *, on: Callable[[FuturesOrderNotice], None] | None = None
    ) -> RealtimeSubscription[FuturesOrderNotice]:
        """해외선물옵션 실시간주문내역통보를 구독하고 RealtimeSubscription[FuturesOrderNotice] 을 반환한다(HDFFF1C0). hts_id 단위."""
        return cast(
            "RealtimeSubscription[FuturesOrderNotice]",
            self._c._open_typed(_FUTURES_ORDER_NOTICE_TR, hts_id, on=on),
        )

    def derivative_fills(
        self, hts_id: str, *, on: Callable[[FuturesExecutionNotice], None] | None = None
    ) -> RealtimeSubscription[FuturesExecutionNotice]:
        """해외선물옵션 실시간체결내역통보를 구독하고 RealtimeSubscription[FuturesExecutionNotice] 을 반환한다(HDFFF2C0). hts_id 단위."""
        return cast(
            "RealtimeSubscription[FuturesExecutionNotice]",
            self._c._open_typed(_FUTURES_EXECUTION_NOTICE_TR, hts_id, on=on),
        )


class RealtimeOverseasNamespace:
    """``kis.realtime().overseas`` -- 해외 실시간 시세/통보의 지역 네임스페이스."""

    def __init__(self, client: RealtimeClient) -> None:
        self._c = client
        self.execution_notices = OverseasExecutionNotices(client)

    def stock(self, symbol: str, *, exchange: str | None = None) -> OverseasStockHandle:
        """해외주식 실시간 핸들을 반환한다(.trades()/.order_book() 로 구독). ``exchange`` 를 생략하면 종목
        마스터로 자동 해석한다(첫 조회는 마스터를 받아 캐시). 같은 심볼이 여러 거래소면 ``exchange`` 를
        명시해야 한다(:class:`~kis_trader.errors.KISUsageError`). 종목 마스터에 접근할 수 없는 클라이언트
        (KISClient 없이 만든 RealtimeClient)면 fail-closed."""
        record = self._c._resolve_instrument(symbol, exchange)
        return OverseasStockHandle(self._c, record.realtime_symbol)

    def futures(self, series_code: str) -> OverseasFuturesHandle:
        """해외선물 실시간 핸들을 반환한다(.trades()/.order_book() 로 구독). ``series_code`` 는 시리즈코드(예: ESZ25)."""
        return OverseasFuturesHandle(self._c, series_code)

    def option(self, series_code: str) -> OverseasFuturesHandle:
        """해외옵션 실시간 핸들을 반환한다(선물과 같은 TR·핸들을 공유). ``series_code`` 는 시리즈코드."""
        return OverseasFuturesHandle(self._c, series_code)
