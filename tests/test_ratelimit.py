"""SlidingWindowRateLimiter -- 결정적(fake clock/sleep) + 스레드 안전 검증."""

from __future__ import annotations

import threading
import time

import pytest

from kis_openapi._ratelimit import (
    DEFAULT_REQUESTS_PER_SECOND_BY_ENVIRONMENT,
    SlidingWindowRateLimiter,
)
from kis_openapi.errors import KISError


class FakeClock:
    """제어 가능한 monotonic clock -- sleep 은 시간을 앞으로 감고 대기량을 기록한다."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        assert dt >= 0, f"음수 대기: {dt}"
        self.t += dt
        self.slept.append(dt)


def _limiter(clock: FakeClock, max_requests: int, window_seconds: float = 1.0, **kw):
    return SlidingWindowRateLimiter(
        max_requests, window_seconds=window_seconds, clock=clock.now, sleep=clock.sleep, **kw
    )


def test_within_limit_does_not_block():
    clock = FakeClock()
    rl = _limiter(clock, 3)
    for _ in range(3):
        rl.acquire()
    assert clock.slept == []            # 3건 <= 한도 -> 대기 없음


def test_over_limit_waits_for_oldest_to_age_out():
    clock = FakeClock()
    rl = _limiter(clock, 2)
    rl.acquire()                        # t=0
    rl.acquire()                        # t=0 -> 창 가득(2건)
    rl.acquire()                        # 대기: 가장 오래된 t=0 이 1.0 에 만료
    assert clock.slept == [1.0]
    assert clock.now() == pytest.approx(1.0)


def test_waits_only_until_oldest_ages_out_partial():
    clock = FakeClock()
    rl = _limiter(clock, 2)
    rl.acquire()                        # t=0
    clock.t = 0.3
    rl.acquire()                        # t=0.3 -> 가득
    rl.acquire()                        # 가장 오래된 t=0 -> 1.0 만료, 대기 0.7
    assert clock.slept == [pytest.approx(0.7)]
    assert clock.now() == pytest.approx(1.0)


def test_window_slides_allows_more_after_gap():
    clock = FakeClock()
    rl = _limiter(clock, 2)
    rl.acquire()
    rl.acquire()
    clock.t = 1.5                       # 두 요청 모두 창 밖(>1s)
    rl.acquire()                        # 대기 없이 통과
    rl.acquire()
    assert clock.slept == []            # 창이 슬라이드해 즉시 허용


def test_wait_exceeding_max_wait_raises():
    clock = FakeClock()
    rl = _limiter(clock, 1, window_seconds=10.0, max_wait=1.0)
    rl.acquire()                        # t=0
    with pytest.raises(KISError, match="유량|대기|rate"):
        rl.acquire()                    # 필요 대기 10s > max_wait 1s


def test_zero_or_negative_max_requests_rejected():
    clock = FakeClock()
    with pytest.raises(ValueError):
        _limiter(clock, 0)


def test_from_rate_integer_rate():
    clock = FakeClock()
    rl = SlidingWindowRateLimiter.from_rate(2, clock=clock.now, sleep=clock.sleep)   # 초당 2건
    rl.acquire()
    rl.acquire()
    rl.acquire()                        # 3번째는 1초 대기
    assert clock.slept == [1.0]


def test_from_rate_fractional_rate():
    clock = FakeClock()
    rl = SlidingWindowRateLimiter.from_rate(0.5, clock=clock.now, sleep=clock.sleep)  # 2초당 1건
    rl.acquire()
    rl.acquire()                        # 2초 대기
    assert clock.slept == [2.0]


def test_from_rate_rejects_nonpositive():
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter.from_rate(0)


def test_from_rate_scales_max_wait_to_window():
    """아주 낮은 rate(창 > 기본 max_wait 60s)여도 정상 대기가 max_wait 를 넘어 spurious raise 되면
    안 된다 -- 팩토리가 창(window_seconds)에 맞춰 max_wait 를 키운다. (안 그러면 낮은 rps 설정이
    주문 경로에서 KISError 로 in-flight 를 고착시킨다.)"""
    clock = FakeClock()
    rl = SlidingWindowRateLimiter.from_rate(0.01, clock=clock.now, sleep=clock.sleep)   # 100초당 1건(창 100s)
    rl.acquire()
    rl.acquire()                        # 100초 대기 -- 스케일된 max_wait 안 넘어 raise 안 함
    assert clock.slept == [100.0]


def test_default_rates_real_and_demo():
    # 공식 실전 18/모의 1 아래 마진.
    assert DEFAULT_REQUESTS_PER_SECOND_BY_ENVIRONMENT["real"] == pytest.approx(15.0)
    assert DEFAULT_REQUESTS_PER_SECOND_BY_ENVIRONMENT["demo"] == pytest.approx(1.0)


def test_thread_safe_serializes_and_enforces_rate():
    # 실시각 동시 acquire 가 교착·유실 없이 전원 완료되고, 리미터가 속도를 강제한다(지터로 더
    # 빨라질 수는 없다 -- 최소 경과시간은 하한으로 신뢰 가능). 5건/0.2초 => 20건은 최소 ~0.6초.
    rl = SlidingWindowRateLimiter(5, window_seconds=0.2, clock=time.monotonic, sleep=time.sleep)
    done: list[int] = []
    lock = threading.Lock()

    def worker():
        rl.acquire()
        with lock:
            done.append(1)

    start = time.monotonic()
    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)                  # 교착 시 무한 대기하지 않고 실패로 드러난다
    elapsed = time.monotonic() - start
    assert not any(t.is_alive() for t in threads)   # 전원 종료(살아있으면 교착 -> 실패)
    assert len(done) == 20                  # 교착·유실 없이 전원 완료(락이 상태를 지킴)
    assert elapsed >= 0.5                    # 리미터가 강제하는 하한(더 빨라질 수 없음)
