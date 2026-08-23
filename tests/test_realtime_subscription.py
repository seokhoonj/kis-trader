from __future__ import annotations

from kis_trader.realtime._connection import RealtimeMessage
from kis_trader.realtime.subscription import RealtimeSubscription


def _msg(tr_id: str, tr_key: str, data: object) -> RealtimeMessage:
    return RealtimeMessage(tr_id, tr_key, data)


def test_feed_then_iterate_yields_entities_in_order() -> None:
    closed: list[RealtimeSubscription[object]] = []
    sub: RealtimeSubscription[str] = RealtimeSubscription(
        "H0IFCNT0", "101W09", on=None, unsubscribe=closed.append
    )
    sub._feed(_msg("H0IFCNT0", "101W09", "a"))
    sub._feed(_msg("H0IFCNT0", "101W09", "b"))
    sub.close()  # sentinel -> iteration terminates after drained items
    assert list(sub) == ["a", "b"]
    assert closed == [sub]  # unsubscribe called exactly once


def test_callback_receives_entity() -> None:
    seen: list[str] = []
    sub: RealtimeSubscription[str] = RealtimeSubscription(
        "H0IFCNT0", "101W09", on=seen.append, unsubscribe=lambda s: None
    )
    sub._feed(_msg("H0IFCNT0", "101W09", "x"))
    assert seen == ["x"]


def test_close_is_idempotent() -> None:
    calls: list[object] = []
    sub: RealtimeSubscription[str] = RealtimeSubscription(
        "H0IFCNT0", "101W09", on=None, unsubscribe=calls.append
    )
    sub.close()
    sub.close()
    assert calls == [sub]  # only first close unsubscribes


def test_drop_oldest_on_overflow() -> None:
    sub: RealtimeSubscription[int] = RealtimeSubscription(
        "H0IFCNT0", "101W09", on=None, unsubscribe=lambda s: None, maxsize=2
    )
    for i in range(4):
        sub._feed(_msg("H0IFCNT0", "101W09", i))
    sub.close()
    assert list(sub) == [2, 3]  # oldest (0,1) dropped


def test_feed_after_close_is_ignored() -> None:
    # dispatch 스냅샷과 close 의 경합으로 close 뒤 late _feed 가 올 수 있다 -- 무시돼야 한다
    # (콜백 미호출 + 종료 sentinel 보존).
    seen: list[str] = []
    sub: RealtimeSubscription[str] = RealtimeSubscription(
        "H0IFCNT0", "101W09", on=seen.append, unsubscribe=lambda s: None
    )
    sub.close()
    sub._feed(_msg("H0IFCNT0", "101W09", "late"))
    assert seen == []  # 닫힌 뒤 콜백 미호출
    assert list(sub) == []  # sentinel 만 남아 반복 즉시 종료


def test_context_manager_closes() -> None:
    calls: list[object] = []
    with RealtimeSubscription(
        "H0STASP0", "005930", on=None, unsubscribe=calls.append
    ) as sub:
        sub._feed(_msg("H0STASP0", "005930", "ob"))
    assert calls  # __exit__ closed
