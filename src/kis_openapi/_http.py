"""KIS REST API용 실제 HTTP 전송 구현.

실전·모의 KIS 도메인에 ``requests`` 로 접속하며 :class:`Transport` 계약을 구현한다.
읽기 GET만 타임아웃을 재시도하고 주문을 포함한 쓰기는 중복 체결 방지를 위해 재시도하지 않는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from ._auth import TokenManager
from ._endpoints import base_url
from ._ratelimit import SlidingWindowRateLimiter
from .errors import KISAuthError, KISError, KISRateLimitError
from .transport import Environment, RawResponse, TransportTimeout

HTTPResult = tuple[int, Mapping[str, str], Mapping[str, Any]]
HTTPSend = Callable[..., HTTPResult]
_SESSION: Any = None


def _requests_send(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, str] | None,
    json_body: Mapping[str, str] | None,
) -> HTTPResult:
    """기본 HTTP 송신기.

    목적: KIS REST 도메인에 한 요청을 보낸다.
    입력: 메서드·URL·표준 헤더·쿼리·JSON 바디를 받는다.
    출력: HTTP 상태, 응답 헤더, JSON 객체를 반환한다.
    오류: 연결 및 시간 초과는 :class:`TransportTimeout` 으로 변환한다.
    주의: 선택 사항인 hashkey는 사용하지 않는다.
    """
    import requests

    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
    try:
        response = _SESSION.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=10,
        )
    except (requests.Timeout, requests.ConnectionError) as err:
        raise TransportTimeout("KIS HTTP 요청의 결과를 확인할 수 없다.") from err
    return response.status_code, dict(response.headers), response.json()


class RequestsTransport:
    """``requests`` 기반 :class:`Transport` 구현.

    목적: 실전·모의 KIS REST 도메인 호출을 표준 응답으로 바꾼다.
    입력: 앱 자격증명, 환경, 지연 토큰 관리자와 주입 가능한 송신기를 받는다.
    출력: 성공 HTTP 응답과 KIS 오류 envelope 모두 :class:`RawResponse` 로 반환한다.
    오류: 전송·HTTP 상태 오류만 KIS 예외 계층으로 올린다.
    주의: 멱등 GET만 재시도하며 POST/쓰기는 중복 체결 방지를 위해 한 번만 보낸다.
    """

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        environment: Environment,
        token_manager: TokenManager,
        custtype: str = "P",
        send: HTTPSend = _requests_send,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._environment = environment
        self._token_manager = token_manager
        self._custtype = custtype
        self._send = send
        self._sleep = sleep
        if max_attempts < 1:  # 0 이면 어떤 시도도 못 해 결과가 불명 -- 생성 시점에 거부(fail-fast)
            raise ValueError(f"max_attempts 는 1 이상이어야 한다: {max_attempts}")
        self._max_attempts = max_attempts
        # 앱키 단위 호출 유량 제한기(선택). None 이면 스로틀 없음. 전송 시도마다 acquire.
        self._rate_limiter = rate_limiter

    def revoke_token(self) -> None:
        """현재 접근 토큰을 폐기한다(``/oauth2/revokeP``). 토큰 매니저에 위임."""
        self._token_manager.revoke()

    def request(
        self,
        *,
        method: str,
        path: str,
        tr_id: str,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, str] | None = None,
        idempotent: bool,
        tr_cont: str = "",
    ) -> RawResponse:
        """KIS ``Transport`` 계약에 따라 한 REST 요청을 수행한다.

        목적: KIS 도메인의 응답을 벤더 원형을 보존한 ``RawResponse`` 로 만든다.
        입력: 프로토콜의 메서드·경로·TR ID·쿼리·바디·멱등성 표지·연속조회 ``tr_cont`` 를 받는다.
        출력: HTTP 200의 표준 envelope와 응답 ``tr_cont`` 헤더를 반환한다.
        오류: 401/429/기타 HTTP 실패 및 잘못된 JSON을 KIS 예외로 변환한다.
        주의: GET+멱등 요청만 재시도하고 쓰기 요청의 타임아웃은 즉시 전파한다. ``tr_cont`` 는
        요청 헤더로 그대로 전달한다("" 초기, "N" 다음 페이지).
        """
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token_manager.access_token()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "tr_cont": tr_cont,
            "custtype": self._custtype,
        }
        url = base_url(self._environment) + path
        is_retryable = method.upper() == "GET" and idempotent
        attempts = self._max_attempts if is_retryable else 1

        for attempt in range(attempts):
            if self._rate_limiter is not None:
                self._rate_limiter.acquire()   # 앱키 유량 준수 -- 재시도되는 GET 은 시도마다 소모
            try:
                status, response_headers, payload = self._send(
                    method,
                    url,
                    headers=headers,
                    params=params if method.upper() == "GET" else None,
                    json_body=None if method.upper() == "GET" else body,
                )
                break
            except TransportTimeout:
                if not is_retryable or attempt + 1 == attempts:
                    raise
                self._sleep(0.1 * (attempt + 1))
            except ValueError as err:
                raise KISError("KIS HTTP 응답이 올바른 JSON이 아니다.") from err
        else:  # attempts>=1 이 보장돼(위 생성자 검증) 정상 흐름에선 닿지 않는 백스톱 -- payload 미정의 방지
            raise TransportTimeout("KIS HTTP 요청의 결과를 확인할 수 없다.")

        # 에러 응답도 KIS 봉투(rt_cd/msg_cd/msg1)를 실어 올려 호출자가 프로그램으로 분기할 수 있게 한다.
        # (401/429 EGW 응답도 대개 이 3필드를 담고 있다.) 봉투를 실는 건 경계(transport)의 책임이다.
        envelope = (
            {
                "rt_cd": str(payload.get("rt_cd", "")) or None,
                "msg_cd": str(payload.get("msg_cd", "")) or None,
                "msg1": str(payload.get("msg1", "")) or None,
                "raw": payload,
            }
            if isinstance(payload, Mapping)
            else {}
        )
        if status == 401:
            raise KISAuthError("KIS HTTP 인증에 실패했다 (status 401).", **envelope)
        if status == 429:
            raise KISRateLimitError("KIS HTTP 요청 한도를 초과했다 (status 429).", **envelope)
        if status != 200:
            raise KISError(f"KIS HTTP 요청에 실패했다 (status {status}).", **envelope)
        if not isinstance(payload, Mapping):
            raise KISError("KIS HTTP 응답 JSON이 객체가 아니다.")

        return RawResponse(
            rt_cd=str(payload.get("rt_cd", "")),
            msg_cd=str(payload.get("msg_cd", "")),
            msg1=str(payload.get("msg1", "")),
            body=payload,
            tr_cont=str(response_headers.get("tr_cont", "")),
        )
