from __future__ import annotations

import threading

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


def test_stop_broadcasts_sentinel_to_open_subscriptions() -> None:
    # stop()/teardown 이 중앙 큐만 sentinel 하면 for tick in sub 가 영구 hang -- 열린 sub 에도
    # 종료 sentinel 을 넣고 라우팅을 비워야 한다(반복 종료 + 등록/라우팅 누수 방지).
    c = _client()
    s1 = c._open_typed("H0IFCNT0", "101W09")
    s2 = c._open_typed("H0STASP0", "005930")
    s1._feed(RealtimeMessage("H0IFCNT0", "101W09", "a"))
    c._shutdown_subscriptions()
    assert c._subscriptions == {}  # 라우팅 맵 비워짐(누수 없음)
    assert list(s1) == ["a"]  # 버퍼된 엔티티 뒤 sentinel -> 종료
    assert list(s2) == []  # sentinel -> 즉시 종료(hang 아님)


def test_concurrent_open_close_same_key_keeps_routing_consistent() -> None:
    # 같은 (tr_id, tr_key) 에 여러 스레드가 동시에 open/close 해도 refcount 라우팅이 일관돼야
    # 한다(모두 닫히면 키 제거, 누수 없음). refcount 판단이 락 하 원자적임을 확인.
    c = _client()
    key = ("H0IFCNT0", "101W09")
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        sub = c._open_typed(*key)
        sub.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert key not in c._subscriptions  # 전부 close -> 키 제거(라우팅 누수 없음)


def test_domestic_property_returns_namespace() -> None:
    from kis_trader.realtime.domestic_namespace import RealtimeDomesticNamespace

    c = _client()
    assert isinstance(c.domestic, RealtimeDomesticNamespace)
    # end-to-end: 잎이 구독을 등록
    sub = c.domestic.futures("101W09").trades()
    assert (sub.tr_id, sub.tr_key) == ("H0IFCNT0", "101W09")
    assert ("H0IFCNT0", "101W09") in c._subscriptions
