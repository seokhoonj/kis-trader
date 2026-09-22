"""실시간 WebSocket async 코어 -- :class:`RealtimeConnection`.

연결/구독/수신 디스패치의 async 계층이다. 순수 프로토콜(:mod:`._protocol`)과 TR 레지스트리
(:mod:`._registry`)를 조합해:

- ``approval_key`` 로 구독/해제 메시지를 보내고(등록 상한 관리),
- 수신 프레임을 분류해 시스템 응답(구독 ACK 의 암호키 저장, PINGPONG echo)을 처리하고,
- 데이터 프레임은 (필요 시 복호화 후) 레지스트리 파서로 엔티티화해 흘려보내며,
- 연결이 끊기면 백오프 재연결 + 기존 구독 재등록한다.

``approval_key`` 획득(동기 REST ``/oauth2/Approval``)과 스레드/콜백은 상위(:mod:`.client`)의 몫.
``connect`` 를 주입할 수 있어 실서버 없이 가짜 소켓으로 테스트한다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, Self, cast

from ..errors import KISUsageError, RealtimeError
from . import _registry
from ._protocol import (
    CustomerType,
    DataFrame,
    SystemMessage,
    aes_cbc_decrypt,
    build_subscription_message,
    parse_frame,
)

_logger = logging.getLogger("kis_trader.realtime")


class WebSocketLike(Protocol):
    """연결 객체의 최소 계약(주입 가능한 시임/실 ``websockets`` 연결이 모두 만족)."""

    async def send(self, message: str) -> None: ...
    async def pong(self, data: str) -> None: ...
    async def close(self) -> None: ...
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


#: ``connect(url) -> ws`` -- ws 는 :class:`WebSocketLike` 를 만족하는 연결 객체.
Connector = Callable[[str], Awaitable[WebSocketLike]]

_MAX_REGISTRATIONS = 41  # approval_key 당 실시간 등록 상한(KIS)


@dataclass(frozen=True, slots=True)
class RealtimeMessage:
    """한 실시간 레코드. ``data`` 는 파서가 있으면 엔티티, 없으면 원시 필드(``list[str]``).

    엔티티 종류가 열려 있어 공통 베이스가 없으므로 ``data`` 는 ``object`` 다 -- 소비자는
    ``isinstance`` 로 좁혀 쓴다(정직한 계약).
    """

    tr_id: str
    tr_key: str
    data: object


async def _default_connector(url: str) -> WebSocketLike:
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover - 설치 안내
        raise RuntimeError(
            "'websockets' 를 import 할 수 없습니다(기본 의존성이어야 함): pip install kis-trader"
        ) from exc
    # 공식 KIS 샘플과 동일하게 라이브러리 기본값으로 연결(기본 open_timeout 10s 가 무한대기 방지).
    # 실 websockets 연결은 구조적으로 WebSocketLike 를 만족한다(send/pong/close/__aiter__).
    return cast(WebSocketLike, await websockets.connect(url))


class RealtimeConnection:
    """KIS 실시간 WebSocket 연결(async).

    ``async with RealtimeConnection(approval_key, url) as conn:`` 로 열고,
    ``await conn.subscribe(tr_id, tr_key)`` 로 등록한 뒤 ``async for msg in conn:`` 로 받는다.
    """

    def __init__(
        self,
        approval_key: str,
        url: str,
        *,
        connect: Connector | None = None,
        customer_type: CustomerType = "P",
        reconnect: bool = True,
        max_backoff: float = 30.0,
    ) -> None:
        self._approval_key = approval_key
        self._url = url
        self._connect = connect or _default_connector
        self._customer_type: CustomerType = customer_type
        self._reconnect = reconnect
        self._max_backoff = max_backoff
        self._ws: WebSocketLike | None = None
        # 재연결 시 재등록할 활성 구독. 값은 순서 보존 불필요(집합).
        self._subscriptions: set[tuple[str, str]] = set()
        # subscribe/unsubscribe 본문과 재접속 재등록을 직렬화한다. 이게 없으면 같은 키의 subscribe
        # 가 `await send` 에서 양보한 사이 unsubscribe 가 (아직 add 전이라) 미등록으로 보고 조기
        # 반환해 해제를 안 보내, 소켓은 구독된 채 슬롯만 새는 순서 경합이 난다(check-then-act).
        self._subscription_lock = asyncio.Lock()
        # stop() 이 세팅한다 -- backoff 대기를 즉시 깨워 stop 의 join(5s) 안에 스레드가 끝나게 한다
        # (blocking sleep 이면 최대 backoff(30s)까지 잔류해 join 이 실패하고 재시작이 fail-closed 로 막힘).
        self._stop_event = asyncio.Event()
        # 암호 TR 의 (key, iv) -- 구독 ACK 에서 수신.
        self._crypto: dict[str, tuple[str, str]] = {}

    async def __aenter__(self) -> Self:
        self._ws = await self._connect(self._url)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def stop(self) -> None:
        """재연결을 끄고 소켓을 닫아 수신 루프(``async for``)를 종료시킨다."""
        self._reconnect = False
        self._stop_event.set()          # backoff 대기를 즉시 깨운다(중단 가능)
        await self.close()

    async def _sleep_or_stop(self, seconds: float) -> None:
        """backoff 대기 -- stop() 이 오면 즉시 반환한다(그냥 asyncio.sleep 이면 못 깨운다)."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass                        # 정상 backoff 경과 -- 계속

    async def subscribe(self, tr_id: str, tr_key: str) -> None:
        """실시간 등록. 상한(41) 초과면 :class:`KISUsageError`. 재연결 후 자동 재등록된다."""
        # check-then-act(guard -> await send -> add)를 락으로 원자화한다 -- 같은 키 unsubscribe/
        # 재등록과 인터리브되면 순서가 뒤집혀 소켓 상태와 _subscriptions 가 어긋난다.
        async with self._subscription_lock:
            if (tr_id, tr_key) in self._subscriptions:
                return
            if len(self._subscriptions) >= _MAX_REGISTRATIONS:
                raise KISUsageError(
                    f"실시간 등록 상한({_MAX_REGISTRATIONS}) 초과 -- 일부 해제 후 등록하세요."
                )
            await self._send_subscription(tr_id, tr_key, subscribe=True)
            self._subscriptions.add((tr_id, tr_key))

    async def unsubscribe(self, tr_id: str, tr_key: str) -> None:
        """실시간 해제. 미등록이면 무시."""
        async with self._subscription_lock:
            if (tr_id, tr_key) not in self._subscriptions:
                return
            await self._send_subscription(tr_id, tr_key, subscribe=False)
            self._subscriptions.discard((tr_id, tr_key))

    async def _send_subscription(self, tr_id: str, tr_key: str, *, subscribe: bool) -> None:
        if self._ws is None:
            return  # 연결 없음(닫힘 중) -- 구독은 _subscriptions 에 남아 재연결 시 재전송된다.
        message = build_subscription_message(
            self._approval_key,
            tr_id,
            tr_key,
            subscribe=subscribe,
            customer_type=self._customer_type,
        )
        await self._ws.send(message)

    def __aiter__(self) -> AsyncIterator[RealtimeMessage]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[RealtimeMessage]:
        connection_closed = _connection_closed_errors()
        idle_backoff = 1.0
        if self._ws is None:  # __aenter__ 가 연결을 채운 뒤에만 호출된다(-O 에서도 지켜져야 함)
            raise RealtimeError("연결이 열리기 전에 메시지 루프가 시작됐다(내부 오류).")
        while True:
            delivered = False
            try:
                async for raw in self._ws:
                    if isinstance(raw, bytes):
                        # KIS 실시간은 텍스트 프레임만 쓴다 -- 비-str 프레임은 계약상 드롭한다.
                        _logger.warning("drop non-text frame (%d bytes)", len(raw))
                        continue
                    async for message in self._handle(raw):
                        delivered = True
                        yield message
            except connection_closed:
                pass
            # 정상 종료(async for 소진) 또는 연결 끊김 -> 재연결 여부 결정
            if not self._reconnect:
                return
            if delivered:
                idle_backoff = 1.0  # 정상 세션 뒤엔 즉시 재연결
            else:
                # accept-후-즉시-close(스로틀/장애) 재연결 폭주 방지: 프레임 없이 끝난 세션은 backoff.
                await self._sleep_or_stop(idle_backoff)
                idle_backoff = min(idle_backoff * 2, self._max_backoff)
            if not await self._reopen_with_backoff():
                return  # stop() 이 재연결을 껐다

    async def _handle(self, raw: str) -> AsyncIterator[RealtimeMessage]:
        try:
            frame = parse_frame(raw)
        except Exception:
            _logger.warning("drop unparseable frame: %r", raw[:80], exc_info=True)
            return
        if isinstance(frame, SystemMessage):
            ws = self._ws
            if frame.is_pingpong and ws is not None:
                # 공식 KIS 샘플과 동일하게 WebSocket PONG 제어프레임으로 응답(하트비트).
                await ws.pong(raw)
            elif frame.encryption_key is not None:
                self._crypto[frame.tr_id] = frame.encryption_key
            return
        # DataFrame -- 필요 시 복호화 후 파싱. 실패 프레임은 드롭(fail-safe, 스트림 미중단).
        try:
            messages = self._decode(frame)
        except Exception:
            _logger.warning("drop frame tr_id=%s (decode failed)", frame.tr_id, exc_info=True)
            return
        for message in messages:
            yield message

    def _decode(self, frame: DataFrame) -> list[RealtimeMessage]:
        working = frame
        if frame.encrypted:
            crypto = self._crypto.get(frame.tr_id)
            if crypto is None:
                return []  # 키 미수신 -> 드롭(다음 ACK 대기)
            plain = aes_cbc_decrypt(crypto[0], crypto[1], frame.payload)
            working = DataFrame(False, frame.tr_id, frame.record_count, plain)
        spec = _registry.lookup(working.tr_id)
        if spec is None:
            # 파서 미등록 -> 원시 필드로 흘려보냄(파서는 나중에 등록됨).
            fields = working.payload.split("^")
            return [RealtimeMessage(working.tr_id, fields[0] if fields else "", fields)]
        return [
            RealtimeMessage(working.tr_id, record[0] if record else "", spec.parser(record))
            for record in working.records(spec.field_count)
        ]

    async def _reopen_with_backoff(self) -> bool:
        """재연결 + 기존 구독 재등록. ``stop()`` 이 ``_reconnect`` 를 끄면 ``False`` 반환.

        연결 실패는 backoff 로 재시도하되, 매 시도 전 ``_reconnect`` 를 확인해 backoff sleep
        중 온 ``stop()`` 도 즉시 반영한다(스레드/소켓 누수 방지).
        """
        backoff = 1.0
        while self._reconnect:
            try:
                self._ws = await self._connect(self._url)
            except Exception:  # noqa: BLE001 - 연결 실패는 backoff 재시도
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                continue
            if not self._reconnect:
                # connect await 중 stop() 이 재연결을 껐다 -- 방금 연 소켓을 닫고 중단(누수·hang 방지).
                await self._ws.close()
                self._ws = None
                return False
            # 스냅샷과 재등록을 락 안에서 -- 재등록 도중 unsubscribe 가 끼어들어 방금 해제한 키를
            # 되살리지(resurrect) 못하게 한다. connect await 는 락 밖이라 재접속이 구독을 안 막는다.
            async with self._subscription_lock:
                for tr_id, tr_key in list(self._subscriptions):
                    await self._send_subscription(tr_id, tr_key, subscribe=True)
            return True
        return False


def _connection_closed_errors() -> type[BaseException]:
    """``websockets`` 연결종료 예외 타입(미설치 시 절대 안 잡히는 더미)."""
    try:
        from websockets.exceptions import ConnectionClosed

        return ConnectionClosed
    except ImportError:  # pragma: no cover
        class _Never(BaseException):
            pass

        return _Never
