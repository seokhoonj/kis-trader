from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import TypeVar

import pytest

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


Entity = TypeVar("Entity")


def _release_waiters(sub: RealtimeSubscription[Entity], count: int = 3) -> None:
    sub._shutdown()
    # 실패한 종료 구현에서도 테스트 스레드를 회수한다. 데이터/종료 신호가 같은 Queue 에
    # 들어가는 구현은 닫힌 뒤에도 get 이 대기할 수 있으므로 정리 때만 종료 신호를 보충한다.
    pending = getattr(sub, "_queue", None)
    if isinstance(pending, queue.Queue):
        from kis_trader.realtime import subscription
        for _ in range(count):
            pending.put(getattr(subscription, "_SENTINEL", None))


def _bounded(action: Callable[[], None], cleanup: Callable[[], None]) -> None:
    done = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            action()
        except BaseException as error:  # noqa: BLE001 -- 스레드의 pytest 실패도 부모 테스트로 전달
            errors.append(error)
        finally:
            done.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    completed = done.wait(1)
    try:
        assert completed, "작업이 종료되지 않았다"
        if errors:
            raise errors[0]
    finally:
        cleanup()
        worker.join(1)
        assert not worker.is_alive(), "테스트 스레드가 회수되지 않았다"


def _assert_permanently_exhausted(sub: RealtimeSubscription[Entity]) -> None:
    def consume() -> None:
        for _ in range(3):
            with pytest.raises(StopIteration):
                next(sub)
    _bounded(consume, lambda: _release_waiters(sub))


def test_callback_can_close_full_subscription_permanently() -> None:
    calls: list[object] = []
    sub: RealtimeSubscription[str] = RealtimeSubscription(
        "T", "K", on=lambda value: sub.close(), unsubscribe=calls.append, maxsize=1,
    )
    sub._feed(_msg("T", "K", "tick"))
    _assert_permanently_exhausted(sub)
    assert calls == [sub]


def test_close_during_admitted_callback_prevents_late_enqueue() -> None:
    entered, release = threading.Event(), threading.Event()

    def callback(value: str) -> None:
        entered.set()
        assert release.wait(2)

    sub: RealtimeSubscription[str] = RealtimeSubscription(
        "T", "K", on=callback, unsubscribe=lambda sub: None, maxsize=1,
    )
    feeder = threading.Thread(target=lambda: sub._feed(_msg("T", "K", "late")), daemon=True)
    feeder.start()
    try:
        assert entered.wait(1)
        sub.close()
    finally:
        release.set()
        feeder.join(1)
    assert not feeder.is_alive()
    _assert_permanently_exhausted(sub)


@pytest.mark.parametrize("maxsize, expected", [(2, [2, 3]), (0, [0, 1, 2, 3])])
def test_closed_buffer_drains_before_permanent_exhaustion(maxsize: int, expected: list[int]) -> None:
    sub: RealtimeSubscription[int] = RealtimeSubscription(
        "T", "K", on=None, unsubscribe=lambda sub: None, maxsize=maxsize,
    )
    for value in range(4):
        sub._feed(_msg("T", "K", value))
    sub.close()
    assert list(sub) == expected
    _assert_permanently_exhausted(sub)


def test_close_wakes_all_waiting_consumers(monkeypatch: pytest.MonkeyPatch) -> None:
    sub: RealtimeSubscription[str] = RealtimeSubscription(
        "T", "K", on=None, unsubscribe=lambda sub: None,
    )
    waiting = threading.Event()
    wait_count = 0
    condition = getattr(sub, "_condition", None)
    if condition is None:
        condition = getattr(sub._queue, "not_empty", None)
    assert condition is not None
    original_wait = condition.wait

    def wait(timeout: float | None = None) -> bool:
        nonlocal wait_count
        wait_count += 1  # condition 잠금이 잡힌 상태에서 호출됨
        if wait_count == 2:
            waiting.set()
        return bool(original_wait(timeout))

    monkeypatch.setattr(condition, "wait", wait)
    finished = [threading.Event(), threading.Event()]
    results: list[str] = []

    def consume(index: int) -> None:
        try:
            next(sub)
        except StopIteration:
            results.append("stopped")
        finally:
            finished[index].set()

    workers = [threading.Thread(target=consume, args=(index,), daemon=True) for index in range(2)]
    for worker in workers:
        worker.start()
    try:
        assert waiting.wait(1)  # 두 소비자가 실제 wait 에 진입한 뒤 종료한다
        sub.close()
        assert all(event.wait(1) for event in finished)
        assert results == ["stopped", "stopped"]
    finally:
        _release_waiters(sub)
        for worker in workers:
            worker.join(1)
            assert not worker.is_alive()


def test_close_shutdown_race_unsubscribes_at_most_once() -> None:
    calls: list[object] = []
    sub: RealtimeSubscription[str] = RealtimeSubscription(
        "T", "K", on=None, unsubscribe=calls.append,
    )
    barrier = threading.Barrier(4)

    def finish(action: Callable[[], None]) -> None:
        barrier.wait(timeout=2)
        action()

    workers = [threading.Thread(target=finish, args=(action,), daemon=True)
               for action in (sub.close, sub.close, sub._shutdown)]
    for worker in workers:
        worker.start()
    try:
        barrier.wait(timeout=2)
    finally:
        for worker in workers:
            worker.join(1)
            assert not worker.is_alive()
    assert len(calls) <= 1
    _assert_permanently_exhausted(sub)


def test_unsubscribe_reenters_after_terminal_publication_and_error_is_preserved() -> None:
    failure = RuntimeError("unsubscribe failed")
    calls: list[object] = []
    workers: list[threading.Thread] = []
    reentered = threading.Event()

    def unsubscribe(sub: RealtimeSubscription[object]) -> None:
        calls.append(sub)

        def reenter() -> None:
            sub.close()
            with pytest.raises(StopIteration):
                next(sub)
            reentered.set()

        worker = threading.Thread(target=reenter, daemon=True)
        workers.append(worker)
        worker.start()
        assert reentered.wait(1), "unsubscribe 는 구독 잠금 밖에서 실행돼야 한다"
        raise failure

    sub: RealtimeSubscription[str] = RealtimeSubscription("T", "K", on=None, unsubscribe=unsubscribe)

    def close() -> None:
        with pytest.raises(RuntimeError) as excinfo:
            sub.close()
        assert excinfo.value is failure

    try:
        _bounded(close, lambda: _release_waiters(sub))
        sub.close()
        assert calls == [sub]
        _assert_permanently_exhausted(sub)
    finally:
        _release_waiters(sub)
        for worker in workers:
            worker.join(1)
            assert not worker.is_alive()


def test_logging_handler_can_wait_for_reentrant_close() -> None:
    sub: RealtimeSubscription[str] = RealtimeSubscription(
        "T", "K", on=None, unsubscribe=lambda sub: None, maxsize=1,
    )
    closed = threading.Event()
    workers: list[threading.Thread] = []

    class Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            def close() -> None:
                sub.close()
                closed.set()
            worker = threading.Thread(target=close, daemon=True)
            workers.append(worker)
            worker.start()
            assert closed.wait(1), "logging handler 가 구독 잠금 밖에서 실행돼야 한다"

    logger = logging.getLogger("kis_trader.realtime")
    handler = Handler()
    logger.addHandler(handler)
    try:
        sub._feed(_msg("T", "K", "a"))
        sub._feed(_msg("T", "K", "b"))
        assert list(sub) == ["b"]
        _assert_permanently_exhausted(sub)
    finally:
        logger.removeHandler(handler)
        _release_waiters(sub)
        for worker in workers:
            worker.join(1)
            assert not worker.is_alive()


def test_shutdown_permanently_terminates_without_unsubscribe() -> None:
    calls: list[object] = []
    sub: RealtimeSubscription[str] = RealtimeSubscription(
        "T", "K", on=None, unsubscribe=calls.append,
    )
    sub._shutdown()
    sub.close()
    _assert_permanently_exhausted(sub)
    assert calls == []
