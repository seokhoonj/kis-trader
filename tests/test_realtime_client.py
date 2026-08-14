"""실시간 동기 래퍼(client.RealtimeClient) 테스트 -- 가짜 WebSocket + 백그라운드 스레드.

콜백 전달, stream() 이터레이션, start/stop 수명주기를 실서버 없이 검증한다.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from kis_trader.errors import RealtimeError
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
