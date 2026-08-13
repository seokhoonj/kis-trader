"""클라이언트측 호출 유량 조절 -- sliding window rate limiter.

KIS 유량제한(앱키 단위 합산, 실전 초당 18건 / 모의 1건)에 걸리기 전에 클라이언트가 선제적으로
호출 속도를 조절한다. 최근 ``window_seconds`` 창 안의 요청 수가 ``max_requests`` 미만이 되도록,
필요하면 가장 오래된 요청이 창 밖으로 밀려날 때까지 블록(sleep)한다. KIS 서버가 sliding window
로 세는 것으로 추정되어(커뮤니티 관찰) token bucket 보다 유량초과(EGW00201)를 덜 유발한다.

``clock``/``sleep`` 은 주입 가능해 fake clock 으로 벽시계 없이 결정적으로 테스트한다.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Self

from .errors import KISError

#: 환경별 기본 초당 호출 한도. 공식 유량(실전 REST 18건/초, 모의 1건/초; 앱키 단위 합산)
#: 아래로 마진을 둔 값이다 -- KIS 서버가 sliding window 로 세는 것으로 추정돼 경계값(18)에
#: 붙이면 간헐 초과가 나므로 실전은 15 로 낮춘다. 모의는 원래 1 이라 그대로.
DEFAULT_REQUESTS_PER_SECOND_BY_ENVIRONMENT: dict[str, float] = {"real": 15.0, "paper": 1.0}


class SlidingWindowRateLimiter:
    """스레드 안전 sliding-window 유량 제한기.

    상태(최근 요청 시각 deque)와 그 위 연산(:meth:`acquire`)이 불변식(창 안 요청 <= ``max_requests``)
    을 함께 지키므로 클래스다. 한도 미만이면 즉시 통과하며 현재 시각을 기록하고, 가득 차면 가장
    오래된 요청이 창 밖으로 만료될 때까지 ``sleep`` 후 재확인한다. 단일 순차 호출(창당 한도 미만)에는
    지연이 없다.

    - ``max_requests`` / ``window_seconds``: "``window_seconds`` 초 창 안 최대 ``max_requests`` 건".
    - ``clock``: 단조(monotonic) 초 단위 시각원. ``sleep``: 대기 함수. 둘 다 주입 가능(테스트용).
    - ``max_wait``: **한 번의 sleep**(한 슬롯이 만료되길 기다리는 시간)의 상한. 정상 동작에선 그 대기가
      창(``window_seconds``)을 넘지 않으므로, 이를 초과하면 클럭 이상/과도한 설정으로 보고
      :class:`KISError` 를 던진다. (경합 시 ``acquire`` 는 여러 슬롯을 기다리며 총 대기가 ``max_wait``
      를 넘을 수 있다 -- 이는 누적 상한이 아니라 슬롯당 이상치 탐지용이다.)

    락을 쥔 채 대기하므로 여러 스레드의 ``acquire`` 는 직렬화되어 총 처리율이 한도를 지킨다.
    (네트워크 호출은 ``acquire`` 반환 뒤에 일어나므로 락 밖이다.)
    """

    def __init__(
        self,
        max_requests: int,
        *,
        window_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_wait: float = 60.0,
    ) -> None:
        # 잘못된 설정을 여기서 즉시 거부한다 -- 비유한/비양수 창이나 max_wait 는 acquire 에서
        # 무한 대기나 상한 검사 무력화로 잠복하다 주문 경로를 고착시킨다.
        if max_requests <= 0:
            raise ValueError(f"max_requests 는 양수여야 한다: {max_requests}")
        if not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError(f"window_seconds 는 유한한 양수여야 한다: {window_seconds}")
        if not math.isfinite(max_wait) or max_wait <= 0:
            raise ValueError(f"max_wait 는 유한한 양수여야 한다: {max_wait}")
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._clock = clock
        self._sleep = sleep
        self._max_wait = max_wait
        self._request_times: deque[float] = deque()
        self._lock = threading.Lock()

    @classmethod
    def from_rate(
        cls,
        requests_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Self:
        """초당 허용 호출 수로 리미터를 만든다.

        1 이상이면 "1초 창에 N건"(``max_requests=int(N)``), 1 미만(예: 0.5/초)이면 "1건을 1/N 초
        창에"로 환산한다(예: 0.5 -> 2초당 1건). 0 이하는 :class:`ValueError`. ``max_wait`` 는 창에
        맞춰 키워, 낮은 rate(창 > 60s)여도 정상 대기가 상한을 넘어 spurious raise 되지 않게 한다
        (주문 경로에선 그 raise 가 in-flight 를 고착시키므로).
        """
        rate = float(requests_per_second)
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError(f"requests_per_second 는 유한한 양수여야 한다: {requests_per_second}")
        window_seconds = 1.0 if rate >= 1 else 1.0 / rate
        max_requests = int(rate) if rate >= 1 else 1
        return cls(
            max_requests, window_seconds=window_seconds, clock=clock, sleep=sleep,
            max_wait=max(60.0, window_seconds * 2.0),
        )

    def acquire(self) -> None:
        """창 안 요청 수가 한도 미만이 되도록 필요 시 블록한 뒤, 이 요청의 시각을 기록한다."""
        with self._lock:
            while True:
                now = self._clock()
                cutoff = now - self._window_seconds
                while self._request_times and self._request_times[0] <= cutoff:
                    self._request_times.popleft()
                if len(self._request_times) < self._max_requests:
                    self._request_times.append(now)
                    return
                wait = self._request_times[0] + self._window_seconds - now
                if wait > self._max_wait:
                    raise KISError(
                        f"유량 제한 대기({wait:.3f}s)가 상한 max_wait({self._max_wait:.3f}s)을 "
                        f"넘었다 -- 클럭 이상이거나 유량 설정이 과도하다."
                    )
                if wait > 0:
                    self._sleep(wait)
