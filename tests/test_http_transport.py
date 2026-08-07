"""실제 HTTP 전송의 배선, 응답 변환, 주문 안전 재시도 정책을 검증한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from kis_openapi._auth import TokenManager
from kis_openapi._http import RequestsTransport
from kis_openapi.client import KISClient
from kis_openapi.errors import KISAuthError, KISError, KISRateLimitError
from kis_openapi.transport import TransportTimeout


def _token_manager(tmp_path: Any, environment: str = "real") -> TokenManager:
    return TokenManager(
        app_key="key",
        app_secret="secret",
        environment=environment,
        post=lambda url, body: (200, {"access_token": "token", "expires_in": 3600}),
        clock=lambda: 1_000.0,
        cache_dir=str(tmp_path),
        refresh_margin=0,
    )


def _transport(tmp_path: Any, send: Any, **kwargs: Any) -> RequestsTransport:
    environment = kwargs.pop("environment", "real")
    return RequestsTransport(
        app_key="key",
        app_secret="secret",
        environment=environment,
        token_manager=_token_manager(tmp_path, environment),
        send=send,
        sleep=lambda delay: None,
        **kwargs,
    )


def test_get_builds_request_and_exposes_tr_cont(tmp_path: Any) -> None:
    calls: list[dict[str, Any]] = []
    payload = {"rt_cd": "0", "msg_cd": "OK", "msg1": "정상", "output": {"x": "1"}}

    def send(method: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Mapping[str, Any]]:
        calls.append({"method": method, "url": url, **kwargs})
        return 200, {"tr_cont": "M"}, payload

    response = _transport(tmp_path, send).request(
        method="GET",
        path="/uapi/test",
        tr_id="TR001",
        params={"code": "005930"},
        idempotent=True,
    )

    assert calls[0]["url"] == "https://openapi.koreainvestment.com:9443/uapi/test"
    assert calls[0]["headers"] == {
        "content-type": "application/json; charset=utf-8",
        "authorization": "Bearer token",
        "appkey": "key",
        "appsecret": "secret",
        "tr_id": "TR001",
        "tr_cont": "",
        "custtype": "P",
    }
    assert calls[0]["params"] == {"code": "005930"}
    assert calls[0]["json_body"] is None
    assert (response.rt_cd, response.msg_cd, response.msg1) == ("0", "OK", "정상")
    assert dict(response.body) == payload
    assert response.tr_cont == "M"


def test_demo_uses_virtual_domain(tmp_path: Any) -> None:
    urls: list[str] = []

    def send(method: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Mapping[str, Any]]:
        urls.append(url)
        return 200, {}, {"rt_cd": "0"}

    _transport(tmp_path, send, environment="demo").request(
        method="GET", path="/test", tr_id="TR", idempotent=True
    )
    assert urls == ["https://openapivts.koreainvestment.com:29443/test"]


def test_post_timeout_is_never_retried(tmp_path: Any) -> None:
    calls = 0

    def send(method: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Mapping[str, Any]]:
        nonlocal calls
        calls += 1
        assert kwargs["params"] is None
        assert kwargs["json_body"] == {"QTY": "1"}
        raise TransportTimeout("unknown outcome")

    with pytest.raises(TransportTimeout):
        _transport(tmp_path, send, max_attempts=5).request(
            method="POST",
            path="/order",
            tr_id="ORDER",
            body={"QTY": "1"},
            idempotent=False,
        )
    assert calls == 1


def test_get_timeout_retries_to_limit(tmp_path: Any) -> None:
    calls = 0

    def send(method: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Mapping[str, Any]]:
        nonlocal calls
        calls += 1
        raise TransportTimeout("temporary")

    with pytest.raises(TransportTimeout):
        _transport(tmp_path, send, max_attempts=4).request(
            method="GET", path="/quote", tr_id="QUOTE", idempotent=True
        )
    assert calls == 4


def test_get_timeout_can_succeed_on_retry(tmp_path: Any) -> None:
    calls = 0

    def send(method: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Mapping[str, Any]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransportTimeout("temporary")
        return 200, {}, {"rt_cd": "0", "msg_cd": "OK", "msg1": "정상"}

    response = _transport(tmp_path, send).request(
        method="GET", path="/quote", tr_id="QUOTE", idempotent=True
    )
    assert calls == 2
    assert response.ok


def test_rt_cd_error_envelope_is_returned(tmp_path: Any) -> None:
    def send(method: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Mapping[str, Any]]:
        return 200, {}, {"rt_cd": "1", "msg_cd": "BAD", "msg1": "거부"}

    response = _transport(tmp_path, send).request(
        method="POST", path="/order", tr_id="ORDER", body={}, idempotent=False
    )
    assert not response.ok
    assert response.msg_cd == "BAD"


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(401, KISAuthError), (429, KISRateLimitError), (500, KISError)],
)
def test_http_status_errors(tmp_path: Any, status: int, error_type: type[KISError]) -> None:
    def send(method: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Mapping[str, Any]]:
        return status, {}, {}

    with pytest.raises(error_type):
        _transport(tmp_path, send).request(
            method="GET", path="/test", tr_id="TR", idempotent=True
        )


def test_client_constructs_lazy_real_transport() -> None:
    client = KISClient(app_key="k", app_secret="s")
    assert isinstance(client.transport, RequestsTransport)
