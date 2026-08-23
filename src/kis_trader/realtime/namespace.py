"""국내 실시간 지역 네임스페이스 -- ``kis.realtime().domestic``.

REST ``kis.domestic`` 을 미러한다: ``domestic.futures(code).trades()`` 가
:class:`~kis_trader.realtime.subscription.RealtimeSubscription` 를 반환해 그 계약·그 타입만
소비한다. 파생 세부종류는 ``kind`` 인자로, 국내주식 거래소는 ``venue`` 인자로 TR 을 고른다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, cast

from ..errors import KISUsageError
from .parsers.derivatives import (
    DerivativeExecutionNotice,
    DerivativeOrderBook,
    FuturesTick,
    OptionTick,
)
from .parsers.domestic_stock import StockExecutionNotice, StockOrderBook

if TYPE_CHECKING:
    from .client import RealtimeClient
    from .messages import StockTradeTick
    from .subscription import RealtimeSubscription

StockVenue = Literal["KRX", "NXT", "unified"]
FuturesKind = Literal["index", "commodity", "stock", "night"]
OptionKind = Literal["index", "stock", "night"]
NoticeSession = Literal["regular", "night_futures", "night_option"]

_STOCK_TRADES_TR: dict[str, str] = {"KRX": "H0STCNT0", "NXT": "H0NXCNT0", "unified": "H0UNCNT0"}
_STOCK_ORDER_BOOK_TR: dict[str, str] = {"KRX": "H0STASP0", "NXT": "H0NXASP0", "unified": "H0UNASP0"}
_STOCK_EXECUTION_NOTICE_TR = "H0STCNI0"
_FUTURES_TRADES_TR: dict[str, str] = {
    "index": "H0IFCNT0", "commodity": "H0CFCNT0", "stock": "H0ZFCNT0", "night": "H0MFCNT0"
}
_FUTURES_ORDER_BOOK_TR: dict[str, str] = {
    "index": "H0IFASP0", "commodity": "H0CFASP0", "stock": "H0ZFASP0", "night": "H0MFASP0"
}
_OPTION_TRADES_TR: dict[str, str] = {"index": "H0IOCNT0", "stock": "H0ZOCNT0", "night": "H0EUCNT0"}
_OPTION_ORDER_BOOK_TR: dict[str, str] = {"index": "H0IOASP0", "stock": "H0ZOASP0", "night": "H0EUASP0"}
_DERIV_NOTICE_TR: dict[str, str] = {
    "regular": "H0IFCNI0", "night_futures": "H0MFCNI0", "night_option": "H0EUCNI0"
}


def _pick(table: dict[str, str], key: str, *, label: str) -> str:
    """``table`` 에서 ``key`` 로 TR 을 고른다. 미등록 키는 즉시 fail-closed(구독 전)."""
    try:
        return table[key]
    except KeyError:
        raise KISUsageError(
            f"알 수 없는 {label} '{key}' -- 가능한 값: {', '.join(sorted(table))}"
        ) from None


class StockHandle:
    """국내주식 실시간 핸들. ``venue`` 로 거래소(KRX/NXT/통합)를 고른다."""

    def __init__(self, client: RealtimeClient, code: str) -> None:
        self._c = client
        self._code = code

    def trades(
        self, venue: StockVenue = "KRX", *, on: Callable[[StockTradeTick], None] | None = None
    ) -> RealtimeSubscription[StockTradeTick]:
        tr = _pick(_STOCK_TRADES_TR, venue, label="venue")
        return cast(
            "RealtimeSubscription[StockTradeTick]", self._c._open_typed(tr, self._code, on=on)
        )

    def order_book(
        self, venue: StockVenue = "KRX", *, on: Callable[[StockOrderBook], None] | None = None
    ) -> RealtimeSubscription[StockOrderBook]:
        tr = _pick(_STOCK_ORDER_BOOK_TR, venue, label="venue")
        return cast(
            "RealtimeSubscription[StockOrderBook]", self._c._open_typed(tr, self._code, on=on)
        )


class FuturesHandle:
    """국내 선물 실시간 핸들. ``kind``: index|commodity|stock|night."""

    def __init__(self, client: RealtimeClient, code: str, kind: FuturesKind) -> None:
        self._c = client
        self._code = code
        self._trades_tr = _pick(_FUTURES_TRADES_TR, kind, label="kind")
        self._order_book_tr = _pick(_FUTURES_ORDER_BOOK_TR, kind, label="kind")

    def trades(
        self, *, on: Callable[[FuturesTick], None] | None = None
    ) -> RealtimeSubscription[FuturesTick]:
        return cast(
            "RealtimeSubscription[FuturesTick]", self._c._open_typed(self._trades_tr, self._code, on=on)
        )

    def order_book(
        self, *, on: Callable[[DerivativeOrderBook], None] | None = None
    ) -> RealtimeSubscription[DerivativeOrderBook]:
        return cast(
            "RealtimeSubscription[DerivativeOrderBook]",
            self._c._open_typed(self._order_book_tr, self._code, on=on),
        )


class OptionHandle:
    """국내 옵션 실시간 핸들. ``kind``: index|stock|night."""

    def __init__(self, client: RealtimeClient, code: str, kind: OptionKind) -> None:
        self._c = client
        self._code = code
        self._trades_tr = _pick(_OPTION_TRADES_TR, kind, label="kind")
        self._order_book_tr = _pick(_OPTION_ORDER_BOOK_TR, kind, label="kind")

    def trades(
        self, *, on: Callable[[OptionTick], None] | None = None
    ) -> RealtimeSubscription[OptionTick]:
        return cast(
            "RealtimeSubscription[OptionTick]", self._c._open_typed(self._trades_tr, self._code, on=on)
        )

    def order_book(
        self, *, on: Callable[[DerivativeOrderBook], None] | None = None
    ) -> RealtimeSubscription[DerivativeOrderBook]:
        return cast(
            "RealtimeSubscription[DerivativeOrderBook]",
            self._c._open_typed(self._order_book_tr, self._code, on=on),
        )


class ExecutionNotices:
    """체결통보 -- hts_id 단위(계약 아님). 자산군을 잎에서 고른다."""

    def __init__(self, client: RealtimeClient) -> None:
        self._c = client

    def stock(
        self, hts_id: str, *, on: Callable[[StockExecutionNotice], None] | None = None
    ) -> RealtimeSubscription[StockExecutionNotice]:
        return cast(
            "RealtimeSubscription[StockExecutionNotice]",
            self._c._open_typed(_STOCK_EXECUTION_NOTICE_TR, hts_id, on=on),
        )

    def derivative(
        self,
        hts_id: str,
        session: NoticeSession = "regular",
        *,
        on: Callable[[DerivativeExecutionNotice], None] | None = None,
    ) -> RealtimeSubscription[DerivativeExecutionNotice]:
        tr = _pick(_DERIV_NOTICE_TR, session, label="session")
        return cast(
            "RealtimeSubscription[DerivativeExecutionNotice]", self._c._open_typed(tr, hts_id, on=on)
        )


class RealtimeDomesticNamespace:
    """``kis.realtime().domestic`` -- 국내 실시간 시세/통보의 지역 네임스페이스."""

    def __init__(self, client: RealtimeClient) -> None:
        self._c = client
        self.execution_notices = ExecutionNotices(client)

    def stock(self, code: str) -> StockHandle:
        return StockHandle(self._c, code)

    def futures(self, code: str, kind: FuturesKind = "index") -> FuturesHandle:
        return FuturesHandle(self._c, code, kind)

    def option(self, code: str, kind: OptionKind = "index") -> OptionHandle:
        return OptionHandle(self._c, code, kind)
