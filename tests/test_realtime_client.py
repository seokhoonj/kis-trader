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

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._closed.wait()
        raise StopAsyncIteration

    async def send(self, message):
        self.sent.append(message)

    async def pong(self, data):
        pass

    async def close(self):
        self._closed.set()


def _open_client(ws):
    return RealtimeClient("KEY", "ws://x", connect=_connector(ws), reconnect=False)


def _wait_until(pred, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


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
    assert _wait_until(lambda: len(_frames_of(ws, "1", "H0IFCNT0")) == 1)
    assert len(_frames_of(ws, "1", "H0IFCNT0")) == 1  # 첫 구독만 등록(중복 없음)

    s1.close()  # 마지막 아님 -> wire 해제 예약 안 함(코드상 last 에서만)
    s2.close()  # 마지막 -> 한 번 해제
    assert _wait_until(lambda: len(_frames_of(ws, "2", "H0IFCNT0")) == 1)
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
