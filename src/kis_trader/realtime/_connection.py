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
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from . import _registry
from ._protocol import (
    CustomerType,
    DataFrame,
    SystemMessage,
    aes_cbc_decrypt,
    build_subscription_message,
    parse_frame,
)

#: ``connect(url) -> ws`` -- ws 는 ``async for`` / ``send`` / ``close`` 를 지원하는 연결 객체.
Connector = Callable[[str], Awaitable[Any]]

_MAX_REGISTRATIONS = 41  # approval_key 당 실시간 등록 상한(KIS)


@dataclass(frozen=True, slots=True)
class RealtimeMessage:
    """한 실시간 레코드. ``data`` 는 파서가 있으면 엔티티, 없으면 원시 필드(``list[str]``)."""

    tr_id: str
    tr_key: str
    data: Any


async def _default_connector(url: str) -> Any:
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover - 설치 안내
        raise RuntimeError(
            "실시간 WebSocket 에는 'websockets' 가 필요합니다: pip install kis-trader[realtime]"
        ) from exc
    return await websockets.connect(url)


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
        self._ws: Any = None
        # 재연결 시 재등록할 활성 구독. 값은 순서 보존 불필요(집합).
        self._subscriptions: set[tuple[str, str]] = set()
        # 암호 TR 의 (key, iv) -- 구독 ACK 에서 수신.
        self._crypto: dict[str, tuple[str, str]] = {}

    async def __aenter__(self) -> RealtimeConnection:
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
        await self.close()

    async def subscribe(self, tr_id: str, tr_key: str) -> None:
        """실시간 등록. 상한(41) 초과면 ``RuntimeError``. 재연결 후 자동 재등록된다."""
        if (tr_id, tr_key) in self._subscriptions:
            return
        if len(self._subscriptions) >= _MAX_REGISTRATIONS:
            raise RuntimeError(
                f"실시간 등록 상한({_MAX_REGISTRATIONS}) 초과 -- 일부 해제 후 등록하세요."
            )
        await self._send_subscription(tr_id, tr_key, subscribe=True)
        self._subscriptions.add((tr_id, tr_key))

    async def unsubscribe(self, tr_id: str, tr_key: str) -> None:
        """실시간 해제. 미등록이면 무시."""
        if (tr_id, tr_key) not in self._subscriptions:
            return
        await self._send_subscription(tr_id, tr_key, subscribe=False)
        self._subscriptions.discard((tr_id, tr_key))

    async def _send_subscription(self, tr_id: str, tr_key: str, *, subscribe: bool) -> None:
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
        while True:
            try:
                async for raw in self._ws:
                    async for message in self._handle(raw):
                        yield message
            except connection_closed:
                pass
            # 정상 종료(async for 소진) 또는 연결 끊김 -> 재연결 여부 결정
            if not self._reconnect:
                return
            await self._reopen_with_backoff()

    async def _handle(self, raw: str) -> AsyncIterator[RealtimeMessage]:
        frame = parse_frame(raw)
        if isinstance(frame, SystemMessage):
            if frame.is_pingpong:
                await self._ws.send(raw)  # PINGPONG echo
            elif frame.encryption_key is not None:
                self._crypto[frame.tr_id] = frame.encryption_key
            return
        # DataFrame -- 필요 시 복호화 후 파싱
        working = frame
        if frame.encrypted:
            crypto = self._crypto.get(frame.tr_id)
            if crypto is None:
                return  # 키 미수신 -> 드롭(다음 ACK 대기)
            plain = aes_cbc_decrypt(crypto[0], crypto[1], frame.payload)
            working = DataFrame(False, frame.tr_id, frame.record_count, plain)
        spec = _registry.lookup(working.tr_id)
        if spec is None:
            # 파서 미등록 -> 원시 필드로 흘려보냄(파서는 나중에 등록됨).
            fields = working.payload.split("^")
            yield RealtimeMessage(working.tr_id, fields[0] if fields else "", fields)
            return
        for record in working.records(spec.field_count):
            yield RealtimeMessage(working.tr_id, record[0] if record else "", spec.parser(record))

    async def _reopen_with_backoff(self) -> bool:
        backoff = 1.0
        while True:
            try:
                self._ws = await self._connect(self._url)
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                continue
            # 재등록
            for tr_id, tr_key in list(self._subscriptions):
                await self._send_subscription(tr_id, tr_key, subscribe=True)
            return True


def _connection_closed_errors() -> type[BaseException]:
    """``websockets`` 연결종료 예외 타입(미설치 시 절대 안 잡히는 더미)."""
    try:
        from websockets.exceptions import ConnectionClosed

        return ConnectionClosed
    except ImportError:  # pragma: no cover
        class _Never(BaseException):
            pass

        return _Never
