"""MCP 주문 확인(elicitation) 래퍼 -- ``mcp`` 의존을 여기 격리한다.

server 가 실주문 전에 :func:`confirm_order` 로 사람 확인을 받는다. elicitation 은 서버가 멈추고
클라이언트를 통해 사람에게 **전체 주문 티켓**을 보여주고 accept 를 받는 흐름이라, 모델이 대신 승인할 수
없다(모델이 자기 토큰을 echo 할 수 있는 in-band 토큰과 다르다). 클라가 elicitation 을 지원하지 않으면
(백채널 없음) 예외를 삼켜 ``False`` 를 돌려 **fail-closed**(실주문 거부)한다.
"""

from __future__ import annotations

from typing import Any

from mcp.server.elicitation import AcceptedElicitation
from mcp.shared.exceptions import NoBackChannelError
from pydantic import BaseModel


class _OrderConfirmation(BaseModel):
    """elicitation 응답 스키마(primitive 만) -- 사람이 ``confirm`` 을 true 로 줘야 승인."""

    confirm: bool


def _format_ticket(ticket: dict[str, Any]) -> str:
    return "\n".join(f"  {key}: {value}" for key, value in ticket.items())


async def confirm_order(ctx: Any, ticket: dict[str, Any]) -> bool:
    """전체 주문 티켓을 사람에게 보여주고 승인(``confirm=true``)을 받는다. ``accept`` + ``confirm`` 만
    True. 클라 미지원(백채널 없음)·decline·cancel·오류는 전부 False(fail-closed)."""
    message = (
        "다음 실주문을 전송하려 합니다. 내용을 확인하고 confirm 을 true 로 승인하세요"
        "(거절하면 전송하지 않습니다):\n" + _format_ticket(ticket)
    )
    try:
        result = await ctx.elicit(message=message, schema=_OrderConfirmation)
    except NoBackChannelError:
        return False                       # 클라가 elicitation 미지원(백채널 없음) -> fail-closed 거부
    return isinstance(result, AcceptedElicitation) and bool(result.data.confirm)
