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


class _RecordingLimiter:
    def __init__(self) -> None:
        self.acquired = 0

    def acquire(self) -> None:
        self.acquired += 1


def test_request_acquires_rate_limit_before_each_send(tmp_path: Any) -> None:
    events: list[str] = []

    class Limiter:
        def acquire(self) -> None:
            events.append("acquire")

    def send(method: str, url: str, **kwargs: Any):
        events.append("send")
        return 200, {}, {"rt_cd": "0", "msg_cd": "", "msg1": ""}

    _transport(tmp_path, send, rate_limiter=Limiter()).request(
        method="GET", path="/uapi/x", tr_id="T", idempotent=True
    )
    assert events == ["acquire", "send"]           # 전송 전에 permit 확보


def test_rate_limit_acquired_on_each_retry_attempt(tmp_path: Any) -> None:
    events: list[str] = []
    attempts = {"n": 0}

    class Limiter:
        def acquire(self) -> None:
            events.append("acquire")

    def send(method: str, url: str, **kwargs: Any):
        events.append("send")
        attempts["n"] += 1
        if attempts["n"] < 2:                       # 첫 시도 타임아웃 -> 재시도
            raise TransportTimeout()
        return 200, {}, {"rt_cd": "0", "msg_cd": "", "msg1": ""}

    _transport(tmp_path, send, rate_limiter=Limiter()).request(
        method="GET", path="/uapi/x", tr_id="T", idempotent=True
    )
    assert events == ["acquire", "send", "acquire", "send"]   # 재시도마다 permit


def test_post_timeout_acquires_one_permit_and_is_not_retried(tmp_path: Any) -> None:
    """쓰기(POST)는 타임아웃에 재시도하지 않으므로 리미터도 정확히 1 permit 만 소모한다 --
    리미터가 재시도를 만들지 않음을(무재시도 불변 유지) 확인한다."""
    limiter = _RecordingLimiter()

    def send(method: str, url: str, **kwargs: Any):
        raise TransportTimeout()

    with pytest.raises(TransportTimeout):
        _transport(tmp_path, send, rate_limiter=limiter).request(
            method="POST", path="/uapi/x", tr_id="T", idempotent=False
        )
    assert limiter.acquired == 1              # 1회 시도 = 1 permit(재시도 없음)


def test_no_limiter_by_default_does_not_throttle(tmp_path: Any) -> None:
    # rate_limiter 미지정(기본) 시 그대로 전송(스로틀 없음).
    def send(method: str, url: str, **kwargs: Any):
        return 200, {}, {"rt_cd": "0", "msg_cd": "", "msg1": ""}

    resp = _transport(tmp_path, send).request(
        method="GET", path="/uapi/x", tr_id="T", idempotent=True
    )
    assert resp.rt_cd == "0"


def test_client_builds_rate_limiter_by_default(tmp_path: Any) -> None:
    kis = KISClient(app_key="k", app_secret="s", environment="real")
    assert kis.transport._rate_limiter is not None       # 기본 on


def test_client_throttle_false_disables_rate_limiter(tmp_path: Any) -> None:
    kis = KISClient(app_key="k", app_secret="s", throttle=False)
    assert kis.transport._rate_limiter is None


def test_client_requests_per_second_override(tmp_path: Any) -> None:
    # override 시 그 한도로 리미터를 구성한다(초당 3건 -> 4번째 대기).
    from kis_openapi._ratelimit import SlidingWindowRateLimiter

    kis = KISClient(app_key="k", app_secret="s", requests_per_second=3)
    limiter = kis.transport._rate_limiter
    assert isinstance(limiter, SlidingWindowRateLimiter)
    assert limiter._max_requests == 3 and limiter._window_seconds == pytest.approx(1.0)


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


def test_tr_cont_is_forwarded_to_request_header(tmp_path: Any) -> None:
    captured: list[str] = []

    def send(method: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Mapping[str, Any]]:
        captured.append(kwargs["headers"]["tr_cont"])
        return 200, {"tr_cont": "D"}, {"rt_cd": "0"}

    transport = _transport(tmp_path, send)
    transport.request(method="GET", path="/p", tr_id="TR", idempotent=True)
    transport.request(method="GET", path="/p", tr_id="TR", idempotent=True, tr_cont="N")
    assert captured == ["", "N"]


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


@pytest.mark.parametrize(
    ("method", "idempotent", "expected_attempts"),
    [
        ("GET", True, 4),     # 읽기 + 멱등 = 유일하게 재시도(타임아웃이 무해)
        ("POST", True, 1),    # 쓰기는 멱등 표기와 무관하게 1회만(이중체결 방지)
        ("POST", False, 1),
        ("GET", False, 1),    # 비멱등 GET 도 재시도 금지
    ],
)
def test_only_idempotent_get_is_retried_on_timeout(
    tmp_path: Any, method: str, idempotent: bool, expected_attempts: int
) -> None:
    # 재시도 게이트는 정확히 `method == GET and idempotent`. 이 4조합이 모두 걸려 있어야
    # 게이트를 `and`->`or` 로 바꾸는 회귀(쓰기 재시도 = 이중체결)를 테스트가 잡는다.
    calls = 0

    def send(m: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Mapping[str, Any]]:
        nonlocal calls
        calls += 1
        raise TransportTimeout("uncertain outcome")

    with pytest.raises(TransportTimeout):
        _transport(tmp_path, send, max_attempts=4).request(
            method=method, path="/x", tr_id="TR", idempotent=idempotent,
            body=None if method == "GET" else {"QTY": "1"},
        )
    assert calls == expected_attempts


def test_http_status_error_carries_kis_envelope(tmp_path: Any) -> None:
    # 429 등 에러 응답도 KIS 봉투(rt_cd/msg_cd/raw)를 실어 올려야 호출자가 분기할 수 있다.
    body = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"}

    def send(method: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Mapping[str, Any]]:
        return 429, {}, body

    with pytest.raises(KISRateLimitError) as excinfo:
        _transport(tmp_path, send).request(method="GET", path="/x", tr_id="TR", idempotent=True)
    assert excinfo.value.rt_cd == "1"
    assert excinfo.value.msg_cd == "EGW00201"
    assert excinfo.value.raw == body


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
