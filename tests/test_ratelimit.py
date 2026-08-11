"""SlidingWindowRateLimiter -- 결정적(fake clock/sleep) + 스레드 안전 검증."""

from __future__ import annotations

import threading
import time

import pytest

from kis_openapi._ratelimit import (
    DEFAULT_REQUESTS_PER_SECOND,
    SlidingWindowRateLimiter,
    build_rate_limiter,
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


def _limiter(clock: FakeClock, max_requests: int, per_seconds: float = 1.0, **kw):
    return SlidingWindowRateLimiter(
        max_requests, per_seconds=per_seconds, clock=clock.now, sleep=clock.sleep, **kw
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
    rl = _limiter(clock, 1, per_seconds=10.0, max_wait=1.0)
    rl.acquire()                        # t=0
    with pytest.raises(KISError, match="유량|대기|rate"):
        rl.acquire()                    # 필요 대기 10s > max_wait 1s


def test_zero_or_negative_max_requests_rejected():
    clock = FakeClock()
    with pytest.raises(ValueError):
        _limiter(clock, 0)


def test_build_rate_limiter_integer_rate():
    clock = FakeClock()
    rl = build_rate_limiter(2, clock=clock.now, sleep=clock.sleep)   # 초당 2건
    rl.acquire()
    rl.acquire()
    rl.acquire()                        # 3번째는 1초 대기
    assert clock.slept == [1.0]


def test_build_rate_limiter_fractional_rate():
    clock = FakeClock()
    rl = build_rate_limiter(0.5, clock=clock.now, sleep=clock.sleep)  # 2초당 1건
    rl.acquire()
    rl.acquire()                        # 2초 대기
    assert clock.slept == [2.0]


def test_build_rate_limiter_rejects_nonpositive():
    with pytest.raises(ValueError):
        build_rate_limiter(0)


def test_default_rates_real_and_demo():
    # 공식 실전 18/모의 1 아래 마진.
    assert DEFAULT_REQUESTS_PER_SECOND["real"] == pytest.approx(15.0)
    assert DEFAULT_REQUESTS_PER_SECOND["demo"] == pytest.approx(1.0)


def test_thread_safe_never_exceeds_limit_in_window():
    # 실시각·고빈도(실제 sleep 최소)로 동시 acquire 가 상태를 깨지 않고 한도를 지키는지.
    rl = SlidingWindowRateLimiter(5, per_seconds=0.2, clock=time.monotonic, sleep=time.sleep)
    stamps: list[float] = []
    lock = threading.Lock()

    def worker():
        rl.acquire()
        with lock:
            stamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(stamps) == 20                                # 전원 완료(교착·유실 없음)
    stamps.sort()
    # 임의 0.2초 창 안에 5건 초과가 없어야 한다.
    for i in range(len(stamps)):
        in_window = [s for s in stamps if stamps[i] <= s < stamps[i] + 0.2]
        assert len(in_window) <= 5
