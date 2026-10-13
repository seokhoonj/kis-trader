"""KIS REST API용 실제 HTTP 전송 구현.

실전·모의 KIS 도메인에 ``requests`` 로 접속하며 :class:`Transport` 계약을 구현한다.
읽기 GET만 타임아웃을 재시도하고 주문을 포함한 쓰기는 중복 체결 방지를 위해 재시도하지 않는다.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, cast

from ..errors import KISAuthError, KISError, KISRateLimitError, _KISErrorKwargs
from ..transport import Environment, RawResponse, TransportTimeoutError

if TYPE_CHECKING:
    from .._literals import JSONObject
from ._auth import TokenManager
from ._endpoints import base_url
from ._ratelimit import SlidingWindowRateLimiter

HTTPResult = tuple[int, Mapping[str, str], object]

#: 헤더 값에 금지되는 제어문자(CR/LF 는 헤더 분할, 그 밖의 C0/DEL 은 비정상 자격증명 표지).
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _reject_control_chars(label: str, value: str) -> None:
    """자격증명/토큰에 제어문자(CR/LF 등)가 있으면 **값 없이** 거부한다. 메시지에는 어떤 필드인지
    (label)만 담고 그 값은 절대 넣지 않아, 자격증명/토큰이 예외 문자열·로그에 노출되지 않게 한다.
    또한 이런 값이 그대로 전송돼 헤더 분할(CRLF injection)로 번지는 것을 전송 전에 막는다."""
    if _CONTROL_CHARS.search(value):
        raise KISError(f"KIS 요청 {label} 에 허용되지 않는 제어문자가 있다.")


class _HTTPSender(Protocol):
    """HTTP 송신기 계약 -- 한 요청을 보내고 (상태, 헤더, 파싱된 바디)를 돌려준다.

    ``RequestsTransport`` 에 주입되는 실제/가짜 송신기가 만족해야 하는 형이다. 바디는
    :data:`HTTPResult` 대로 ``object`` 라, 호출자가 ``Mapping`` 여부를 좁혀 써야 한다.
    """

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None,
        json_body: Mapping[str, str] | None,
    ) -> HTTPResult: ...


_SESSION: Any = None
#: ``_SESSION`` 지연 생성 경쟁 방지. 잠금 없이 첫 호출이 겹치면 Session 이 둘 생겨 하나가 샌다.
_SESSION_LOCK = threading.Lock()


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
    오류: 연결·시간 초과 및 그 밖의 전송 계층 오류(TooManyRedirects/RetryError/ChunkedEncoding 등,
      요청이 전송됐을 수 있음)는 :class:`TransportTimeoutError` 으로, 요청 구성 단계 오류(InvalidURL/
      MissingSchema 등, 전송 전이라 'not sent' 확실)는 평범한 :class:`KISError` 로 변환한다.
    주의: 선택 사항인 hashkey는 사용하지 않는다.
    """
    import requests

    global _SESSION
    if _SESSION is None:
        with _SESSION_LOCK:  # double-checked: 두 스레드가 동시에 None 을 봐도 Session 은 하나만 만든다
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
        raise TransportTimeoutError("KIS HTTP 요청의 결과를 확인할 수 없다.") from err
    except requests.exceptions.InvalidHeader:
        # 헤더 값이 유효하지 않다(대개 자격증명/토큰에 CR/LF 등 비정상 문자). requests 의 예외
        # 메시지는 문제의 헤더 '값' 을 그대로 담으므로, 그 값을 KISError 에 끌고 들어오지 않도록
        # 'from None' 으로 체인을 끊고 값 없는 메시지만 올린다(자격증명/토큰 유출 방지).
        raise KISError("KIS HTTP 요청 헤더가 올바르지 않다.") from None
    except (
        requests.exceptions.URLRequired,
        requests.exceptions.MissingSchema,
        requests.exceptions.InvalidSchema,
        requests.exceptions.InvalidURL,
    ) as err:
        # 요청 준비 단계 오류 -- 소켓에 닿기 전이라 'not sent' 가 확실하다. 결과 불명(no-retry)
        # 신호를 실을 필요 없이 평범한 KISError 로 올린다(사실상 우리 URL 구성 버그, 값에 비밀 없음).
        raise KISError(f"KIS HTTP 요청을 구성하지 못했다: {err}") from err
    except requests.RequestException as err:
        # 그 밖의 전송 계층 오류 -- 여기엔 ChunkedEncodingError/ContentDecodingError(둘 다
        # ConnectionError 가 아니라 RequestException 직계라 위 첫 분기에 안 걸린다)와
        # TooManyRedirects/RetryError 등이 포함된다. 응답을 받던/전송됐을 수 있어(sent-unknown)
        # 결과가 불명하므로, 타임아웃과 같이 재전송 금지 신호로 올려 쓰기 중복을 막는다.
        raise TransportTimeoutError("KIS HTTP 전송 계층 오류 -- 요청의 결과를 확인할 수 없다.") from err
    # 헤더 키를 소문자로 정규화 -- requests 의 대소문자 무시 맵을 평범한 dict 로 바꾸면서도 tr_cont 등
    # 하위 연속조회가 서버/프록시의 Title-Case 표기에도 매칭되게 한다(페이지네이션 조용한 절단 방지).
    return response.status_code, {k.lower(): v for k, v in response.headers.items()}, response.json()


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
        send: _HTTPSender = _requests_send,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        #: 이 전송이 향하는 KIS 환경. :class:`~kis_trader.transport.Transport` 계약(공개)으로,
        #: 세션이 안전 게이트와 소켓 목적지의 환경 일치를 확인하는 데 읽는다.
        self.environment = environment
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
        token = self._token_manager.access_token()
        # 자격증명/토큰에 CR/LF 등 제어문자가 있으면 전송 전에 값 없는 에러로 막는다 -- 헤더 분할
        # (CRLF injection)과, requests 가 그 값을 담아 올릴 때의 유출을 둘 다 차단한다.
        _reject_control_chars("app key", self._app_key)
        _reject_control_chars("app secret", self._app_secret)
        _reject_control_chars("access token", token)
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "tr_cont": tr_cont,
            "custtype": self._custtype,
        }
        url = base_url(self.environment) + path
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
            except TransportTimeoutError:
                if not is_retryable or attempt + 1 == attempts:
                    raise
                self._sleep(0.1 * (attempt + 1))
                continue
            except ValueError as err:
                raise KISError("KIS HTTP 응답이 올바른 JSON이 아니다.") from err
            # KIS 는 앱키 유량 초과를 HTTP 500 으로 알린다(공식 429 가 아니라) -- 같은 앱키를 다른
            # 앱/프로세스가 동시에 쓰거나 연속조회가 몰릴 때 흔하다. 재조회가 안전한 멱등 GET 은
            # 백오프 후 재시도해 자연히 회복한다(장기간 bars 등의 연속조회가 통째로 실패하지 않게).
            # 쓰기는 is_retryable=False 라 여기 안 걸리고 아래에서 즉시 오류가 된다.
            if status == 500 and is_retryable and attempt + 1 < attempts:
                self._sleep(0.2 * (attempt + 1))
                continue
            break
        else:  # attempts>=1 이 보장돼(위 생성자 검증) 정상 흐름에선 닿지 않는 백스톱 -- payload 미정의 방지
            raise TransportTimeoutError("KIS HTTP 요청의 결과를 확인할 수 없다.")

        # 에러 응답도 KIS 봉투(rt_cd/msg_cd/msg1)를 실어 올려 호출자가 프로그램으로 분기할 수 있게 한다.
        # (401/429 EGW 응답도 대개 이 3필드를 담고 있다.) 봉투를 실는 건 경계(transport)의 책임이다.
        envelope: _KISErrorKwargs = (
            {
                "rt_cd": str(payload.get("rt_cd", "")) or None,
                "msg_cd": str(payload.get("msg_cd", "")) or None,
                "msg1": str(payload.get("msg1", "")) or None,
                "raw": cast("JSONObject", payload),
            }
            if isinstance(payload, Mapping)
            else {}
        )
        if status == 401:
            raise KISAuthError("KIS HTTP 인증에 실패했다 (status 401).", **envelope)
        if status == 429:
            raise KISRateLimitError("KIS HTTP 요청 한도를 초과했다 (status 429).", **envelope)
        if envelope.get("msg_cd") == "EGW00201":
            # KIS 는 앱키 유량 초과를 HTTP 500 + msg_cd=EGW00201 로 알린다(공식 429 가 아니라). 재시도가
            # 소진된 뒤엔 일반 KISError 가 아니라 KISRateLimitError 로 분류해 호출자가 한도초과를 구분하게.
            raise KISRateLimitError("KIS HTTP 요청 한도를 초과했다 (EGW00201).", **envelope)
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
