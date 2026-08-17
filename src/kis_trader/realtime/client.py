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
import logging
import queue
import threading
from collections import defaultdict
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import CancelledError as FutureCancelledError
from typing import Any, Self

from ..errors import RealtimeError
from ._connection import Connector, RealtimeConnection, RealtimeMessage
from ._protocol import CustomerType

MessageCallback = Callable[[RealtimeMessage], None]

_logger = logging.getLogger("kis_trader.realtime")
_STREAM_SENTINEL = object()  # stream() 종료 신호
_QUEUE_MAXSIZE = 10_000  # stream() 큐 상한(틱) -- 초과 시 오래된 것부터 드롭


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
        # stream() 소비자가 없거나 느려도 무한정 자라지 않게 상한을 둔다(콜백 전용 사용자 메모리 누수 방지).
        # 가득 차면 가장 오래된 틱을 버린다(시장데이터는 최신이 중요) -- 정책은 _dispatch 참고.
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._queue_overflow_warned = False
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
    def start(self, *, timeout: float = 15.0) -> None:
        """백그라운드 수신 시작. 연결이 열리고 보관된 구독이 전송될 때까지 블록한다.

        연결/초기구독이 실패하면 hang 하지 않고 그 예외를 호출자에게 그대로 raise 한다.
        ``timeout`` 내에 연결이 준비되지 않으면(느린/멎은 연결) :class:`RealtimeError` 를 던진다
        -- 무한 대기하지 않는다.
        """
        if self._running:
            return
        self._startup_error = None
        self._ready.clear()  # 재시작 시 이전 set 이 남아 조기 ready 로 오판되지 않게
        self._thread = threading.Thread(target=self._run, name="kis-realtime", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise RealtimeError(f"실시간 연결이 {timeout}s 내에 준비되지 않았습니다(연결 지연/실패).")
        if self._startup_error is not None:
            self._thread.join(timeout=5.0)
            raise self._startup_error
        self._running = True

    def stop(self, *, timeout: float = 5.0) -> None:
        """수신 중단 및 스레드 종료. 백그라운드 루프가 이미 끝났으면 join 만 한다."""
        if not self._running:
            return
        self._running = False
        stop_error: BaseException | None = None
        if self._conn is not None and self._loop is not None and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._conn.stop(), self._loop)
                future.result(timeout=timeout)
            except TimeoutError:
                # ws.close() 등이 멎음 -- 조용히 성공으로 보고하지 않고 강제 종료로 넘어간다.
                _logger.warning("realtime stop() 이 %ss 내에 끝나지 않아 강제 종료합니다", timeout)
            except (RuntimeError, FutureCancelledError):
                # 정상 종료 경합 -- 루프가 그 사이 종료됐거나(RuntimeError), _run 의 종료 정리가
                # 방금 제출한 stop() 코루틴을 pending 태스크로서 취소했다(FutureCancelledError).
                # 어느 쪽이든 연결은 _main 의 finally 가 이미 닫으므로 이 요청은 중복이라 조용히 넘어간다.
                pass
            except Exception as exc:
                stop_error = exc
                _logger.warning("realtime 연결 stop() 이 예외를 던졌습니다", exc_info=True)
        self._queue.put(_STREAM_SENTINEL)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                raise RealtimeError(
                    f"실시간 백그라운드 스레드가 {timeout}s 내에 종료되지 않았습니다(연결/루프 미정리)."
                )
        if stop_error is not None:
            raise stop_error

    def __enter__(self) -> Self:
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
            # 스케줄돼 있던 태스크(예: run_coroutine_threadsafe 로 넣은 stop())를 취소·수거한 뒤
            # 루프를 닫는다 -- "Task was destroyed but it is pending" 방지. 종료 직전 도착한 제출이
            # 새 태스크를 만들 수 있어 quiescent 할 때까지 반복한다.
            while True:
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                if not pending:
                    break
                for task in pending:
                    task.cancel()
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
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
            except Exception:
                _logger.warning("realtime callback for %s raised", message.tr_id, exc_info=True)
        self._enqueue(message)

    def _enqueue(self, message: RealtimeMessage) -> None:
        """stream() 큐에 적재. 상한 도달 시 가장 오래된 틱을 버리고 최신을 넣는다(콜백 전용/느린
        소비자여도 메모리가 무한정 자라지 않게)."""
        try:
            self._queue.put_nowait(message)
            return
        except queue.Full:
            pass
        try:
            self._queue.get_nowait()  # 가장 오래된 것 드롭
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(message)
        except queue.Full:
            pass
        if not self._queue_overflow_warned:
            self._queue_overflow_warned = True
            _logger.warning(
                "realtime stream 큐가 상한(%d)에 도달 -- 소비가 느리거나 없어 오래된 틱을 드롭합니다",
                _QUEUE_MAXSIZE,
            )

    def _call_async(self, coro: Coroutine[Any, Any, Any], *, timeout: float = 5.0) -> None:
        """백그라운드 루프에 코루틴을 제출하고 완료를 기다린다(예외 전파).

        루프가 돌고 있지 않으면(이미 종료) 코루틴을 닫고 조용히 무시한다. 콜백이 수신 루프
        스레드에서 subscribe/unsubscribe 를 부르면 자기 루프를 기다려 self-deadlock 이 되므로,
        그 경우엔 블록하지 않고 fire-and-forget 으로 스케줄한다.
        """
        if self._loop is None or not self._loop.is_running():
            coro.close()
            return
        if threading.current_thread() is self._thread:
            self._loop.call_soon_threadsafe(asyncio.ensure_future, coro)
            return
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.result(timeout=timeout)
