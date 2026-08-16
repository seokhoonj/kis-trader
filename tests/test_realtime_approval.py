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
