"""실시간 WebSocket 접속키(approval_key) 발급.

REST OAuth 토큰(``/oauth2/tokenP``)과 별개인 ``/oauth2/Approval`` 을 쓴다. 바디 키가 REST 와
다르다 -- ``secretkey``(REST 는 ``appsecret``). 응답의 ``approval_key`` 를 반환한다. ``post`` 를
주입할 수 있어 실서버 없이 테스트한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from .._internal._auth import _requests_post
from .._internal._endpoints import base_url
from ..errors import KISAuthError

if TYPE_CHECKING:
    from ..transport import Environment

Poster = Callable[[str, Mapping[str, str]], tuple[int, Mapping[str, Any]]]


def fetch_approval_key(
    app_key: str,
    app_secret: str,
    environment: Environment,
    *,
    post: Poster | None = None,
) -> str:
    """``/oauth2/Approval`` 로 실시간 접속키를 발급받는다. 실패 시 :class:`KISAuthError`."""
    poster = post or _requests_post
    url = base_url(environment) + "/oauth2/Approval"
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "secretkey": app_secret,
    }
    try:
        status, data = poster(url, body)
    except (OSError, ValueError) as exc:
        # 전송 오류(requests 예외는 OSError 계열)나 JSON 디코드 실패(ValueError)를 그대로 올리면
        # 호출자가 raw 예외를 떠안는다 -- auth 계약에 맞춰 KISAuthError 로 승격한다(원문은 안 싣는다).
        raise KISAuthError("실시간 접속키 발급 실패 -- 전송/응답 처리 오류.") from exc
    if not isinstance(data, Mapping):
        # 응답이 매핑이 아니면(예: JSON 배열) ``.get`` 이 AttributeError 를 낸다 -- 먼저 거부한다.
        raise KISAuthError(f"실시간 접속키 발급 실패 (status={status}).") from None
    approval_key = data.get("approval_key")
    # data 는 Mapping[str, Any] 라 truthy 만으론 str 을 보장 못 한다 -- isinstance 로 좁혀야 반환 계약이 정직하다.
    if status != 200 or not isinstance(approval_key, str) or not approval_key:
        # 응답 본문(data)을 메시지에 넣지 않는다 -- 서버가 키를 발급했는데 검증/상태가 어긋난
        # 비정상 경로에서 응답에 approval_key(자격증명)가 담길 수 있어 예외 문자열로 유출된다.
        # OAuth 토큰 발급 경로와 같은 규율: auth 오류는 자격증명을 실어 나르지 않는다.
        raise KISAuthError(f"실시간 접속키 발급 실패 (status={status})") from None
    return approval_key
