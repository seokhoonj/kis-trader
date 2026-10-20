"""실시간 접속키 발급 + kis.realtime() 팩토리 테스트 (무네트워크)."""

from __future__ import annotations

import pytest

from kis_trader import KISClient
from kis_trader.errors import KISAuthError
from kis_trader.realtime._approval import fetch_approval_key


def test_fetch_approval_key_success():
    captured = {}

    def poster(url, body):
        captured["url"] = url
        captured["body"] = dict(body)
        return 200, {"approval_key": "APPROVAL-XYZ"}

    key = fetch_approval_key("APPKEY", "SECRET", "real", post=poster)
    assert key == "APPROVAL-XYZ"
    assert captured["url"].endswith("/oauth2/Approval")
    # Approval 은 secretkey 키를 쓴다(REST 의 appsecret 아님).
    assert captured["body"] == {
        "grant_type": "client_credentials",
        "appkey": "APPKEY",
        "secretkey": "SECRET",
    }


def test_fetch_approval_key_missing_key_fails():
    with pytest.raises(KISAuthError):
        fetch_approval_key("K", "S", "real", post=lambda u, b: (200, {"error": "no key"}))


def test_fetch_approval_key_bad_status_fails():
    with pytest.raises(KISAuthError):
        fetch_approval_key("K", "S", "real", post=lambda u, b: (403, {"approval_key": "x"}))


def test_fetch_approval_key_error_does_not_leak_returned_key():
    # 서버가 키를 발급했는데 상태/검증이 어긋나는 비정상 경로에서, 응답(approval_key 포함)이 예외
    # 문자열로 새면 안 된다 -- auth 오류는 자격증명을 실어 나르지 않는다(OAuth 토큰 발급 경로와 같은 규율).
    secret_key = "LEAKED-APPROVAL-KEY-DO-NOT-EXPOSE-0123456789"
    with pytest.raises(KISAuthError) as excinfo:
        fetch_approval_key("K", "S", "real", post=lambda u, b: (500, {"approval_key": secret_key}))
    assert secret_key not in str(excinfo.value)


def test_fetch_approval_key_transport_error_maps_to_auth_error():
    # 전송 오류(연결 실패 등, requests 예외는 OSError 계열)는 raw 로 새지 않고 KISAuthError 로 승격된다.
    def poster(url, body):
        raise OSError("connection refused")

    with pytest.raises(KISAuthError) as excinfo:
        fetch_approval_key("K", "S", "real", post=poster)
    assert excinfo.value.__cause__ is None


def test_fetch_approval_key_json_decode_error_maps_to_auth_error():
    # JSON 디코드 실패(ValueError)도 KISAuthError 로 승격된다.
    def poster(url, body):
        raise ValueError("not json")

    with pytest.raises(KISAuthError):
        fetch_approval_key("K", "S", "real", post=poster)


def test_fetch_approval_key_non_mapping_payload_rejected():
    # 응답이 매핑이 아니면(JSON 배열 등) AttributeError 대신 KISAuthError 로 거부한다.
    with pytest.raises(KISAuthError):
        fetch_approval_key("K", "S", "real", post=lambda u, b: (200, ["not", "a", "map"]))


def test_client_realtime_builds_client_with_ws_url(monkeypatch):
    monkeypatch.setattr(
        "kis_trader.realtime._approval.fetch_approval_key",
        lambda *a, **k: "APPROVAL-XYZ",
    )
    kis = KISClient(app_key="k", app_secret="s", account="12345678-01", environment="real")
    rt = kis.realtime()
    assert rt._approval_key == "APPROVAL-XYZ"
    assert rt._url == "ws://ops.koreainvestment.com:21000"
    assert rt._running is False  # 아직 start 안 함(무네트워크)


def test_client_realtime_paper_url(monkeypatch):
    monkeypatch.setattr(
        "kis_trader.realtime._approval.fetch_approval_key",
        lambda *a, **k: "K",
    )
    kis = KISClient(app_key="k", app_secret="s", account="12345678-01", environment="paper")
    rt = kis.realtime()
    assert rt._url == "ws://ops.koreainvestment.com:31000"
