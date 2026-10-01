"""HTTP 전송(sse/streamable-http)용 정적 bearer 토큰 게이트.

공개 노출의 1차 방어는 TLS·인증을 맡는 역프록시/터널이고(권장), 이 미들웨어는 그 뒤에 두는
**방어심층**이다 -- :envvar:`KIS_MCP_ACCESS_TOKEN` 이 설정되면 HTTP 요청에 ``Authorization: Bearer
<token>`` 를 요구한다(불일치/누락 → 401). 토큰 비교는 타이밍 공격을 피해 바이트 단위
:func:`hmac.compare_digest` 로 한다. 돈이 오가는 서버라 **deny-by-default** -- http 는 토큰을 검사하고
lifespan 만 통과시키며, 그 외 scope(websocket 등)는 닫는다. 미인증 경로를 열어 두지 않는다.
"""

from __future__ import annotations

import hmac

from starlette.types import ASGIApp, Receive, Scope, Send

_PREFIX = b"Bearer "


class BearerTokenMiddleware:
    """정적 bearer 토큰을 요구하는 순수 ASGI 미들웨어."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan" or (kind == "http" and self._authorized(scope)):
            await self._app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await _reject(send)

    def _authorized(self, scope: Scope) -> bool:
        # 정확히 1개의 Authorization 헤더만 인정(중복 헤더 스머글링 차단). 바이트로 비교해 비-ASCII
        # 토큰/헤더에도 예외 없이 상수시간 비교한다.
        headers = [v for (name, v) in (scope.get("headers") or []) if name == b"authorization"]
        if len(headers) != 1 or not headers[0].startswith(_PREFIX):
            return False
        return hmac.compare_digest(headers[0][len(_PREFIX):], self._token)


async def _reject(send: Send) -> None:
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
    })
    await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
