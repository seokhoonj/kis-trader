"""실시간 동기 래퍼(client.RealtimeClient) 테스트 -- 가짜 WebSocket + 백그라운드 스레드.

콜백 전달, stream() 이터레이션, start/stop 수명주기를 실서버 없이 검증한다.
"""

from __future__ import annotations

import json
import threading

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
            "0|H0STCNT0|001|005930^093000^71500",
            "0|H0STCNT0|001|005930^093001^71600",
        ]
    )
    received = []
    client = _client(ws)
    client.subscribe("H0STCNT0", "005930", on=received.append)
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
    assert sub["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "005930"}


def test_context_manager_starts_and_stops():
    ws = FakeWebSocket(incoming=["0|H0STCNT0|001|005930^1"])
    got = []
    client = _client(ws)
    client.subscribe("H0STCNT0", "005930", on=got.append)
    with client:
        list(client.stream(timeout=2.0))
    assert len(got) == 1
    assert not client._running  # stop 됨


def test_subscribe_before_start_is_deferred_then_sent():
    ws = FakeWebSocket(incoming=[])
    client = _client(ws)
    client.subscribe("H0STASP0", "005930")  # start 전
    assert ws.sent == []  # 아직 미전송
    client.start()
    list(client.stream(timeout=1.0))
    client.stop()
    assert any("H0STASP0" in s for s in ws.sent)  # start 시 전송됨
