from __future__ import annotations

from kis_trader.realtime._connection import RealtimeMessage
from kis_trader.realtime.client import RealtimeClient


def _client() -> RealtimeClient:
    # 연결 없이 dispatch/라우팅 단위테스트 (start() 안 함)
    return RealtimeClient("approval", "wss://example/ws", connect=None)


def test_open_typed_routes_only_matching_contract() -> None:
    c = _client()
    fut = c._open_typed("H0IFCNT0", "101W09")
    other = c._open_typed("H0IFCNT0", "201W09")
    c._dispatch(RealtimeMessage("H0IFCNT0", "101W09", "A"))
    c._dispatch(RealtimeMessage("H0IFCNT0", "201W09", "B"))
    fut.close()
    other.close()
    assert list(fut) == ["A"]
    assert list(other) == ["B"]


def test_dispatch_still_feeds_central_stream() -> None:
    c = _client()
    c._dispatch(RealtimeMessage("H0IFCNT0", "101W09", "A"))
    # 중앙 큐가 계속 전량 수신 (raw stream 경로 보존)
    msg = c._queue.get_nowait()
    assert isinstance(msg, RealtimeMessage) and msg.data == "A"


def test_flat_methods_removed() -> None:
    c = _client()
    assert not hasattr(c, "trades")
    assert not hasattr(c, "order_book")
    assert not hasattr(c, "execution_notices")


def test_close_deregisters_routing() -> None:
    c = _client()
    sub = c._open_typed("H0STASP0", "005930")
    sub.close()
    c._dispatch(RealtimeMessage("H0STASP0", "005930", "late"))
    # 닫힌 뒤 도착한 메시지는 sub 큐에 안 들어감 (sentinel 뒤로 반복 즉시 종료)
    assert list(sub) == []


def test_domestic_property_returns_namespace() -> None:
    from kis_trader.realtime.namespace import RealtimeDomesticNamespace

    c = _client()
    assert isinstance(c.domestic, RealtimeDomesticNamespace)
    # end-to-end: 잎이 구독을 등록
    sub = c.domestic.futures("101W09").trades()
    assert (sub.tr_id, sub.tr_key) == ("H0IFCNT0", "101W09")
    assert ("H0IFCNT0", "101W09") in c._subscriptions
