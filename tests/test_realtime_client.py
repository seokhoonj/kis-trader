"""실시간 동기 래퍼(client.RealtimeClient) 테스트 -- 가짜 WebSocket + 백그라운드 스레드.

콜백 전달, stream() 이터레이션, start/stop 수명주기를 실서버 없이 검증한다.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time

import pytest

from kis_trader.errors import RealtimeError
from kis_trader.realtime._connection import RealtimeMessage
from kis_trader.realtime.client import RealtimeClient


class FakeWebSocket:
    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent: list[str] = []
        self._closed = threading.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._closed.is_set() or not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)

    async def send(self, message):
        self.sent.append(message)

    async def pong(self, data):
        pass

    async def close(self):
        self._closed.set()


def _connector(ws):
    async def connect(url):
        return ws

    return connect


def _client(ws):
    # reconnect=False: fake 소진 시 스레드가 깔끔히 종료(무한 재연결 방지).
    return RealtimeClient("KEY", "ws://x", connect=_connector(ws), reconnect=False)


def test_callback_receives_messages_and_subscription_sent():
    ws = FakeWebSocket(
        incoming=[
            "0|DUMMYTR0|001|005930^093000^71500",
            "0|DUMMYTR0|001|005930^093001^71600",
        ]
    )
    received = []
    client = _client(ws)
    client.subscribe("DUMMYTR0", "005930", on=received.append)
    client.start()
    # 소비를 stream 으로 유도(백그라운드가 큐에 넣음), 스트림이 끝나면 프레임 소진됨.
    streamed = list(client.stream(timeout=2.0))
    client.stop()

    assert [m.data for m in received] == [
        ["005930", "093000", "71500"],
        ["005930", "093001", "71600"],
    ]
    assert [m.data for m in streamed] == [m.data for m in received]


def test_blocking_callback_does_not_stall_stream_delivery():
    # on= 콜백이 블로킹해도 수신 루프/stream() 전달은 막히지 않는다 -- 콜백은 전용 워커 스레드에서
    # 돌기 때문이다(과거엔 콜백이 이벤트루프 스레드에서 돌아 keepalive 응답을 고사시켰다).
    ws = FakeWebSocket(incoming=[
        "0|DUMMYTR0|001|005930^093000^71500",
        "0|DUMMYTR0|001|005930^093001^71600",
    ])
    entered = threading.Event()
    release = threading.Event()

    def blocking(_message):
        entered.set()
        release.wait(2.0)

    client = _client(ws)
    client.subscribe("DUMMYTR0", "005930", on=blocking)
    client.start()
    # 콜백이 워커에서 블로킹 중이어도 stream() 은 두 틱을 모두 전달한다(루프 미차단).
    streamed = list(client.stream(timeout=2.0))
    assert [m.data for m in streamed] == [
        ["005930", "093000", "71500"],
        ["005930", "093001", "71600"],
    ]
    assert entered.wait(2.0)   # 콜백은 워커 스레드에서 이미 시작돼(블로킹) 있다
    release.set()              # 풀어주면 워커가 나머지를 처리하고 종료한다
    client.stop()
    assert client._running is False
    # 구독 등록 메시지가 실제로 나갔는지
    sub = json.loads(ws.sent[0])
    assert sub["body"]["input"] == {"tr_id": "DUMMYTR0", "tr_key": "005930"}


def test_context_manager_starts_and_stops():
    ws = FakeWebSocket(incoming=["0|DUMMYTR0|001|005930^1"])
    got = []
    client = _client(ws)
    client.subscribe("DUMMYTR0", "005930", on=got.append)
    with client:
        list(client.stream(timeout=2.0))
    assert len(got) == 1
    assert not client._running  # stop 됨


def test_start_times_out_instead_of_hanging_on_slow_connect():
    # 연결이 시간 내에 안 뜨면 무한 대기하지 않고 RealtimeError 를 던진다.
    async def hanging_connect(url):
        await asyncio.sleep(30)  # 준비되지 않음

    client = RealtimeClient("KEY", "ws://x", connect=hanging_connect, reconnect=False)
    with pytest.raises(RealtimeError):
        client.start(timeout=0.3)
    assert client._running is False


def test_start_propagates_connector_failure_without_hanging():
    # 연결 실패 시 start() 는 hang 하지 않고 그 예외를 raise 한다(P0 회귀).
    async def failing_connect(url):
        raise ConnectionError("connect refused")

    client = RealtimeClient("KEY", "ws://x", connect=failing_connect, reconnect=False)
    with pytest.raises(ConnectionError):
        client.start()
    assert client._running is False


def test_startup_failure_terminates_typed_subscription():
    # 기동 실패(connect 예외) 시에도 start() 전에 만든 타입드 구독의 이터레이터가 hang 하면 안 된다
    # (P0: startup-error 경로가 _shutdown_subscriptions 를 호출해 per-sub 큐에 sentinel 을 넣는다).
    async def failing_connect(url):
        raise ConnectionError("connect refused")

    client = RealtimeClient("KEY", "ws://x", connect=failing_connect, reconnect=False)
    sub = client._open_typed("H0STASP0", "005930")    # start 전 타입드 구독
    with pytest.raises(ConnectionError):
        client.start()
    # 상한 있는 스레드로 소비 -- 퇴행(센티넬 누락)으로 이터레이터가 멎으면 hang 대신 실패로 드러난다.
    result: list[list[object]] = []
    worker = threading.Thread(target=lambda: result.append(list(sub)))
    worker.start()
    worker.join(timeout=5.0)
    assert not worker.is_alive(), "구독 이터레이터가 종료되지 않았다(센티넬 누락 퇴행)"
    assert result == [[]]                             # sentinel -> 즉시 종료


def test_subscribe_before_start_is_deferred_then_sent():
    ws = FakeWebSocket(incoming=[])
    client = _client(ws)
    client.subscribe("H0STASP0", "005930")  # start 전
    assert ws.sent == []  # 아직 미전송
    client.start()
    list(client.stream(timeout=1.0))
    client.stop()
    assert any("H0STASP0" in s for s in ws.sent)  # start 시 전송됨


class OpenWebSocket:
    """메시지 없이 열린 채로 유지되는 가짜 소켓 -- start 후 등록/해제 프레임을 관찰하려면
    수신 루프가 곧장 끝나지 않고 살아 있어야 한다(close 될 때까지 __anext__ 가 대기)."""

    def __init__(self):
        self.sent: list[str] = []
        self._closed = asyncio.Event()
        # 송신이 일어날 때마다 set 되는 cross-thread 동기점. 메인 스레드가 벽시계 폴링 대신
        # 이 이벤트에 블록해, 소켓 스레드가 프레임을 보내는 즉시 깨어난다(느린 CI 에서도 결정적).
        self.sent_event = threading.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._closed.wait()
        raise StopAsyncIteration

    async def send(self, message):
        self.sent.append(message)   # append 를 먼저 -- 깨어난 대기자가 pred 로 리스트를 읽을 때 보이도록
        self.sent_event.set()

    async def pong(self, data):
        pass

    async def close(self):
        self._closed.set()


def _open_client(ws):
    return RealtimeClient("KEY", "ws://x", connect=_connector(ws), reconnect=False)


def _wait_until(ws, pred, timeout=2.0):
    # 소켓이 프레임을 보낼 때마다 set 되는 이벤트에 블록한다 -- 벽시계 sleep 폴링이 아니라
    # 송신 즉시 깨어나 pred 를 재평가하므로 느린 CI 에서도 결정적이다. timeout 은 pred 가 끝내
    # 참이 되지 않는 실패에서 테스트가 매달리지 않게 하는 안전망일 뿐이다(정상 경로에선 닿지 않음).
    deadline = time.monotonic() + timeout
    while not pred():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return pred()
        ws.sent_event.wait(remaining)
        ws.sent_event.clear()       # 다음 송신을 기다리기 위해 리셋(append 가 set 에 선행하므로 손실 없음)
    return True


def _frames_of(ws, tr_type, tr_id):
    out = []
    for raw in ws.sent:
        frame = json.loads(raw)
        if frame["header"]["tr_type"] == tr_type and frame["body"]["input"]["tr_id"] == tr_id:
            out.append(frame)
    return out


def test_typed_subscription_refcount_wire_first_subscribe_last_unsubscribe():
    # 같은 (tr_id, tr_key) 에 타입드 구독 둘 -> wire subscribe 는 한 번만; 첫 close 는 wire 미해제,
    # 마지막 close 에만 한 번 해제. wire 예약은 비블로킹이라 프레임을 폴링으로 관찰한다.
    ws = OpenWebSocket()
    client = _open_client(ws)
    client.start()
    s1 = client._open_typed("H0IFCNT0", "101W09")
    s2 = client._open_typed("H0IFCNT0", "101W09")
    assert _wait_until(ws, lambda: len(_frames_of(ws, "1", "H0IFCNT0")) == 1)
    assert len(_frames_of(ws, "1", "H0IFCNT0")) == 1  # 첫 구독만 등록(중복 없음)

    s1.close()  # 마지막 아님 -> wire 해제 예약 안 함(코드상 last 에서만)
    s2.close()  # 마지막 -> 한 번 해제
    assert _wait_until(ws, lambda: len(_frames_of(ws, "2", "H0IFCNT0")) == 1)
    client.stop()
    assert len(_frames_of(ws, "1", "H0IFCNT0")) == 1  # 등록 정확히 1
    assert len(_frames_of(ws, "2", "H0IFCNT0")) == 1  # 해제 정확히 1


def test_subscribe_after_start_sends_frame():
    # start 후 등록은 보관만 하지 않고 즉시 전송된다(_call_async 의 블로킹 브랜치).
    ws = OpenWebSocket()
    client = _open_client(ws)
    client.start()
    client.subscribe("H0STASP0", "005930")
    client.stop()
    assert any("H0STASP0" in s for s in ws.sent)


def test_unsubscribe_sends_unregister_frame_and_discards():
    ws = OpenWebSocket()
    client = _open_client(ws)
    client.subscribe("H0STASP0", "005930")
    client.start()
    client.unsubscribe("H0STASP0", "005930")
    client.stop()
    assert ("H0STASP0", "005930") not in client._desired
    frames = [json.loads(s) for s in ws.sent]
    assert any(f["header"]["tr_type"] == "1" for f in frames)   # 등록(1)
    assert any(f["header"]["tr_type"] == "2" for f in frames)   # 해제(2)


def test_subscribe_from_callback_does_not_deadlock():
    # 콜백(수신 스레드)에서 subscribe 를 부르면 자기 루프를 기다려 self-deadlock 이 될 수 있다 --
    # fire-and-forget 로 스케줄해 막는다. 데드락이면 stop() 의 join 이 풀리지 않아 실패한다.
    ws = FakeWebSocket(incoming=["0|DUMMYTR0|001|005930^1"])
    client = _client(ws)

    def on_msg(_message):
        client.subscribe("H0STASP0", "000660")

    client.subscribe("DUMMYTR0", "005930", on=on_msg)
    client.start()
    list(client.stream(timeout=2.0))
    client.stop()
    assert client._running is False


def test_stop_from_callback_raises_realtime_error_and_main_thread_can_stop():
    client = RealtimeClient("KEY", "ws://x", reconnect=False)
    callback_finished = threading.Event()
    callback_errors: list[BaseException] = []

    def on_msg(_message):
        try:
            client.stop()
        except BaseException as exc:  # noqa: BLE001 -- RuntimeError 퇴행도 단언하려고 포착
            callback_errors.append(exc)
        finally:
            callback_finished.set()

    client.subscribe("DUMMYTR0", "005930", on=on_msg)
    client._running = True
    client._dispatch_thread = threading.Thread(target=client._dispatch_worker, daemon=True)
    client._dispatch_thread.start()
    client._dispatch(RealtimeMessage("DUMMYTR0", "005930", ["tick"]))

    assert callback_finished.wait(2.0)
    assert len(callback_errors) == 1
    assert isinstance(callback_errors[0], RealtimeError)
    client.stop()
    assert client._running is False


def test_stop_does_not_reraise_cancelled_stop_coroutine(monkeypatch):
    # 종료 경합: _main 이 자연 종료하면 _run 의 정리 코드가 stop() 이 제출한 _conn.stop() 태스크를
    # pending 으로 보고 cancel 한다 -- 그러면 future.result() 가 concurrent.futures.CancelledError 를
    # 던진다. 이는 정상 종료 경합이므로 stop() 이 다시 던지지 않아야 한다(3.12 CI flaky 회귀).
    import concurrent.futures

    from kis_trader.realtime import client as client_module

    ws = OpenWebSocket()
    client = _open_client(ws)
    client.start()

    class _CancelledFuture:
        def result(self, timeout=None):
            raise concurrent.futures.CancelledError()

    def _fake_submit(coro, loop):
        coro.close()                                    # 실제 _conn.stop() 은 건너뛴다("never awaited" 방지)
        loop.call_soon_threadsafe(ws._closed.set)       # 대신 소켓을 닫아 백그라운드 스레드가 종료되게
        return _CancelledFuture()

    monkeypatch.setattr(client_module.asyncio, "run_coroutine_threadsafe", _fake_submit)

    client.stop()                                       # CancelledError 를 삼켜야 한다(재던지기 금지)
    assert client._running is False


def test_enqueue_drops_oldest_when_queue_full():
    # 상한 도달 시 가장 오래된 틱을 버리고 최신을 넣는다(콜백 전용/느린 소비자 메모리 누수 방지).
    client = RealtimeClient("KEY", "ws://x")
    client._queue = queue.Queue(maxsize=2)
    first, second, third = (
        RealtimeMessage(tr_id="T", tr_key="1", data=[str(i)]) for i in range(3)
    )
    for message in (first, second, third):
        client._enqueue(message)
    drained = []
    while not client._queue.empty():
        drained.append(client._queue.get_nowait())
    assert drained == [second, third]                # 가장 오래된 first 는 드롭
    assert client._queue_overflow_warned is True


def test_put_sentinel_does_not_block_when_queue_full():
    # 콜백 전용 소비자(stream() 미소비)로 중앙 큐가 상한에 차 있으면 블로킹 put 은 teardown 을
    # 영원히 멎게 한다(P0 교착). _put_sentinel 은 오래된 것을 버리며 non-blocking 으로 넣는다.
    from kis_trader.realtime.client import _STREAM_SENTINEL

    client = RealtimeClient("KEY", "ws://x")
    client._queue = queue.Queue(maxsize=3)
    for i in range(3):                                # 큐를 상한까지 채운다
        client._queue.put_nowait(RealtimeMessage(tr_id="T", tr_key="K", data=[str(i)]))
    # 상한 있는 스레드로 호출 -- 블로킹 put 으로 퇴행하면 hang 대신 실패로 드러난다.
    worker = threading.Thread(target=client._put_sentinel)
    worker.start()
    worker.join(timeout=5.0)
    assert not worker.is_alive(), "_put_sentinel 이 블록했다(non-blocking 퇴행)"
    drained = []
    while not client._queue.empty():
        drained.append(client._queue.get_nowait())
    assert drained[-1] is _STREAM_SENTINEL             # 가득 차 있어도 sentinel 이 들어갔다


def test_dispatch_queue_drops_oldest_when_full():
    # 디스패치 큐도 stream 큐와 같은 drop-oldest -- 느린/블로킹 콜백이 메모리를 무한정 키우지 않게.
    client = RealtimeClient("KEY", "ws://x")
    client._dispatch_queue = queue.Queue(maxsize=2)
    items = [(RealtimeMessage(tr_id="T", tr_key="1", data=[str(i)]), (), ()) for i in range(3)]
    for item in items:
        client._submit_to_dispatch(item)
    drained = []
    while not client._dispatch_queue.empty():
        drained.append(client._dispatch_queue.get_nowait())
    assert drained == items[1:]                        # 가장 오래된 first 는 드롭
    assert client._dispatch_overflow_warned is True


def test_put_dispatch_sentinel_does_not_block_when_queue_full():
    # 블로킹 콜백이 디스패치 큐를 상한에 채운 채 워커가 갇혀 있으면, 종료 센티넬의 블로킹 put 은
    # 수신(루프) 스레드를 teardown 중 영원히 멎게 한다(P0 교착). _put_dispatch_sentinel 은 오래된
    # 것을 버리며 non-blocking 으로 넣는다(stream 큐의 _put_sentinel 과 쌍).
    from kis_trader.realtime.client import _DISPATCH_SENTINEL

    client = RealtimeClient("KEY", "ws://x")
    client._dispatch_queue = queue.Queue(maxsize=3)
    for i in range(3):                                 # 큐를 상한까지 채운다
        client._dispatch_queue.put_nowait(
            (RealtimeMessage(tr_id="T", tr_key="K", data=[str(i)]), (), ()))
    # 상한 있는 스레드로 호출 -- 블로킹 put 으로 퇴행하면 hang 대신 실패로 드러난다.
    worker = threading.Thread(target=client._put_dispatch_sentinel)
    worker.start()
    worker.join(timeout=5.0)
    assert not worker.is_alive(), "_put_dispatch_sentinel 이 블록했다(non-blocking 퇴행)"
    drained = []
    while not client._dispatch_queue.empty():
        drained.append(client._dispatch_queue.get_nowait())
    assert drained[-1] is _DISPATCH_SENTINEL            # 가득 차 있어도 센티넬이 들어갔다


def test_dispatch_worker_terminates_on_stop():
    # stop() 후 디스패치 워커 스레드가 센티넬에서 종료돼야 한다(유계 큐에서도 센티넬이 들어가므로
    # 수신 스레드가 teardown 에서 wedge 되지 않고 워커도 멎는다 -- 프로세스 내 재시작 가드가 안 막힌다).
    ws = FakeWebSocket(incoming=["0|DUMMYTR0|001|005930^093100^71800"])
    client = _client(ws)
    client.subscribe("DUMMYTR0", "005930")
    client.start()
    list(client.stream(timeout=2.0))
    client.stop()
    assert client._dispatch_thread is not None
    client._dispatch_thread.join(timeout=5.0)
    assert not client._dispatch_thread.is_alive()      # 워커가 센티넬에서 종료됐다


def test_restart_drains_stale_queue():
    # 재시작 시 이전 run 의 잔여 틱/센티넬이 새 stream() 에 낡은 데이터·즉시종료로 새면 안 된다.
    from kis_trader.realtime.client import _STREAM_SENTINEL

    ws = FakeWebSocket(incoming=["0|DUMMYTR0|001|005930^093100^71800"])
    client = _client(ws)
    client._queue.put(RealtimeMessage(tr_id="DUMMYTR0", tr_key="STALE", data=["old"]))
    client._queue.put(_STREAM_SENTINEL)               # 이전 run 이 남긴 종료 신호를 흉내
    client.subscribe("DUMMYTR0", "005930")
    client.start()                                    # start() 가 큐를 비워야 한다
    streamed = list(client.stream(timeout=2.0))
    client.stop()
    assert all(m.tr_key != "STALE" for m in streamed)  # 낡은 틱이 새지 않았다
    assert [m.tr_key for m in streamed] == ["005930"]  # 새 run 의 틱만, 조기종료 없이


def test_concurrent_start_connects_once():
    # 동시 start() 2개가 가드를 함께 통과해 수신 스레드를 2개 띄우고 _conn/_thread 를 clobber(join 불가
    # 누수)하던 경합 회귀 -- lifecycle 락으로 연결은 정확히 1회, 두 호출 모두 예외 없이 반환한다.
    connects: list[str] = []

    async def counting_connect(url):
        connects.append(url)
        return FakeWebSocket(incoming=[])

    client = RealtimeClient("KEY", "ws://x", connect=counting_connect, reconnect=False)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def go():
        try:
            barrier.wait()
            client.start(timeout=2.0)
        except BaseException as exc:                 # noqa: BLE001 -- 경합 시 어떤 예외든 포착해 단언
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    try:
        assert not errors                            # 두 호출 모두 정상 반환(한쪽은 _running 보고 조기반환)
        assert len(connects) == 1                    # 수신 스레드가 1개만 떠서 연결도 정확히 1회
        assert client._running is True
    finally:
        client.stop()


def test_start_refused_while_previous_thread_alive():
    # 이전 run 스레드가 아직 살아있으면(멎은 연결 등) start() 는 새 run 을 띄우지 않고 fail-closed 해야
    # 한다 -- 안 그러면 self._conn/_queue 를 갈아치워 뒤늦게 깨어난 옛 스레드가 새 소켓을 닫고 큐를 오염시킨다.
    client = _client(FakeWebSocket(incoming=[]))
    blocker = threading.Event()
    stuck = threading.Thread(target=blocker.wait, daemon=True)
    stuck.start()
    client._thread = stuck                       # 이전 run 이 아직 종료 안 된 상황을 흉내
    try:
        with pytest.raises(RealtimeError):
            client.start(timeout=1.0)
        assert client._running is False          # 새 run 을 띄우지 않았다
    finally:
        blocker.set()
        stuck.join(timeout=1.0)


def test_closing_typed_keeps_wire_while_raw_callback_shares_contract():
    # raw subscribe 와 타입드 구독이 같은 (tr_id, tr_key) wire 계약을 공유할 때, 타입드를 닫아도
    # raw 가 남아 있으면 wire 해제를 보내지 않고 _desired 도 유지한다(공유 refcount).
    ws = OpenWebSocket()
    client = _open_client(ws)
    client.start()
    client.subscribe("H0IFCNT0", "101W09", on=lambda _m: None)   # raw
    sub = client._open_typed("H0IFCNT0", "101W09")               # typed, 같은 계약
    assert _wait_until(ws, lambda: len(_frames_of(ws, "1", "H0IFCNT0")) == 1)
    sub.close()                                                  # 타입드만 닫음 -- raw 는 남음
    assert ("H0IFCNT0", "101W09") in client._desired            # raw 가 여전히 원함
    assert client._callbacks.get(("H0IFCNT0", "101W09"))        # raw 콜백 유지
    assert ("H0IFCNT0", "101W09") not in client._subscriptions  # 타입드는 사라짐
    client.stop()
    assert len(_frames_of(ws, "2", "H0IFCNT0")) == 0            # wire 해제는 안 나갔다


def test_raw_unsubscribe_keeps_wire_while_typed_subscription_shares_contract():
    # 반대 방향: raw unsubscribe 해도 타입드 구독이 같은 계약에 남아 있으면 wire 해제를 안 보낸다.
    ws = OpenWebSocket()
    client = _open_client(ws)
    client.start()
    client.subscribe("H0IFCNT0", "101W09", on=lambda _m: None)
    client._open_typed("H0IFCNT0", "101W09")
    assert _wait_until(ws, lambda: len(_frames_of(ws, "1", "H0IFCNT0")) == 1)
    client.unsubscribe("H0IFCNT0", "101W09")                    # raw 만 해제 -- 타입드 남음
    assert ("H0IFCNT0", "101W09") in client._desired
    assert client._subscriptions.get(("H0IFCNT0", "101W09"))    # 타입드 구독 유지
    assert not client._callbacks.get(("H0IFCNT0", "101W09"))    # raw 콜백은 제거
    client.stop()
    assert len(_frames_of(ws, "2", "H0IFCNT0")) == 0            # wire 해제는 안 나갔다


def test_closing_typed_keeps_wire_while_callback_free_raw_shares_contract():
    # F33 회귀: subscribe(on=None) 로 연 콜백 없는 raw 구독(stream() 전용)도 공유 refcount 에
    # 포함돼야 한다 -- raw 소유권을 _callbacks 유무로 판정하면 이 구독을 못 봐, 타입드 close 가
    # 살아 있는 wire 를 끊었다. _raw_subscriptions 로 판정해야 끊기지 않는다.
    ws = OpenWebSocket()
    client = _open_client(ws)
    client.start()
    client.subscribe("H0IFCNT0", "101W09")                       # raw, 콜백 없음(on=None)
    sub = client._open_typed("H0IFCNT0", "101W09")               # typed, 같은 계약
    assert _wait_until(ws, lambda: len(_frames_of(ws, "1", "H0IFCNT0")) == 1)
    sub.close()                                                  # 타입드만 닫음 -- raw 는 남음
    assert ("H0IFCNT0", "101W09") in client._desired            # raw 가 여전히 원함(끊기면 안 됨)
    assert ("H0IFCNT0", "101W09") in client._raw_subscriptions  # 콜백 없어도 raw 소유권 추적
    assert ("H0IFCNT0", "101W09") not in client._subscriptions  # 타입드는 사라짐
    client.stop()
    assert len(_frames_of(ws, "2", "H0IFCNT0")) == 0            # wire 해제는 안 나갔다


def test_callback_free_raw_unsubscribe_releases_wire_when_no_typed_remains():
    # F33 의 반대편: 콜백 없는 raw 구독만 있을 때 unsubscribe 하면 raw 소유권이 사라지므로
    # _desired 에서 빠지고(다음 start/재연결이 ghost 재구독하지 않는다) wire 해제가 나간다.
    ws = OpenWebSocket()
    client = _open_client(ws)
    client.start()
    client.subscribe("H0IFCNT0", "101W09")                       # raw, 콜백 없음
    assert _wait_until(ws, lambda: len(_frames_of(ws, "1", "H0IFCNT0")) == 1)
    client.unsubscribe("H0IFCNT0", "101W09")
    assert ("H0IFCNT0", "101W09") not in client._raw_subscriptions
    assert ("H0IFCNT0", "101W09") not in client._desired
    assert _wait_until(ws, lambda: len(_frames_of(ws, "2", "H0IFCNT0")) == 1)  # wire 해제 나감


class _DeadLoop:
    """is_running() 은 True 라고 보고하지만 예약 직전에 멎은 루프 -- call_soon_threadsafe 가
    RuntimeError 를 던진다. is_running() 확인과 예약 사이의 teardown 레이스를 결정적으로 재현한다."""

    def is_running(self) -> bool:
        return True

    def call_soon_threadsafe(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("Event loop is closed")


@pytest.mark.filterwarnings("error::RuntimeWarning")
def test_wire_schedulers_close_coro_when_loop_dies_mid_schedule():
    # 백로그 하드닝: is_running() 이 True 인 직후 루프가 멎으면 예약 호출이 RuntimeError 를 내고
    # coro 가 영영 await 되지 않는다("coroutine was never awaited" GC 경고 + 조용히 사라진 구독).
    # _submit_wire/_schedule_wire/_call_async 는 그 RuntimeError 를 잡아 coro 를 닫아야 한다.
    # filterwarnings(error) 로 coro 가 안 닫히면(=미await) 이 테스트가 실패하도록 못박는다.
    client = _client(FakeWebSocket([]))
    client._loop = _DeadLoop()  # type: ignore[assignment]

    async def _wire() -> str:
        return "x"  # pragma: no cover - 실행되면 안 된다(예약 자체가 실패)

    # (1) run_coroutine_threadsafe 분기(다른 스레드): _thread 를 현재와 다르게 둔다.
    client._thread = threading.Thread(target=lambda: None)
    coro = _wire()
    assert client._submit_wire(coro) is None
    assert coro.cr_frame is None                     # 닫혔다(await 되지 않음)

    # (2) call_soon_threadsafe 분기(수신 루프 스레드): _thread 를 현재 스레드로 둔다.
    client._thread = threading.current_thread()
    coro2 = _wire()
    assert client._submit_wire(coro2) is None
    assert coro2.cr_frame is None

    # _schedule_wire(완료 비대기)와 _call_async(완료 대기) 도 같은 레이스를 닫는다.
    coro3 = _wire()
    client._schedule_wire(coro3)
    assert coro3.cr_frame is None
    coro4 = _wire()  # _call_async, 수신 루프 스레드 분기(call_soon_threadsafe)
    client._call_async(coro4)
    assert coro4.cr_frame is None

    # _call_async 의 다른-스레드 분기(run_coroutine_threadsafe)도 같은 레이스를 닫아야 한다 --
    # 이 경로는 call_soon_threadsafe 분기와 별개라 따로 못박는다.
    client._thread = threading.Thread(target=lambda: None)
    coro5 = _wire()
    client._call_async(coro5)
    assert coro5.cr_frame is None
