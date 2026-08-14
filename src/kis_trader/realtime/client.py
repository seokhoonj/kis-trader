"""실시간 동기 래퍼 -- :class:`RealtimeClient`.

async 코어(:class:`~kis_trader.realtime._connection.RealtimeConnection`)를 **백그라운드 스레드의
전용 이벤트루프**에서 돌리고, 동기 API 로 노출한다: ``subscribe(tr_id, tr_key, on=콜백)`` /
``stream()`` 이터레이터 / ``start()`` / ``stop()``. 패키지의 나머지(동기 REST)와 결이 같아
사용자가 asyncio 를 몰라도 된다. async 앱(FastAPI 등)은 코어(:class:`RealtimeConnection`)를
직접 쓰면 된다.

수신 메시지는 (1) ``tr_id`` 별 등록 콜백으로, (2) ``stream()`` 용 스레드-세이프 큐로 동시에 전달된다.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator

from ._connection import Connector, RealtimeConnection, RealtimeMessage
from ._protocol import CustomerType

MessageCallback = Callable[[RealtimeMessage], None]

_STREAM_SENTINEL = object()  # stream() 종료 신호


class RealtimeClient:
    """실시간 WebSocket 동기 클라이언트.

    보통 직접 만들지 않고 ``kis.realtime()`` 으로 얻는다. ``subscribe`` 로 등록(선택 콜백)한 뒤
    ``start()`` 하면 백그라운드에서 수신이 시작되고, 콜백 또는 ``for msg in client.stream()`` 로 받는다.
    """

    def __init__(
        self,
        approval_key: str,
        url: str,
        *,
        customer_type: CustomerType = "P",
        connect: Connector | None = None,
        reconnect: bool = True,
    ) -> None:
        self._approval_key = approval_key
        self._url = url
        self._customer_type: CustomerType = customer_type
        self._connect = connect
        self._reconnect = reconnect
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._conn: RealtimeConnection | None = None
        self._ready = threading.Event()  # 루프+연결 준비 완료
        self._lock = threading.Lock()
        self._callbacks: dict[str, list[MessageCallback]] = defaultdict(list)
        self._desired: set[tuple[str, str]] = set()  # start 전 등록 요청 보관
        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._startup_error: BaseException | None = None  # start() 로 전달할 연결/구독 실패

    # -- 구독 (start 전/후 모두 가능) --
    def subscribe(self, tr_id: str, tr_key: str, *, on: MessageCallback | None = None) -> None:
        """실시간 등록. ``on`` 콜백은 해당 ``tr_id`` 수신 시 호출된다. ``start()`` 전이면 보관해
        연결 후 자동 전송한다."""
        if on is not None:
            with self._lock:
                self._callbacks[tr_id].append(on)
        with self._lock:
            self._desired.add((tr_id, tr_key))
        if self._running and self._conn is not None and self._loop is not None:
            self._call_async(self._conn.subscribe(tr_id, tr_key))

    def unsubscribe(self, tr_id: str, tr_key: str) -> None:
        """실시간 해제."""
        with self._lock:
            self._desired.discard((tr_id, tr_key))
        if self._running and self._conn is not None and self._loop is not None:
            self._call_async(self._conn.unsubscribe(tr_id, tr_key))

    # -- 수명주기 --
    def start(self) -> None:
        """백그라운드 수신 시작. 연결이 열리고 보관된 구독이 전송될 때까지 블록한다.

        연결/초기구독이 실패하면 hang 하지 않고 그 예외를 호출자에게 그대로 raise 한다.
        """
        if self._running:
            return
        self._startup_error = None
        self._thread = threading.Thread(target=self._run, name="kis-realtime", daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            self._thread.join(timeout=5.0)
            raise self._startup_error
        self._running = True

    def stop(self, *, timeout: float = 5.0) -> None:
        """수신 중단 및 스레드 종료. 백그라운드 루프가 이미 끝났으면 join 만 한다."""
        if not self._running:
            return
        self._running = False
        if self._conn is not None and self._loop is not None and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._conn.stop(), self._loop)
                future.result(timeout=timeout)
            except Exception:  # noqa: BLE001 - 루프가 그 사이 종료됐을 수 있음
                pass
        self._queue.put(_STREAM_SENTINEL)
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def __enter__(self) -> RealtimeClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- 소비 --
    def stream(self, *, timeout: float | None = None) -> Iterator[RealtimeMessage]:
        """수신 메시지를 동기 이터레이터로. ``stop()`` 시 또는 ``timeout`` 초과 시 종료."""
        while True:
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                return
            if item is _STREAM_SENTINEL:
                return
            yield item

    # -- 내부: 백그라운드 스레드 --
    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()

    async def _main(self) -> None:
        self._conn = RealtimeConnection(
            self._approval_key,
            self._url,
            connect=self._connect,
            customer_type=self._customer_type,
            reconnect=self._reconnect,
        )
        try:
            await self._conn.__aenter__()
            with self._lock:
                desired = list(self._desired)
            for tr_id, tr_key in desired:
                await self._conn.subscribe(tr_id, tr_key)
        except BaseException as exc:  # noqa: BLE001 - 실패를 start() 로 전달(hang 방지)
            self._startup_error = exc
            await self._conn.close()  # 소켓 누수 방지
            self._queue.put(_STREAM_SENTINEL)
            self._ready.set()
            return
        self._ready.set()  # 준비 완료 신호
        try:
            async for message in self._conn:
                self._dispatch(message)
        finally:
            await self._conn.close()
            self._queue.put(_STREAM_SENTINEL)

    def _dispatch(self, message: RealtimeMessage) -> None:
        with self._lock:
            callbacks = list(self._callbacks.get(message.tr_id, ()))
        for callback in callbacks:
            try:
                callback(message)
            except Exception:  # noqa: BLE001 - 콜백 오류가 수신 루프를 죽이지 않게
                pass
        self._queue.put(message)

    def _call_async(self, coro, *, timeout: float = 5.0) -> None:
        """백그라운드 루프에 코루틴을 제출하고 완료를 기다린다(예외 전파).

        루프가 돌고 있지 않으면(이미 종료) 코루틴을 닫고 조용히 무시한다.
        """
        if self._loop is None or not self._loop.is_running():
            coro.close()
            return
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.result(timeout=timeout)
