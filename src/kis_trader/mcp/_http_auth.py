"""HTTP 전송(sse/streamable-http)용 정적 bearer 토큰 게이트.

공개 노출의 1차 방어는 TLS·인증을 맡는 역프록시/터널이고(권장), 이 미들웨어는 그 뒤에 두는
**방어심층**이다 -- :envvar:`KIS_MCP_ACCESS_TOKEN` 이 설정되면 모든 HTTP 요청에
``Authorization: Bearer <token>`` 를 요구한다(불일치/누락 → 401). 토큰 비교는 타이밍 공격을 피해
:func:`hmac.compare_digest` 로 한다. 돈이 오가는 서버라, 프록시가 뚫리거나 포트가 실수로 열려도
토큰 없이는 들어오지 못하게 한다.
"""

from __future__ import annotations

import hmac

from starlette.types import ASGIApp, Receive, Scope, Send

_PREFIX = "Bearer "


class BearerTokenMiddleware:
    """정적 bearer 토큰을 요구하는 순수 ASGI 미들웨어. HTTP 요청만 검사하고 lifespan 등은 통과."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._authorized(scope):
            await self._app(scope, receive, send)
            return
        await _reject(send)

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                raw = value.decode("latin-1")
                if raw.startswith(_PREFIX):
                    return hmac.compare_digest(raw[len(_PREFIX):], self._token)
                return False
        return False


async def _reject(send: Send) -> None:
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
    })
    await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
