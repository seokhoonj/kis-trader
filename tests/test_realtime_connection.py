"""실시간 async 연결 코어(_connection) 테스트 -- 가짜 WebSocket.

실서버 없이 미리 준비한 프레임 시퀀스를 흘려보내 디스패치/PINGPONG echo/복호화/파서/등록상한을
검증한다. async 시나리오는 asyncio.run() 으로 감싸 pytest-asyncio 의존성을 피한다.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from kis_trader.realtime import _registry
from kis_trader.realtime._connection import RealtimeConnection
from kis_trader.realtime._registry import TRSpec


class FakeWebSocket:
    """스크립트된 수신 프레임을 async 이터레이션으로 흘리고, 송신은 기록한다."""

    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent: list[str] = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.pop(0)

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self.closed = True


def _connector(ws):
    async def connect(url):
        return ws

    return connect


def _drive(ws, subscribe=None):
    """연결을 열고(선택 구독) 소진될 때까지 메시지를 모은다(reconnect off)."""

    async def scenario():
        conn = RealtimeConnection("KEY", "ws://x", connect=_connector(ws), reconnect=False)
        async with conn:
            if subscribe:
                for tr_id, tr_key in subscribe:
                    await conn.subscribe(tr_id, tr_key)
            return [m async for m in conn]

    return asyncio.run(scenario())


def test_subscribe_sends_registration_message():
    ws = FakeWebSocket(incoming=[])
    _drive(ws, subscribe=[("H0STCNT0", "005930")])
    sent = json.loads(ws.sent[0])
    assert sent["header"]["approval_key"] == "KEY"
    assert sent["header"]["tr_type"] == "1"
    assert sent["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "005930"}


def test_unregistered_tr_yields_raw_fields():
    # 파서 미등록 TR 은 원시 필드 그대로 흘려보낸다.
    ws = FakeWebSocket(incoming=["0|DUMMYTR0|001|005930^093000^71500"])
    msgs = _drive(ws)
    assert len(msgs) == 1
    assert msgs[0].tr_id == "DUMMYTR0"
    assert msgs[0].tr_key == "005930"
    assert msgs[0].data == ["005930", "093000", "71500"]


def test_pingpong_is_echoed_and_not_yielded():
    ping = json.dumps({"header": {"tr_id": "PINGPONG"}})
    ws = FakeWebSocket(incoming=[ping])
    msgs = _drive(ws)
    assert msgs == []
    assert ws.sent == [ping]  # echo


def test_registered_parser_produces_entity():
    _registry.register(TRSpec("TESTTR0", field_count=2, parser=lambda r: {"a": r[0], "b": r[1]}))
    ws = FakeWebSocket(incoming=["0|TESTTR0|002|x^y^z^w"])
    msgs = _drive(ws)
    assert [m.data for m in msgs] == [{"a": "x", "b": "y"}, {"a": "z", "b": "w"}]


def test_encrypted_frame_decrypted_after_ack():
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key, iv = "0123456789abcdef0123456789abcdef", "abcdef9876543210"
    plaintext = "AAA^BBB^CCC"
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key.encode()), modes.CBC(iv.encode())).encryptor()
    cipher_b64 = base64.b64encode(enc.update(padded) + enc.finalize()).decode()

    ack = json.dumps(
        {"header": {"tr_id": "ENCDUMMY0"}, "body": {"rt_cd": "0", "output": {"key": key, "iv": iv}}}
    )
    ws = FakeWebSocket(incoming=[ack, f"1|ENCDUMMY0|001|{cipher_b64}"])
    msgs = _drive(ws)
    assert len(msgs) == 1
    assert msgs[0].data == ["AAA", "BBB", "CCC"]


def test_encrypted_frame_without_key_is_dropped():
    ws = FakeWebSocket(incoming=["1|ENCDUMMY0|001|garbage"])
    assert _drive(ws) == []  # 키 미수신 -> 드롭, 예외 없음


def test_malformed_registered_frame_is_dropped():
    # 등록된 TR(H0STCNT0=46필드)인데 필드가 모자라면 예외 없이 드롭(fail-safe, 스트림 유지).
    ws = FakeWebSocket(incoming=["0|H0STCNT0|001|too^few^fields"])
    assert _drive(ws) == []


def test_malformed_json_system_frame_is_dropped():
    # 파싱 불가한 시스템 프레임도 스트림을 죽이지 않는다.
    ws = FakeWebSocket(incoming=["{not valid json", "0|DUMMYTR0|001|a^b"])
    msgs = _drive(ws)
    assert [m.data for m in msgs] == [["a", "b"]]


def test_subscribe_registration_cap():
    async def scenario():
        ws = FakeWebSocket(incoming=[])
        conn = RealtimeConnection("KEY", "ws://x", connect=_connector(ws), reconnect=False)
        async with conn:
            for i in range(41):
                await conn.subscribe("H0STCNT0", f"{i:06d}")
            with pytest.raises(RuntimeError):
                await conn.subscribe("H0STCNT0", "999999")

    asyncio.run(scenario())


def test_reopen_returns_false_when_stop_disabled_reconnect():
    # stop() 이 _reconnect 를 끄면 재연결 창(backoff)에서도 재연결하지 않고 False 반환.
    async def scenario():
        conn = RealtimeConnection("KEY", "ws://x", connect=_connector(FakeWebSocket([])))
        conn._reconnect = False
        return await conn._reopen_with_backoff()

    assert asyncio.run(scenario()) is False


def test_reconnect_resubscribes_active_registrations():
    # 첫 소켓은 즉시 소진(끊김 모사), 두 번째 소켓으로 재연결 후 기존 구독 재등록되는지.
    first = FakeWebSocket(incoming=[])
    second = FakeWebSocket(incoming=["0|DUMMYTR0|001|005930^1"])
    sockets = [first, second]

    async def scenario():
        async def connect(url):
            return sockets.pop(0)

        conn = RealtimeConnection("KEY", "ws://x", connect=connect, reconnect=True)
        async with conn:
            await conn.subscribe("DUMMYTR0", "005930")
            out = []
            async for m in conn:
                out.append(m)
                break  # 두 번째 소켓에서 한 건 받으면 종료
            return out

    out = asyncio.run(scenario())
    assert len(out) == 1
    # 재연결된 두 번째 소켓에도 재등록 메시지가 나갔는지
    assert any("DUMMYTR0" in s for s in second.sent)
