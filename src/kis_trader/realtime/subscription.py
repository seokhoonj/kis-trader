"""타입드 실시간 구독 -- :class:`RealtimeSubscription`.

``kis.realtime().domestic.futures(code).trades()`` 같은 잎(leaf)이 반환하는 객체. 특정
``(tr_id, tr_key)`` 계약의 파싱된 엔티티만 전용 큐로 받아, ``for tick in sub`` 로 타입-정확하게
소비한다(중앙 ``stream()`` 의 tr_id/isinstance demux 불필요). 큐가 상한에 차면 가장 오래된 것을
버린다(시장데이터는 최신이 중요) -- 중앙 큐 정책과 동일.
"""

from __future__ import annotations

import logging
import queue
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, Self, TypeVar, cast

if TYPE_CHECKING:
    from ._connection import RealtimeMessage

T = TypeVar("T")

_logger = logging.getLogger("kis_trader.realtime")
_SENTINEL = object()  # 반복 종료 신호


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
        # 큐 자체는 상한 없이 두고 데이터 항목만 수동으로 상한(``maxsize``)에 맞춰 드롭한다.
        # 종료 sentinel 은 제어 신호라 상한과 무관하게 항상 들어가야 하므로(꽉 찬 큐에 blocking
        # put 하면 소비자 없는 close 가 영원히 막힌다) 큐를 unbounded 로 둔다.
        self._maxsize = maxsize
        self._queue: queue.Queue[object] = queue.Queue()
        self._closed = False
        self._overflow_warned = False

    def _feed(self, message: RealtimeMessage) -> None:
        """dispatch 가 매칭 메시지를 전달. 콜백 실행 후 엔티티를 drop-oldest 로 적재.

        ``close()`` 뒤에 도착하는 late 메시지는 (대부분) 무시한다. dispatch 는 라우팅 스냅샷을 락
        밖에서 ``_feed`` 하므로 close 와 경합할 수 있는데, 닫힌 뒤 적재하면 큐가 상한일 때 drop-oldest
        가 종료 sentinel 을 밀어내 ``__next__`` 가 영구 블록될 수 있다 -- ``_closed`` 가드가 이를 막는다.
        단 ``_closed`` 확인과 콜백 호출 사이에 close 가 끼어들면 **이미 디스패치 중이던 콜백이 최대 1회**
        발화할 수 있다(단일 생산자라 그 이상은 없다).
        """
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
        if self._maxsize and self._queue.qsize() >= self._maxsize:
            try:
                self._queue.get_nowait()  # 가장 오래된 것 드롭
            except queue.Empty:
                pass
            if not self._overflow_warned:
                self._overflow_warned = True
                _logger.warning(
                    "realtime subscription 큐가 상한에 도달 -- 소비가 느려 오래된 항목을 드롭합니다 (%s/%s)",
                    self.tr_id,
                    self.tr_key,
                )
        self._queue.put(entity)

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> T:
        item = self._queue.get()
        if item is _SENTINEL:
            raise StopIteration
        return cast("T", item)  # 큐엔 엔티티(T) 또는 종료 sentinel 만 들어오고, sentinel 은 위에서 걸러짐

    def close(self) -> None:
        """구독 해제(wire unsubscribe) + 반복 종료. 멱등."""
        if self._closed:
            return
        self._closed = True
        self._unsubscribe(self)
        self._queue.put(_SENTINEL)

    def _shutdown(self) -> None:
        """클라이언트 종료 시 호출 -- 반복만 종료하고 wire unsubscribe 는 하지 않는다.

        ``stop()`` 이 소켓을 이미 닫으므로 개별 해제는 불필요·불가하다. 미종료 구독의 반복
        (``for tick in sub``)이 영구 블록되지 않게 종료 sentinel 만 넣는다. 멱등."""
        if self._closed:
            return
        self._closed = True
        self._queue.put(_SENTINEL)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
