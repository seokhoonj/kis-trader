"""국내 실시간 지역 네임스페이스 -- ``kis.realtime().domestic``.

REST ``kis.domestic`` 을 미러한다: ``domestic.futures(symbol).trades()`` 가
:class:`~kis_trader.realtime.subscription.RealtimeSubscription` 를 반환해 그 계약·그 타입만
소비한다. 파생 세부종류는 ``kind`` 인자로, 국내주식 거래소는 ``exchange`` 인자로 TR 을 고른다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, cast

from ..errors import KISUsageError
from ..instrument import DomesticQuoteExchange
from .parsers.bond import BondIndexTick, BondOrderBook, BondTick
from .parsers.derivatives import (
    DerivativeExecutionNotice,
    DerivativeOrderBook,
    FuturesTick,
    OptionTick,
)
from .parsers.domestic_stock import StockExecutionNotice, StockOrderBook
from .parsers.elw import ELWExpectedConclusion, ELWOrderBook, ELWTick
from .parsers.index import IndexExpectedConclusion, IndexProgramTrade, IndexTick

if TYPE_CHECKING:
    from .client import RealtimeClient
    from .parsers.domestic_stock import StockTick
    from .subscription import RealtimeSubscription

FuturesKind = Literal["index", "commodity", "stock", "night"]
OptionKind = Literal["index", "stock", "night"]
NoticeSession = Literal["regular", "night_futures", "night_option"]

_STOCK_TRADES_TR: dict[str, str] = {"KRX": "H0STCNT0", "NXT": "H0NXCNT0", "UN": "H0UNCNT0"}
_STOCK_ORDER_BOOK_TR: dict[str, str] = {"KRX": "H0STASP0", "NXT": "H0NXASP0", "UN": "H0UNASP0"}
# 체결통보 TR-id 는 모의(paper)에서 다르다 -- 실전 H0STCNI0 / 모의 H0STCNI9(원장 명시).
_STOCK_EXECUTION_NOTICE_TR: dict[str, str] = {"real": "H0STCNI0", "paper": "H0STCNI9"}
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
_INDEX_TRADES_TR = "H0UPCNT0"
_INDEX_EXPECTED_CONCLUSION_TR = "H0UPANC0"
_INDEX_PROGRAM_TRADE_TR = "H0UPPGM0"
_ELW_TRADES_TR = "H0EWCNT0"
_ELW_ORDER_BOOK_TR = "H0EWASP0"
_ELW_EXPECTED_CONCLUSION_TR = "H0EWANC0"
_BOND_TRADES_TR = "H0BJCNT0"
_BOND_ORDER_BOOK_TR = "H0BJASP0"
_BOND_INDEX_TRADES_TR = "H0BICNT0"


def _pick(table: dict[str, str], key: str, *, label: str) -> str:
    """``table`` 에서 ``key`` 로 TR 을 고른다. 미등록 키는 즉시 fail-closed(구독 전)."""
    try:
        return table[key]
    except KeyError:
        raise KISUsageError(
            f"알 수 없는 {label} '{key}' -- 가능한 값: {', '.join(sorted(table))}"
        ) from None


class DomesticStockHandle:
    """국내주식 실시간 핸들. ``exchange`` 로 거래소(KRX/NXT/통합 UN)를 고른다."""

    def __init__(self, client: RealtimeClient, symbol: str) -> None:
        self._client = client
        self._symbol = symbol

    def trades(
        self,
        exchange: DomesticQuoteExchange = "KRX",
        *,
        on: Callable[[StockTick], None] | None = None,
    ) -> RealtimeSubscription[StockTick]:
        """국내주식 실시간 체결을 구독하고 RealtimeSubscription[StockTick] 을 반환한다. exchange: KRX/NXT/통합(UN)."""
        tr = _pick(_STOCK_TRADES_TR, exchange, label="exchange")
        return cast(
            "RealtimeSubscription[StockTick]", self._client._open_typed(tr, self._symbol, on=on)
        )

    def order_book(
        self,
        exchange: DomesticQuoteExchange = "KRX",
        *,
        on: Callable[[StockOrderBook], None] | None = None,
    ) -> RealtimeSubscription[StockOrderBook]:
        """국내주식 실시간 호가를 구독하고 RealtimeSubscription[StockOrderBook] 을 반환한다. exchange: KRX/NXT/통합(UN)."""
        tr = _pick(_STOCK_ORDER_BOOK_TR, exchange, label="exchange")
        return cast(
            "RealtimeSubscription[StockOrderBook]", self._client._open_typed(tr, self._symbol, on=on)
        )


class DomesticFuturesHandle:
    """국내 선물 실시간 핸들. ``kind``: index|commodity|stock|night."""

    def __init__(self, client: RealtimeClient, symbol: str, kind: FuturesKind) -> None:
        self._client = client
        self._symbol = symbol
        self._trades_tr = _pick(_FUTURES_TRADES_TR, kind, label="kind")
        self._order_book_tr = _pick(_FUTURES_ORDER_BOOK_TR, kind, label="kind")

    def trades(
        self, *, on: Callable[[FuturesTick], None] | None = None
    ) -> RealtimeSubscription[FuturesTick]:
        """국내 선물 실시간 체결을 구독하고 RealtimeSubscription[FuturesTick] 을 반환한다. kind 로 고른 선물군."""
        return cast(
            "RealtimeSubscription[FuturesTick]", self._client._open_typed(self._trades_tr, self._symbol, on=on)
        )

    def order_book(
        self, *, on: Callable[[DerivativeOrderBook], None] | None = None
    ) -> RealtimeSubscription[DerivativeOrderBook]:
        """국내 선물 실시간 호가를 구독하고 RealtimeSubscription[DerivativeOrderBook] 을 반환한다. kind 로 고른 선물군."""
        return cast(
            "RealtimeSubscription[DerivativeOrderBook]",
            self._client._open_typed(self._order_book_tr, self._symbol, on=on),
        )


class DomesticOptionHandle:
    """국내 옵션 실시간 핸들. ``kind``: index|stock|night."""

    def __init__(self, client: RealtimeClient, symbol: str, kind: OptionKind) -> None:
        self._client = client
        self._symbol = symbol
        self._trades_tr = _pick(_OPTION_TRADES_TR, kind, label="kind")
        self._order_book_tr = _pick(_OPTION_ORDER_BOOK_TR, kind, label="kind")

    def trades(
        self, *, on: Callable[[OptionTick], None] | None = None
    ) -> RealtimeSubscription[OptionTick]:
        """국내 옵션 실시간 체결을 구독하고 RealtimeSubscription[OptionTick] 을 반환한다. kind 로 고른 옵션군."""
        return cast(
            "RealtimeSubscription[OptionTick]", self._client._open_typed(self._trades_tr, self._symbol, on=on)
        )

    def order_book(
        self, *, on: Callable[[DerivativeOrderBook], None] | None = None
    ) -> RealtimeSubscription[DerivativeOrderBook]:
        """국내 옵션 실시간 호가를 구독하고 RealtimeSubscription[DerivativeOrderBook] 을 반환한다. kind 로 고른 옵션군."""
        return cast(
            "RealtimeSubscription[DerivativeOrderBook]",
            self._client._open_typed(self._order_book_tr, self._symbol, on=on),
        )


class DomesticIndexHandle:
    """국내지수 실시간 핸들. 지수 코드 단위(체결/예상체결/프로그램매매). 호가는 없다."""

    def __init__(self, client: RealtimeClient, code: str) -> None:
        self._client = client
        self._code = code

    def trades(
        self, *, on: Callable[[IndexTick], None] | None = None
    ) -> RealtimeSubscription[IndexTick]:
        """국내지수 실시간체결을 구독하고 RealtimeSubscription[IndexTick] 을 반환한다(H0UPCNT0)."""
        return cast(
            "RealtimeSubscription[IndexTick]",
            self._client._open_typed(_INDEX_TRADES_TR, self._code, on=on),
        )

    def expected_conclusion(
        self, *, on: Callable[[IndexExpectedConclusion], None] | None = None
    ) -> RealtimeSubscription[IndexExpectedConclusion]:
        """국내지수 실시간 예상체결을 구독하고 RealtimeSubscription[IndexExpectedConclusion] 을 반환한다(H0UPANC0)."""
        return cast(
            "RealtimeSubscription[IndexExpectedConclusion]",
            self._client._open_typed(_INDEX_EXPECTED_CONCLUSION_TR, self._code, on=on),
        )

    def program_trade(
        self, *, on: Callable[[IndexProgramTrade], None] | None = None
    ) -> RealtimeSubscription[IndexProgramTrade]:
        """국내지수 실시간 프로그램매매를 구독하고 RealtimeSubscription[IndexProgramTrade] 을 반환한다(H0UPPGM0)."""
        return cast(
            "RealtimeSubscription[IndexProgramTrade]",
            self._client._open_typed(_INDEX_PROGRAM_TRADE_TR, self._code, on=on),
        )


class DomesticELWHandle:
    """ELW 실시간 핸들. ELW 코드 단위(체결/호가/예상체결)."""

    def __init__(self, client: RealtimeClient, symbol: str) -> None:
        self._client = client
        self._symbol = symbol

    def trades(
        self, *, on: Callable[[ELWTick], None] | None = None
    ) -> RealtimeSubscription[ELWTick]:
        """ELW 실시간체결가를 구독하고 RealtimeSubscription[ELWTick] 을 반환한다(H0EWCNT0)."""
        return cast(
            "RealtimeSubscription[ELWTick]",
            self._client._open_typed(_ELW_TRADES_TR, self._symbol, on=on),
        )

    def order_book(
        self, *, on: Callable[[ELWOrderBook], None] | None = None
    ) -> RealtimeSubscription[ELWOrderBook]:
        """ELW 실시간호가를 구독하고 RealtimeSubscription[ELWOrderBook] 을 반환한다(H0EWASP0)."""
        return cast(
            "RealtimeSubscription[ELWOrderBook]",
            self._client._open_typed(_ELW_ORDER_BOOK_TR, self._symbol, on=on),
        )

    def expected_conclusion(
        self, *, on: Callable[[ELWExpectedConclusion], None] | None = None
    ) -> RealtimeSubscription[ELWExpectedConclusion]:
        """ELW 실시간 예상체결을 구독하고 RealtimeSubscription[ELWExpectedConclusion] 을 반환한다(H0EWANC0)."""
        return cast(
            "RealtimeSubscription[ELWExpectedConclusion]",
            self._client._open_typed(_ELW_EXPECTED_CONCLUSION_TR, self._symbol, on=on),
        )


class DomesticBondHandle:
    """일반채권 실시간 핸들. 채권 코드 단위(체결/호가)."""

    def __init__(self, client: RealtimeClient, symbol: str) -> None:
        self._client = client
        self._symbol = symbol

    def trades(
        self, *, on: Callable[[BondTick], None] | None = None
    ) -> RealtimeSubscription[BondTick]:
        """일반채권 실시간 체결을 구독하고 RealtimeSubscription[BondTick] 을 반환한다(H0BJCNT0)."""
        return cast(
            "RealtimeSubscription[BondTick]",
            self._client._open_typed(_BOND_TRADES_TR, self._symbol, on=on),
        )

    def order_book(
        self, *, on: Callable[[BondOrderBook], None] | None = None
    ) -> RealtimeSubscription[BondOrderBook]:
        """일반채권 실시간 호가를 구독하고 RealtimeSubscription[BondOrderBook] 을 반환한다(H0BJASP0)."""
        return cast(
            "RealtimeSubscription[BondOrderBook]",
            self._client._open_typed(_BOND_ORDER_BOOK_TR, self._symbol, on=on),
        )


class DomesticBondIndexHandle:
    """채권지수 실시간 핸들. 채권지수 코드 단위(체결). 일반채권과 코드 공간이 다르다."""

    def __init__(self, client: RealtimeClient, code: str) -> None:
        self._client = client
        self._code = code

    def trades(
        self, *, on: Callable[[BondIndexTick], None] | None = None
    ) -> RealtimeSubscription[BondIndexTick]:
        """채권지수 실시간 체결을 구독하고 RealtimeSubscription[BondIndexTick] 을 반환한다(H0BICNT0)."""
        return cast(
            "RealtimeSubscription[BondIndexTick]",
            self._client._open_typed(_BOND_INDEX_TRADES_TR, self._code, on=on),
        )


class DomesticExecutionNotices:
    """체결통보 -- hts_id 단위(계약 아님). 자산군을 잎에서 고른다."""

    def __init__(self, client: RealtimeClient) -> None:
        self._client = client

    def stock(
        self, hts_id: str, *, on: Callable[[StockExecutionNotice], None] | None = None
    ) -> RealtimeSubscription[StockExecutionNotice]:
        """국내주식 실시간 체결통보를 구독하고 RealtimeSubscription[StockExecutionNotice] 을 반환한다. hts_id 단위."""
        return cast(
            "RealtimeSubscription[StockExecutionNotice]",
            self._client._open_typed(_STOCK_EXECUTION_NOTICE_TR[self._client._environment], hts_id, on=on),
        )

    def derivative(
        self,
        hts_id: str,
        session: NoticeSession = "regular",
        *,
        on: Callable[[DerivativeExecutionNotice], None] | None = None,
    ) -> RealtimeSubscription[DerivativeExecutionNotice]:
        """국내 파생 실시간 체결통보를 구독하고 RealtimeSubscription[DerivativeExecutionNotice] 을 반환한다. session: regular/night_futures/night_option."""
        tr = _pick(_DERIV_NOTICE_TR, session, label="session")
        return cast(
            "RealtimeSubscription[DerivativeExecutionNotice]", self._client._open_typed(tr, hts_id, on=on)
        )


class RealtimeDomesticNamespace:
    """``kis.realtime().domestic`` -- 국내 실시간 시세/통보의 지역 네임스페이스."""

    def __init__(self, client: RealtimeClient) -> None:
        self._client = client
        self.execution_notices = DomesticExecutionNotices(client)

    def stock(self, symbol: str) -> DomesticStockHandle:
        """국내주식 실시간 핸들을 반환한다(.trades()/.order_book() 로 구독). exchange 는 잎에서 고른다."""
        return DomesticStockHandle(self._client, symbol)

    def futures(self, symbol: str, kind: FuturesKind = "index") -> DomesticFuturesHandle:
        """국내 선물 실시간 핸들을 반환한다(.trades()/.order_book() 로 구독). kind: index|commodity|stock|night."""
        return DomesticFuturesHandle(self._client, symbol, kind)

    def option(self, symbol: str, kind: OptionKind = "index") -> DomesticOptionHandle:
        """국내 옵션 실시간 핸들을 반환한다(.trades()/.order_book() 로 구독). kind: index|stock|night."""
        return DomesticOptionHandle(self._client, symbol, kind)

    def index(self, code: str) -> DomesticIndexHandle:
        """국내지수 실시간 핸들을 반환한다(.trades()/.expected_conclusion()/.program_trade() 로 구독)."""
        return DomesticIndexHandle(self._client, code)

    def elw(self, symbol: str) -> DomesticELWHandle:
        """ELW 실시간 핸들을 반환한다(.trades()/.order_book()/.expected_conclusion() 로 구독)."""
        return DomesticELWHandle(self._client, symbol)

    def bond(self, symbol: str) -> DomesticBondHandle:
        """일반채권 실시간 핸들을 반환한다(.trades()/.order_book() 로 구독)."""
        return DomesticBondHandle(self._client, symbol)

    def bond_index(self, code: str) -> DomesticBondIndexHandle:
        """채권지수 실시간 핸들을 반환한다(.trades() 로 구독). 일반채권과 코드 공간이 다르다."""
        return DomesticBondIndexHandle(self._client, code)
