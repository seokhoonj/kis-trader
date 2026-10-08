"""해외 실시간 지역 네임스페이스 -- ``kis.realtime().overseas``.

REST ``kis.overseas`` 를 미러한다: ``overseas.stock("AAPL").trades()`` 가
:class:`~kis_trader.realtime.subscription.RealtimeSubscription` 를 반환해 그 계약·그 타입만
소비한다. 종목 마스터는 심볼을 **무접두 RSYM**(예: ``NASAAPL`` = 거래소 + 심볼)으로 해석하고
(클라이언트에 주입된 resolver -- :meth:`~kis_trader.realtime.client.RealtimeClient._resolve_instrument`),
해외주식 핸들이 지연피드 tr_key(``DNASAAPL`` = ``D`` + RSYM)를 만든다.
해외선물옵션은 시리즈코드를 그대로 tr_key 로 쓴다. 해외주식 호가는 ``zone`` 으로 글로벌(10호가)/아시아
(1호가) TR 을 고른다. 선물과 옵션은 같은 TR(HDFFF020/010)을 공유해 한 핸들로 다룬다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, cast, overload

from ..errors import KISUsageError

if TYPE_CHECKING:
    from .client import RealtimeClient
    from .parsers.overseas import (
        OverseasAsiaDelayedOrderBook,
        OverseasDelayedTradeTick,
        OverseasDerivativeExecutionNotice,
        OverseasDerivativeOrderBook,
        OverseasDerivativeOrderNotice,
        OverseasDerivativeTradeTick,
        OverseasExecutionNotice,
        OverseasOrderBook,
    )
    from .subscription import RealtimeSubscription

#: 해외주식 실시간호가의 거래소권 -- global 미주/유럽 10호가 / asia 아시아 1호가.
OverseasZone = Literal["global", "asia"]

_STOCK_TRADES_TR = "HDFSCNT0"
_STOCK_ORDER_BOOK_TR: dict[str, str] = {"global": "HDFSASP0", "asia": "HDFSASP1"}
# 체결통보 TR-id 는 모의(paper)에서 다르다 -- 실전 H0GSCNI0 / 모의 H0GSCNI9(원장 명시).
_STOCK_EXECUTION_NOTICE_TR: dict[str, str] = {"real": "H0GSCNI0", "paper": "H0GSCNI9"}
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
    """해외주식 실시간 핸들. tr_key 는 종목 마스터로 해석한 RSYM. ``zone`` 으로 호가 TR 을 고른다."""

    def __init__(self, client: RealtimeClient, realtime_symbol: str) -> None:
        self._client = client
        # 해외주식 실시간(지연시세) tr_key = "D"(지연피드) + 마스터 RSYM("NASAAPL" -> "DNASAAPL").
        # 접두가 없으면 서버가 프레임을 전혀 보내지 않는다(라이브 실증 2026-10-08). 이 접두 로직은
        # 해외주식 전용 -- 해외선물옵션은 시리즈코드 무접두가 정답이라 이 핸들을 거치지 않는다.
        # (미국주간거래 실시간은 "R"+시장구분 별도 체계라 미지원; 추후 지원 시 feed-mode 를 명시
        # 옵션으로 모델링할 것 -- 이 분기를 재사용하지 말 것.) startswith("D") 가드는 멱등용(실제
        # 마스터 RSYM 은 모두 무접두).
        self._rsym = realtime_symbol if realtime_symbol.startswith("D") else "D" + realtime_symbol

    def trades(
        self, *, on: Callable[[OverseasDelayedTradeTick], None] | None = None
    ) -> RealtimeSubscription[OverseasDelayedTradeTick]:
        """해외주식 실시간지연체결가를 구독하고 RealtimeSubscription[OverseasDelayedTradeTick] 을 반환한다(HDFSCNT0)."""
        return cast(
            "RealtimeSubscription[OverseasDelayedTradeTick]",
            self._client._open_typed(_STOCK_TRADES_TR, self._rsym, on=on),
        )

    @overload
    def order_book(
        self, zone: Literal["global"] = ..., *,
        on: Callable[[OverseasOrderBook], None] | None = ...,
    ) -> RealtimeSubscription[OverseasOrderBook]: ...
    @overload
    def order_book(
        self, zone: Literal["asia"], *,
        on: Callable[[OverseasAsiaDelayedOrderBook], None] | None = ...,
    ) -> RealtimeSubscription[OverseasAsiaDelayedOrderBook]: ...

    def order_book(
        self,
        zone: OverseasZone = "global",
        *,
        on: Callable[[Any], None] | None = None,
    ) -> RealtimeSubscription[Any]:
        """해외주식 실시간호가를 구독하고 RealtimeSubscription[OverseasOrderBook | OverseasAsiaDelayedOrderBook] 을 반환한다. zone: global(10호가 HDFSASP0)/asia(아시아 1호가 HDFSASP1).

        ``zone`` 리터럴이 결과 타입을 정한다(@overload): ``"global"`` -> ``OverseasOrderBook``,
        ``"asia"`` -> ``OverseasAsiaDelayedOrderBook`` -- 호출부가 유니온을 좁힐 필요가 없다."""
        tr = _pick(_STOCK_ORDER_BOOK_TR, zone, label="zone")
        return cast(
            "RealtimeSubscription[OverseasOrderBook | OverseasAsiaDelayedOrderBook]",
            self._client._open_typed(tr, self._rsym, on=on),
        )


class OverseasDerivativeHandle:
    """해외선물옵션 실시간 핸들. 선물/옵션이 같은 TR(HDFFF020/010)을 공유한다. tr_key 는 시리즈코드."""

    def __init__(self, client: RealtimeClient, symbol: str) -> None:
        self._client = client
        self._symbol = symbol

    def trades(
        self, *, on: Callable[[OverseasDerivativeTradeTick], None] | None = None
    ) -> RealtimeSubscription[OverseasDerivativeTradeTick]:
        """해외선물옵션 실시간체결가를 구독하고 RealtimeSubscription[OverseasDerivativeTradeTick] 을 반환한다(HDFFF020)."""
        return cast(
            "RealtimeSubscription[OverseasDerivativeTradeTick]",
            self._client._open_typed(_FUTURES_TRADES_TR, self._symbol, on=on),
        )

    def order_book(
        self, *, on: Callable[[OverseasDerivativeOrderBook], None] | None = None
    ) -> RealtimeSubscription[OverseasDerivativeOrderBook]:
        """해외선물옵션 실시간호가를 구독하고 RealtimeSubscription[OverseasDerivativeOrderBook] 을 반환한다(HDFFF010)."""
        return cast(
            "RealtimeSubscription[OverseasDerivativeOrderBook]",
            self._client._open_typed(_FUTURES_ORDER_BOOK_TR, self._symbol, on=on),
        )


class OverseasExecutionNotices:
    """해외 실시간 통보 -- hts_id 단위(계약 아님). 자산군을 잎에서 고른다. 암호화 프레임은 연결 계층이 복호화."""

    def __init__(self, client: RealtimeClient) -> None:
        self._client = client

    def stock(
        self, hts_id: str, *, on: Callable[[OverseasExecutionNotice], None] | None = None
    ) -> RealtimeSubscription[OverseasExecutionNotice]:
        """해외주식 실시간체결통보를 구독하고 RealtimeSubscription[OverseasExecutionNotice] 을 반환한다(실전 H0GSCNI0 / 모의 H0GSCNI9, 환경 자동 선택). hts_id 단위."""
        return cast(
            "RealtimeSubscription[OverseasExecutionNotice]",
            self._client._open_typed(_STOCK_EXECUTION_NOTICE_TR[self._client._environment], hts_id, on=on),
        )

    def derivative_orders(
        self, hts_id: str, *, on: Callable[[OverseasDerivativeOrderNotice], None] | None = None
    ) -> RealtimeSubscription[OverseasDerivativeOrderNotice]:
        """해외선물옵션 실시간주문내역통보를 구독하고 RealtimeSubscription[OverseasDerivativeOrderNotice] 을 반환한다(HDFFF1C0). hts_id 단위."""
        return cast(
            "RealtimeSubscription[OverseasDerivativeOrderNotice]",
            self._client._open_typed(_FUTURES_ORDER_NOTICE_TR, hts_id, on=on),
        )

    def derivative_fills(
        self, hts_id: str, *, on: Callable[[OverseasDerivativeExecutionNotice], None] | None = None
    ) -> RealtimeSubscription[OverseasDerivativeExecutionNotice]:
        """해외선물옵션 실시간체결내역통보를 구독하고 RealtimeSubscription[OverseasDerivativeExecutionNotice] 을 반환한다(HDFFF2C0). hts_id 단위."""
        return cast(
            "RealtimeSubscription[OverseasDerivativeExecutionNotice]",
            self._client._open_typed(_FUTURES_EXECUTION_NOTICE_TR, hts_id, on=on),
        )


class RealtimeOverseasNamespace:
    """``kis.realtime().overseas`` -- 해외 실시간 시세/통보의 지역 네임스페이스."""

    def __init__(self, client: RealtimeClient) -> None:
        self._client = client
        self.execution_notices = OverseasExecutionNotices(client)

    def stock(self, symbol: str, *, exchange: str | None = None) -> OverseasStockHandle:
        """해외주식 실시간 핸들을 반환한다(.trades()/.order_book() 로 구독). ``exchange`` 를 생략하면 종목
        마스터로 자동 해석한다(첫 조회는 마스터를 받아 캐시). 같은 심볼이 여러 거래소면 ``exchange`` 를
        명시해야 한다(:class:`~kis_trader.errors.KISUsageError`). 종목 마스터에 접근할 수 없는 클라이언트
        (KISClient 없이 만든 RealtimeClient)면 fail-closed."""
        record = self._client._resolve_instrument(symbol, exchange)
        return OverseasStockHandle(self._client, record.realtime_symbol)

    def futures(self, symbol: str) -> OverseasDerivativeHandle:
        """해외선물 실시간 핸들을 반환한다(.trades()/.order_book() 로 구독). ``symbol`` 는 시리즈코드(예: ESZ25)."""
        return OverseasDerivativeHandle(self._client, symbol)

    def option(self, symbol: str) -> OverseasDerivativeHandle:
        """해외옵션 실시간 핸들을 반환한다(선물과 같은 TR·핸들을 공유). ``symbol`` 는 시리즈코드."""
        return OverseasDerivativeHandle(self._client, symbol)
