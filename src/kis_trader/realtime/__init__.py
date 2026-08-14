"""실시간(웹소켓) 시세/통보.

async 코어(:mod:`._connection`) 위에 동기 래퍼(:mod:`.client`)를 얹어, 스크립트에서는
콜백/이터레이터로, async 앱(FastAPI 등)에서는 코어를 직접 쓸 수 있게 한다. REST 는 동기
그대로 두고 이 서브패키지만 async 다.
"""

from __future__ import annotations

from .._internal._endpoints import websocket_url
from . import parsers  # noqa: F401  (import 부작용: TR 파서 레지스트리 등록)
from ._approval import fetch_approval_key
from ._connection import RealtimeConnection

__all__ = ["RealtimeConnection", "fetch_approval_key", "websocket_url"]
