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
from typing import TYPE_CHECKING, Any

from ._endpoints import base_url
from .errors import KISAuthError

if TYPE_CHECKING:
    from .transport import Environment

TokenPoster = Callable[[str, Mapping[str, str]], tuple[int, Mapping[str, Any]]]


def _requests_post(url: str, body: Mapping[str, str]) -> tuple[int, Mapping[str, Any]]:
    """JSON 바디를 POST 하는 기본 구현. KIS ``/oauth2/tokenP`` 응답 코드와 JSON을 돌려준다."""
    import requests

    response = requests.post(url, json=dict(body), timeout=10)
    return response.status_code, response.json()


def default_token_cache_dir() -> str:
    """토큰 캐시 디렉터리(repo 밖, 런타임 캐시). ``XDG_CACHE_HOME`` 을 존중한다."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(base, "kis-openapi", "tokens")


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
        self._app_key = app_key
        self._app_secret = app_secret
        self._environment = environment
        self._post = post
        self._clock = clock
        self._cache_dir = cache_dir or default_token_cache_dir()
        self._refresh_margin = refresh_margin
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    @property
    def _cache_path(self) -> str:
        digest = hashlib.sha256(self._app_key.encode()).hexdigest()[:16]
        return os.path.join(self._cache_dir, f"{self._environment}-{digest}.json")

    def _valid(self, token: object, expires_at: object, now: float) -> bool:
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
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        token = payload.get("access_token")
        expires_at = payload.get("expires_at")
        if not self._valid(token, expires_at, now):
            return None
        return token, float(expires_at)

    def _write_cache(self, token: str, expires_at: float) -> None:
        # 토큰(브로커 접근권한)은 캐시 파일에 절대 world-readable 로 잠깐도 노출되면 안 된다.
        # 임시 파일을 처음부터 0o600 으로 만들고(먼저 열고 chmod 하면 그 사이 창에서 읽힌다),
        # 디렉터리도 0o700 으로 잠근 뒤 원자적 교체한다.
        os.makedirs(self._cache_dir, mode=0o700, exist_ok=True)
        path = self._cache_path
        tmp = f"{path}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"access_token": token, "expires_at": expires_at}, out)
        os.replace(tmp, path)

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
        status, payload = self._post(
            base_url(self._environment) + "/oauth2/tokenP", body
        )
        if status != 200:
            raise KISAuthError("KIS OAuth 접근 토큰 발급에 실패했다.")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise KISAuthError("KIS OAuth 응답에 접근 토큰이 없다.")
        try:
            expires_in = int(payload.get("expires_in"))
        except (TypeError, ValueError) as err:
            raise KISAuthError("KIS OAuth 응답의 토큰 유효기간이 올바르지 않다.") from err
        expires_at = now + expires_in
        if not self._valid(token, expires_at, now):
            raise KISAuthError("KIS OAuth 응답의 토큰 유효기간이 너무 짧다.")
        return token, expires_at

    def access_token(self) -> str:
        """현재 유효한 Bearer 토큰 문자열을 반환한다.

        메모리, 디스크 순으로 재사용하고 유효한 토큰이 없으면 KIS ``/oauth2/tokenP`` 에서
        발급한다. 발급 실패나 잘못된 OAuth 응답은 :class:`KISAuthError` 로 닫는다.
        """
        with self._lock:
            now = self._clock()
            if self._valid(self._token, self._expires_at, now):
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
            token = self._token if self._valid(self._token, self._expires_at, now) else None
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
