"""MCP 주문 확인(elicitation) 래퍼 -- ``mcp`` 의존을 여기 격리한다.

server 가 실주문 전에 :func:`confirm_order` 로 클라이언트에 **전체 주문 티켓**의 확인을 요청한다.
클라이언트는 사람이 내용을 검토하고 승인하도록 표시해야 한다. 서버는 승인 응답을 검사하며 실제
입력자가 사람인지 증명하지는 않는다. 클라이언트가 elicitation 을 지원하지 않아 백채널이 없으면
``False`` 를 돌려 실주문을 거부한다.
"""

from __future__ import annotations

from typing import Any, Protocol

from mcp.server.elicitation import AcceptedElicitation
from mcp.shared.exceptions import NoBackChannelError
from pydantic import BaseModel


class SupportsElicit(Protocol):
    """``ctx.elicit(message, schema)`` 를 가진 것(실제 MCP ``Context`` 와 테스트 fake 둘 다 만족). run_*/
    confirm_order 가 ``Any`` 대신 이 구조적 타입으로 ctx 를 받아 ``.elicit`` 오타를 타입체커가 잡게 한다."""

    async def elicit(self, message: str, schema: type) -> Any: ...


class _OrderConfirmation(BaseModel):
    """elicitation 응답 스키마 -- 승인에는 클라이언트의 ``confirm=true`` 응답이 필요하다."""

    confirm: bool


def _format_ticket(ticket: dict[str, Any]) -> str:
    # 값을 repr 로 -- 문자열 값(client_order_id/limit_price 등)에 개행을 끼워 확인 메시지에 가짜 줄을
    # 주입하는 표시 스푸핑을 막는다(개행이 \n 으로 이스케이프돼 한 줄에 남는다).
    return "\n".join(f"  {key}: {value!r}" for key, value in ticket.items())


async def confirm_order(ctx: SupportsElicit, ticket: dict[str, Any]) -> bool:
    """전체 주문 티켓의 확인을 클라이언트에 요청한다. ``accept`` + ``confirm`` 만 True.
    백채널 미지원·decline·cancel 은 False이며 그 밖의 예외는 호출자에게 전달한다."""
    message = (
        "다음 실주문을 전송하려 합니다. 내용을 확인하고 confirm 을 true 로 승인하세요"
        "(거절하면 전송하지 않습니다):\n" + _format_ticket(ticket)
    )
    try:
        result = await ctx.elicit(message=message, schema=_OrderConfirmation)
    except NoBackChannelError:
        return False                       # 클라가 elicitation 미지원(백채널 없음) -> fail-closed 거부
    return isinstance(result, AcceptedElicitation) and bool(result.data.confirm)
