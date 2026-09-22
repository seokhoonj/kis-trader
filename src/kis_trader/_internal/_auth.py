"""KIS OAuth 접근 토큰 발급·캐시·갱신.

KIS ``/oauth2/tokenP`` 의 일반 OAuth JSON 응답을 처리한다. 프로세스 안에서는 메모리,
재시작 뒤에는 권한이 제한된 디스크 캐시를 재사용해 일일 발급 한도를 보호한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from ..errors import KISAuthError, KISUsageError
from ._endpoints import base_url
from ._fsutil import atomic_write_bytes, xdg_cache_subdir

if TYPE_CHECKING:
    from ..transport import Environment

TokenPoster = Callable[[str, Mapping[str, str]], tuple[int, Mapping[str, Any]]]


def _requests_post(url: str, body: Mapping[str, str]) -> tuple[int, Mapping[str, Any]]:
    """JSON 바디를 POST 하는 기본 구현. KIS ``/oauth2/tokenP`` 응답 코드와 JSON을 돌려준다."""
    import requests

    response = requests.post(url, json=dict(body), timeout=10)
    return response.status_code, response.json()


def default_token_cache_dir() -> str:
    """토큰 캐시 디렉터리(repo 밖, 런타임 캐시). ``XDG_CACHE_HOME`` 을 존중한다."""
    return str(xdg_cache_subdir("kis-trader", "tokens"))


class TokenManager:
    """KIS OAuth 접근 토큰 수명주기를 관리한다.

    ``/oauth2/tokenP`` 발급과 메모리·디스크 캐시 재사용을 한 잠금 안에서 직렬화한다.
    만료까지 ``refresh_margin`` 이하가 남은 토큰은 사용하지 않고 새로 발급한다.
    """

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        environment: Environment,
        post: TokenPoster = _requests_post,
        clock: Callable[[], float] = time.time,
        cache_dir: str | None = None,
        refresh_margin: int = 600,
    ) -> None:
        if refresh_margin < 0:
            # 음수 마진은 만료 이후에야 갱신하게 만들어 만료된 토큰을 유효로 오인시킨다.
            raise KISUsageError(f"refresh_margin 은 음수일 수 없다: {refresh_margin}")
        self._app_key = app_key
        self._app_secret = app_secret
        self._environment = environment
        self._post = post
        self._clock = clock
        self._cache_dir = cache_dir or default_token_cache_dir()
        self._refresh_margin = refresh_margin
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float | None = 0.0

    @property
    def _cache_path(self) -> str:
        digest = hashlib.sha256(self._app_key.encode()).hexdigest()[:16]
        return os.path.join(self._cache_dir, f"{self._environment}-{digest}.json")

    def _is_valid(self, token: object, expires_at: object, now: float) -> bool:
        return (
            isinstance(token, str)
            and bool(token.strip())
            and isinstance(expires_at, (int, float))
            and not isinstance(expires_at, bool)
            and now < float(expires_at) - self._refresh_margin
        )

    def _read_cache(self, now: float) -> tuple[str, float] | None:
        try:
            with open(self._cache_path, encoding="utf-8") as cached:
                payload = json.load(cached)
        except (OSError, ValueError):
            # OSError 는 FileNotFoundError 를, ValueError 는 JSONDecodeError 와 (비-UTF8 바이트의)
            # UnicodeDecodeError 를 포괄한다 -- 손상 캐시는 재발급으로 자가치유한다. 좁게 잡으면
            # UnicodeDecodeError 가 새어 토큰 획득이 매 호출 영구 고착된다.
            return None
        if not isinstance(payload, dict):
            return None
        token = payload.get("access_token")
        expires_at = payload.get("expires_at")
        if not self._is_valid(token, expires_at, now):
            return None
        # _is_valid 가 token: str, expires_at: 숫자임을 이미 보장(TypeGuard 아님이라 mypy 는 못 좁힘).
        return cast(str, token), float(cast(float, expires_at))

    def _write_cache(self, token: str, expires_at: float) -> None:
        # 토큰(브로커 접근권한)은 캐시 파일에 절대 world-readable 로 잠깐도 노출되면 안 된다.
        # 디렉터리를 0o700 으로 잠그고(makedirs 의 exist_ok 는 기존 디렉터리의 모드를 안 바꾸므로
        # chmod 로 강제한다 -- config_dir_override 로 caller 가 미리 만든 느슨한 디렉터리 대비), 예측
        # 불가한 임시파일에 0o600 으로 원자적으로 쓴다(atomic_write_bytes 는 mkstemp 라 고정 tmp 경로의
        # 심볼릭링크 선점/토큰 유출을 원천 차단한다).
        os.makedirs(self._cache_dir, mode=0o700, exist_ok=True)
        os.chmod(self._cache_dir, 0o700)
        payload = json.dumps({"access_token": token, "expires_at": expires_at}).encode("utf-8")
        atomic_write_bytes(self._cache_path, payload, mode=0o600)

    def _clear_cache(self) -> None:
        """디스크 토큰 캐시 파일을 지운다(폐기 후 재사용 방지). 없으면 무시."""
        try:
            os.remove(self._cache_path)
        except FileNotFoundError:
            pass

    def _issue(self, now: float) -> tuple[str, float]:
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        # 경계: 전송/JSON 파싱 실패는 raw 예외로 새지 않게 KISAuthError 로 좁힌다.
        try:
            status, payload = self._post(
                base_url(self._environment) + "/oauth2/tokenP", body
            )
        except KISAuthError:
            raise
        except Exception as err:
            raise KISAuthError("KIS OAuth 접근 토큰 발급 요청에 실패했다(전송/JSON 파싱 오류).") from err
        if status != 200:
            raise KISAuthError("KIS OAuth 접근 토큰 발급에 실패했다.")
        # 필드 접근 전에 본문이 매핑인지 확인한다(list/None 이면 .get 에서 AttributeError 가 샌다).
        if not isinstance(payload, Mapping):
            raise KISAuthError(
                f"KIS OAuth 응답 본문이 예상한 JSON 객체가 아니다: {type(payload).__name__}"
            )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise KISAuthError("KIS OAuth 응답에 접근 토큰이 없다.")
        try:
            expires_in = int(cast(Any, payload.get("expires_in")))
        except (TypeError, ValueError) as err:
            raise KISAuthError("KIS OAuth 응답의 토큰 유효기간이 올바르지 않다.") from err
        expires_at = now + expires_in
        if not self._is_valid(token, expires_at, now):
            raise KISAuthError("KIS OAuth 응답의 토큰 유효기간이 너무 짧다.")
        return token, expires_at

    def access_token(self) -> str:
        """현재 유효한 Bearer 토큰 문자열을 반환한다.

        메모리, 디스크 순으로 재사용하고 유효한 토큰이 없으면 KIS ``/oauth2/tokenP`` 에서
        발급한다. 발급 실패나 잘못된 OAuth 응답은 :class:`KISAuthError` 로 닫는다.
        """
        with self._lock:
            now = self._clock()
            if self._is_valid(self._token, self._expires_at, now):
                assert self._token is not None
                return self._token
            cached = self._read_cache(now)
            if cached is not None:
                self._token, self._expires_at = cached
                return self._token
            token, expires_at = self._issue(now)
            self._write_cache(token, expires_at)
            self._token = token
            self._expires_at = expires_at
            return token

    def revoke(self) -> None:
        """현재 접근 토큰을 KIS ``/oauth2/revokeP`` 로 폐기하고 메모리·디스크 캐시를 비운다.

        유효한 토큰이 없으면(발급한 적 없거나 이미 만료) 조용히 반환한다. 폐기 요청 실패는
        :class:`KISAuthError`. 폐기 후 다음 :meth:`access_token` 호출은 새 토큰을 재발급한다.
        """
        with self._lock:
            now = self._clock()
            token = self._token if self._is_valid(self._token, self._expires_at, now) else None
            if token is None:
                cached = self._read_cache(now)
                token = cached[0] if cached is not None else None
            self._token, self._expires_at = None, None
            self._clear_cache()
            if token is None:
                return
            body = {"appkey": self._app_key, "appsecret": self._app_secret, "token": token}
            status, _payload = self._post(base_url(self._environment) + "/oauth2/revokeP", body)
            if status != 200:
                raise KISAuthError("KIS OAuth 접근 토큰 폐기에 실패했다.")
