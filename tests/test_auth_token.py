"""KIS OAuth TokenManager의 발급·캐시·갱신 테스트."""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from kis_openapi._auth import TokenManager
from kis_openapi.errors import KISAuthError


class FakePoster:
    """호출을 기록하고 준비된 OAuth JSON 응답을 반환하는 가짜 poster."""

    def __init__(self, status: int = 200, payload: Mapping[str, Any] | None = None) -> None:
        self.status = status
        self.payload = payload or {
            "access_token": "canned-access-token",
            "token_type": "Bearer",
            "expires_in": "3600",
        }
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    def __call__(
        self, url: str, body: Mapping[str, str]
    ) -> tuple[int, Mapping[str, Any]]:
        self.calls.append((url, dict(body)))
        return self.status, self.payload


def manager(
    tmp_path: Path,
    poster: FakePoster,
    clock: list[float],
    *,
    environment: str = "real",
) -> TokenManager:
    """고정 자격증명과 주입 의존성으로 테스트용 manager를 만든다."""
    return TokenManager(
        app_key="test-app-key",
        app_secret="test-app-secret",
        environment=environment,
        post=poster,
        clock=lambda: clock[0],
        cache_dir=str(tmp_path),
        refresh_margin=600,
    )


@pytest.mark.parametrize(
    ("environment", "domain"),
    [
        ("real", "https://openapi.koreainvestment.com:9443"),
        ("demo", "https://openapivts.koreainvestment.com:29443"),
    ],
)
def test_first_access_posts_expected_request(
    tmp_path: Path, environment: str, domain: str
) -> None:
    poster = FakePoster()
    token_manager = manager(tmp_path, poster, [1_000.0], environment=environment)

    assert token_manager.access_token() == "canned-access-token"
    assert poster.calls == [
        (
            domain + "/oauth2/tokenP",
            {
                "grant_type": "client_credentials",
                "appkey": "test-app-key",
                "appsecret": "test-app-secret",
            },
        )
    ]


def test_memory_and_disk_cache_reuse_and_permissions(tmp_path: Path) -> None:
    clock = [1_000.0]
    poster = FakePoster()
    token_manager = manager(tmp_path, poster, clock)

    assert token_manager.access_token() == "canned-access-token"
    assert token_manager.access_token() == "canned-access-token"
    restarted = manager(tmp_path, poster, clock)
    assert restarted.access_token() == "canned-access-token"
    assert len(poster.calls) == 1

    cache_files = list(tmp_path.glob("*.json"))
    assert len(cache_files) == 1
    assert stat.S_IMODE(cache_files[0].stat().st_mode) == 0o600
    assert "canned-access-token" not in cache_files[0].name
    content = cache_files[0].read_text(encoding="utf-8")
    assert "test-app-secret" not in content
    assert "test-app-key" not in content
    assert set(json.loads(content)) == {"access_token", "expires_at"}


def test_refreshes_within_margin(tmp_path: Path) -> None:
    clock = [1_000.0]
    poster = FakePoster()
    token_manager = manager(tmp_path, poster, clock)
    token_manager.access_token()

    clock[0] = 4_000.0
    assert token_manager.access_token() == "canned-access-token"
    assert len(poster.calls) == 2


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (500, {"error_description": "failure"}),
        (200, {"expires_in": 3600}),
        (200, {"access_token": " ", "expires_in": 3600}),
        (200, {"access_token": "token", "expires_in": "invalid"}),
    ],
)
def test_auth_failures_raise(
    tmp_path: Path, status: int, payload: Mapping[str, Any]
) -> None:
    token_manager = manager(tmp_path, FakePoster(status, payload), [1_000.0])

    with pytest.raises(KISAuthError):
        token_manager.access_token()
