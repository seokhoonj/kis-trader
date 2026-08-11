"""클라이언트측 호출 유량 조절 -- sliding window rate limiter.

KIS 유량제한(앱키 단위 합산, 실전 초당 18건 / 모의 1건)에 걸리기 전에 클라이언트가 선제적으로
호출 속도를 조절한다. 최근 ``per_seconds`` 창 안의 요청 수가 ``max_requests`` 미만이 되도록,
필요하면 가장 오래된 요청이 창 밖으로 밀려날 때까지 블록(sleep)한다. KIS 서버가 sliding window
로 세는 것으로 추정되어(커뮤니티 관찰) token bucket 보다 유량초과(EGW00201)를 덜 유발한다.

``clock``/``sleep`` 은 주입 가능해 fake clock 으로 벽시계 없이 결정적으로 테스트한다.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from .errors import KISError

#: 환경별 기본 초당 호출 한도. 공식 유량(실전 REST 18건/초, 모의 1건/초; 앱키 단위 합산)
#: 아래로 마진을 둔 값이다 -- KIS 서버가 sliding window 로 세는 것으로 추정돼 경계값(18)에
#: 붙이면 간헐 초과가 나므로 실전은 15 로 낮춘다. 모의는 원래 1 이라 그대로.
DEFAULT_REQUESTS_PER_SECOND: dict[str, float] = {"real": 15.0, "demo": 1.0}


def build_rate_limiter(
    requests_per_second: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> SlidingWindowRateLimiter:
    """초당 허용 호출 수(``requests_per_second``)로 :class:`SlidingWindowRateLimiter` 를 만든다.

    1 이상이면 "1초 창에 N건"(``max_requests=int(N)``), 1 미만(예: 0.5/초)이면 "1건을 1/N 초
    창에"로 환산한다(예: 0.5 -> 2초당 1건). 0 이하는 :class:`ValueError`.
    """
    rate = float(requests_per_second)
    if rate <= 0:
        raise ValueError(f"requests_per_second 는 양수여야 한다: {requests_per_second}")
    if rate >= 1:
        return SlidingWindowRateLimiter(int(rate), per_seconds=1.0, clock=clock, sleep=sleep)
    return SlidingWindowRateLimiter(1, per_seconds=1.0 / rate, clock=clock, sleep=sleep)


class SlidingWindowRateLimiter:
    """스레드 안전 sliding-window 유량 제한기.

    상태(최근 요청 시각 deque)와 그 위 연산(:meth:`acquire`)이 불변식(창 안 요청 <= ``max_requests``)
    을 함께 지키므로 클래스다. 한도 미만이면 즉시 통과하며 현재 시각을 기록하고, 가득 차면 가장
    오래된 요청이 창 밖으로 만료될 때까지 ``sleep`` 후 재확인한다. 단일 순차 호출(창당 한도 미만)에는
    지연이 없다.

    - ``max_requests`` / ``per_seconds``: "``per_seconds`` 초 창 안 최대 ``max_requests`` 건".
    - ``clock``: 단조(monotonic) 초 단위 시각원. ``sleep``: 대기 함수. 둘 다 주입 가능(테스트용).
    - ``max_wait``: 한 번의 ``acquire`` 가 기다릴 수 있는 상한. 정상 동작에선 대기가 창(``per_seconds``)
      을 넘지 않으므로, 이를 초과하면 클럭 이상/과도한 설정으로 보고 :class:`KISError` 를 던진다.

    락을 쥔 채 대기하므로 여러 스레드의 ``acquire`` 는 직렬화되어 총 처리율이 한도를 지킨다.
    (네트워크 호출은 ``acquire`` 반환 뒤에 일어나므로 락 밖이다.)
    """

    def __init__(
        self,
        max_requests: int,
        *,
        per_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_wait: float = 60.0,
    ) -> None:
        if max_requests <= 0:
            raise ValueError(f"max_requests 는 양수여야 한다: {max_requests}")
        if per_seconds <= 0:
            raise ValueError(f"per_seconds 는 양수여야 한다: {per_seconds}")
        self._max = max_requests
        self._window = per_seconds
        self._clock = clock
        self._sleep = sleep
        self._max_wait = max_wait
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """창 안 요청 수가 한도 미만이 되도록 필요 시 블록한 뒤, 이 요청의 시각을 기록한다."""
        with self._lock:
            while True:
                now = self._clock()
                horizon = now - self._window
                while self._times and self._times[0] <= horizon:
                    self._times.popleft()
                if len(self._times) < self._max:
                    self._times.append(now)
                    return
                wait = self._times[0] + self._window - now
                if wait > self._max_wait:
                    raise KISError(
                        f"유량 제한 대기({wait:.3f}s)가 상한 max_wait({self._max_wait:.3f}s)을 "
                        f"넘었다 -- 클럭 이상이거나 유량 설정이 과도하다."
                    )
                if wait > 0:
                    self._sleep(wait)
