"""KIS REST 도메인 -- 실전/모의 베이스 URL."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .transport import Environment

_DOMAIN = {
    "real": "https://openapi.koreainvestment.com:9443",
    "paper": "https://openapivts.koreainvestment.com:29443",
}

_WS_DOMAIN = {
    "real": "ws://ops.koreainvestment.com:21000",
    "paper": "ws://ops.koreainvestment.com:31000",
}


def base_url(environment: Environment) -> str:
    """실전(real)/모의(paper) REST 베이스 URL. 미지 환경은 KeyError(호출측 계약 오류)."""
    return _DOMAIN[environment]


def websocket_url(environment: Environment) -> str:
    """실전(real)/모의(paper) 실시간 WebSocket 베이스 URL."""
    return _WS_DOMAIN[environment]
