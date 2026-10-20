"""타입드 실시간 구독 -- :class:`RealtimeSubscription`.

``kis.realtime().domestic.futures(symbol).trades()`` 같은 잎(leaf)이 반환하는 객체. 특정
``(tr_id, tr_key)`` 계약의 파싱된 엔티티만 전용 큐로 받아, ``for tick in sub`` 로 타입-정확하게
소비한다(중앙 ``stream()`` 의 tr_id/isinstance demux 불필요). 큐가 상한에 차면 가장 오래된 것을
버린다(시장데이터는 최신이 중요) -- 중앙 큐 정책과 동일.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, Self, TypeVar, cast

if TYPE_CHECKING:
    from ._connection import RealtimeMessage

T = TypeVar("T")

_logger = logging.getLogger("kis_trader.realtime")


class RealtimeSubscription(Generic[T]):
    """단일 ``(tr_id, tr_key)`` 계약의 타입드 실시간 구독.

    잎 팩토리(:mod:`kis_trader.realtime.domestic_namespace`)가 만들어 반환한다. 반복하면 이 계약의
    엔티티(``T``)만 나오고, ``close()`` 또는 컨텍스트 매니저 이탈 시 해제된다.
    """

    def __init__(
        self,
        tr_id: str,
        tr_key: str,
        *,
        on: Callable[[T], None] | None,
        unsubscribe: Callable[[RealtimeSubscription[Any]], None],
        maxsize: int = 10_000,
    ) -> None:
        self.tr_id = tr_id
        self.tr_key = tr_key
        self._on = on
        self._unsubscribe = unsubscribe
        self._maxsize = maxsize
        # 종료는 데이터와 별도 상태로 보관해 overflow 가 종료 신호를 지울 수 없게 한다.
        self._queue: deque[object] = deque()
        self._condition = threading.Condition()
        self._closed = False
        self._overflow_warned = False

    def _feed(self, message: RealtimeMessage) -> None:
        """매칭 메시지의 콜백을 실행한 뒤 열린 구독에만 엔티티를 적재한다.

        콜백은 잠금 밖에서 실행한다. 종료 전에 실행이 허용된 콜백은 종료와 경합해 끝날 수
        있지만, 적재 직전에 닫힘을 다시 확인하므로 종료 뒤 새 데이터가 들어가지는 않는다.
        """
        with self._condition:
            if self._closed:
                return
        entity = message.data
        if self._on is not None:
            try:
                self._on(entity)  # type: ignore[arg-type]  # data는 tr_id가 정하는 T
            except Exception:
                _logger.warning(
                    "realtime subscription callback for %s raised", self.tr_id, exc_info=True
                )
        self._enqueue(entity)

    def _enqueue(self, entity: object) -> None:
        warn_overflow = False
        with self._condition:
            if self._closed:
                return
            if self._maxsize and len(self._queue) >= self._maxsize:
                if self._queue:
                    self._queue.popleft()  # 가장 오래된 데이터만 드롭
                if not self._overflow_warned:
                    self._overflow_warned = True
                    warn_overflow = True
            self._queue.append(entity)
            self._condition.notify()
        if warn_overflow:
            # 로깅 핸들러도 사용자 코드를 실행할 수 있으므로 잠금 밖에서 호출한다.
            _logger.warning(
                "realtime subscription 큐가 상한에 도달 -- 소비가 느려 오래된 항목을 드롭합니다 (%s/%s)",
                self.tr_id,
                self.tr_key,
            )

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> T:
        with self._condition:
            while not self._queue and not self._closed:
                self._condition.wait()
            if self._queue:
                return cast("T", self._queue.popleft())
            raise StopIteration

    def close(self) -> None:
        """반복을 영구 종료하고 구독 해제(wire unsubscribe). 멱등이며 남은 데이터는 소비 가능."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        # 해제가 실패하거나 재진입해도 반복자는 이미 종료 상태다. 외부 코드는 잠금 밖에서 실행.
        self._unsubscribe(self)

    def _shutdown(self) -> None:
        """클라이언트 종료 시 반복만 영구 종료한다. wire unsubscribe 없이 남은 데이터는 소비 가능."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
