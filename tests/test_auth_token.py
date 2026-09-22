"""KIS OAuth TokenManager의 발급·캐시·갱신 테스트."""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from kis_trader._internal._auth import TokenManager
from kis_trader.errors import KISAuthError, KISUsageError


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
        ("paper", "https://openapivts.koreainvestment.com:29443"),
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


def test_corrupt_cache_with_invalid_utf8_self_heals(tmp_path: Path) -> None:
    # 비-UTF8 바이트로 손상된 캐시가 UnicodeDecodeError 로 새면 토큰 획득이 매 호출 영구 고착된다 --
    # _read_cache 가 (OSError, ValueError) 로 포괄해 재발급으로 자가치유해야 한다.
    clock = [1_000.0]
    poster = FakePoster()
    manager(tmp_path, poster, clock).access_token()          # 캐시 파일 생성
    next(tmp_path.glob("*.json")).write_bytes(b"\xff\xfe not valid utf-8")  # 손상(비-UTF8)
    restarted = manager(tmp_path, poster, clock)
    assert restarted.access_token() == "canned-access-token"  # raise 없이 재발급으로 자가치유
    assert len(poster.calls) == 2                            # 첫 발급 + 손상 후 재발급


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


def test_negative_refresh_margin_rejected(tmp_path: Path) -> None:
    # 음수 마진은 만료 이후에야 갱신하게 만들어 만료 토큰을 유효로 오인시킨다 -- 생성 시 거부.
    with pytest.raises(KISUsageError):
        TokenManager(
            app_key="k", app_secret="s", environment="real",
            post=FakePoster(), clock=lambda: 1_000.0,
            cache_dir=str(tmp_path), refresh_margin=-1,
        )


def test_zero_and_positive_refresh_margin_accepted(tmp_path: Path) -> None:
    # 0 과 양수 마진은 그대로 허용(parity).
    for margin in (0, 600):
        tm = TokenManager(
            app_key="k", app_secret="s", environment="real",
            post=FakePoster(), clock=lambda: 1_000.0,
            cache_dir=str(tmp_path), refresh_margin=margin,
        )
        assert tm.access_token() == "canned-access-token"


def test_transport_failure_narrows_to_auth_error(tmp_path: Path) -> None:
    # poster 가 던지는 전송/JSON 오류는 raw 예외로 새지 않고 KISAuthError 로 좁혀진다.
    def boom(url: str, body: Mapping[str, str]) -> tuple[int, Mapping[str, Any]]:
        raise ValueError("json decode blew up")

    tm = TokenManager(
        app_key="k", app_secret="s", environment="real",
        post=boom, clock=lambda: 1_000.0, cache_dir=str(tmp_path), refresh_margin=600,
    )
    with pytest.raises(KISAuthError):
        tm.access_token()


@pytest.mark.parametrize("payload", [["not", "a", "mapping"], "string-body", 42])
def test_non_mapping_body_raises_auth_error(tmp_path: Path, payload: Any) -> None:
    # 매핑이 아닌 본문은 필드 접근 전에 KISAuthError 로 거부(AttributeError 누출 방지).
    token_manager = manager(tmp_path, FakePoster(200, payload), [1_000.0])
    with pytest.raises(KISAuthError):
        token_manager.access_token()


def test_revoke_posts_revokep_and_clears(tmp_path: Path) -> None:
    poster = FakePoster()
    clock = [1_000.0]
    token_manager = manager(tmp_path, poster, clock)
    token = token_manager.access_token()          # 발급 + 캐시
    assert Path(token_manager._cache_path).exists()
    token_manager.revoke()
    # revokeP 로 폐기 요청(appkey/appsecret/token)
    url, body = poster.calls[-1]
    assert url.endswith("/oauth2/revokeP")
    assert body == {"appkey": "test-app-key", "appsecret": "test-app-secret", "token": token}
    assert not Path(token_manager._cache_path).exists()   # 캐시 삭제
    # 폐기 후 다음 호출은 재발급
    before = len(poster.calls)
    token_manager.access_token()
    assert len(poster.calls) == before + 1


def test_revoke_without_token_is_noop(tmp_path: Path) -> None:
    poster = FakePoster()
    token_manager = manager(tmp_path, poster, [1_000.0])
    token_manager.revoke()                         # 발급한 적 없음 -> 조용히 반환
    assert poster.calls == []


def test_revoke_failure_raises(tmp_path: Path) -> None:
    poster = FakePoster()
    token_manager = manager(tmp_path, poster, [1_000.0])
    token_manager.access_token()
    poster.status = 500                            # 폐기 요청 실패
    with pytest.raises(KISAuthError):
        token_manager.revoke()
