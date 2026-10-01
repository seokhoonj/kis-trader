"""kis_trader MCP 서버/핸들러 테스트.

핸들러(:mod:`kis_trader.mcp.handlers`)는 ``mcp`` 없이 테스트한다 -- 가짜 kis 로 공개 API 를 흉내내고,
직렬화가 ``_raw``/자격증명을 새지 않는지, 주문 미리보기가 전송하지 않고 계좌를 마스킹하는지 확인한다.
서버(:mod:`kis_trader.mcp.server`)는 ``mcp`` 가 있을 때만(importorskip) 도구 등록을 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from kis_trader.mcp import handlers


@dataclass(frozen=True)
class _FakeQuote:
    symbol: str
    current_price: Decimal
    change_percent: Decimal
    _raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _FakeHit:
    symbol: str
    name: str
    _raw: dict[str, Any] = field(default_factory=dict)


def _fake_kis(*, quote=None, hits=None, ranking=None, balance=None,
              positions=None, open_orders=None, reconcile_report="__unset__",
              environment="paper", account="12345678-01"):
    ranking_ns = SimpleNamespace(by_change=lambda direction="gainers": ranking or [])
    domestic = SimpleNamespace(
        stock=lambda sym: SimpleNamespace(quote=lambda: quote),
        search=lambda query: hits or [],
        ranking=ranking_ns,
    )
    account_ns = SimpleNamespace(
        balance=lambda: balance,
        domestic=SimpleNamespace(
            positions=lambda: positions or [],
            open_orders=lambda: open_orders or [],
        ),
    )
    orders_ns = SimpleNamespace(
        reconcile=lambda cid: (None if reconcile_report == "__unset__" else reconcile_report),
    )
    return SimpleNamespace(
        domestic=domestic, account=account_ns, orders=orders_ns,
        environment=environment, _account=account,
    )


# --- 직렬화 방화벽 -----------------------------------------------------------


def test_serialize_drops_raw_and_stringifies_decimal():
    q = _FakeQuote("005930", Decimal(71500), Decimal("0.14"),
                   _raw={"app_secret": "SHOULD_NOT_LEAK", "stac_yn": "Y"})
    out = handlers._serialize(q)
    assert out == {"symbol": "005930", "current_price": "71500", "change_percent": "0.14"}
    assert "_raw" not in out
    assert "SHOULD_NOT_LEAK" not in str(out)


def test_serialize_recurses_lists_and_nested():
    out = handlers._serialize([_FakeHit("005930", "삼성전자", _raw={"x": 1})])
    assert out == [{"symbol": "005930", "name": "삼성전자"}]


# --- 핸들러 -----------------------------------------------------------------


def test_quote_handler_no_raw():
    kis = _fake_kis(quote=_FakeQuote("005930", Decimal(71500), Decimal("0.14"),
                                     _raw={"secret": "x"}))
    out = handlers.quote(kis, "005930")
    assert out["symbol"] == "005930"
    assert out["current_price"] == "71500"
    assert "_raw" not in out and "secret" not in str(out)


def test_serialize_strips_underscore_keys_from_nested_mappings():
    """_serialize 는 dataclass 뿐 아니라 중첩 dict 에서도 밑줄 키(_raw/내부키)를 떼어 낸다(누출 방지)."""
    out = handlers._serialize(
        {"ok": 1, "_raw": {"app_secret": "LEAK"}, "nested": {"_internal": 2, "keep": 3}}
    )
    assert out == {"ok": 1, "nested": {"keep": 3}}
    assert "LEAK" not in str(out)


def test_search_handler_caps_and_serializes():
    hits = [_FakeHit(f"{i:06d}", f"n{i}") for i in range(30)]
    out = handlers.search(kis := _fake_kis(hits=hits), "삼성", limit=5)
    assert len(out) == 5
    assert out[0] == {"symbol": "000000", "name": "n0"}
    assert kis is not None


def test_ranking_handler():
    rows = [_FakeHit("005930", "삼성전자"), _FakeHit("000660", "SK하이닉스")]
    out = handlers.ranking_change(_fake_kis(ranking=rows), direction="gainers", limit=10)
    assert [r["symbol"] for r in out] == ["005930", "000660"]


def test_order_preview_not_sent_and_masks_account():
    kis = _fake_kis(environment="real", account="12345678-01")
    out = handlers.order_preview(kis, symbol="005930", side="buy", quantity=10, limit_price="70000")
    assert out["sent"] is False
    assert out["environment"] == "real"
    assert out["account"] == "****7801"          # 마지막 4자리만
    assert "1234567801" not in str(out)          # 전체 계좌번호 비노출
    assert out["order_type"] == "limit"
    assert out["symbol"] == "005930" and out["side"] == "buy" and out["quantity"] == 10


def test_order_preview_market_when_no_price():
    out = handlers.order_preview(_fake_kis(), symbol="005930", side="sell", quantity=1)
    assert out["order_type"] == "market" and out["limit_price"] is None


@pytest.mark.parametrize("bad", [
    {"side": "hold", "quantity": 1},
    {"side": "buy", "quantity": 0},
    {"side": "buy", "quantity": -5},
])
def test_order_preview_rejects_bad_input(bad):
    from kis_trader.errors import KISUsageError
    with pytest.raises(KISUsageError):
        handlers.order_preview(_fake_kis(), symbol="005930", **bad)


def test_reconcile_unresolved_returns_in_flight():
    out = handlers.reconcile(_fake_kis(), client_order_id="cid-1")
    assert out == {"client_order_id": "cid-1", "resolved": False, "report": None}


def test_reconcile_resolved_serializes_report():
    report = _FakeHit("005930", "done", _raw={"secret": "x"})
    out = handlers.reconcile(_fake_kis(reconcile_report=report), client_order_id="cid-2")
    assert out["resolved"] is True
    assert out["report"] == {"symbol": "005930", "name": "done"}
    assert "secret" not in str(out)


def test_account_tools_reject_non_stock_account():
    """잔고/보유/미체결 도구는 주식 계좌 전용 -- 다른 계좌 뷰면 명확히 거부."""
    from kis_trader.errors import KISUsageError
    kis = _fake_kis()  # account 가 StockAccount 가 아님(파생/기타 계좌 뷰 흉내)
    for fn in (handlers.balance, handlers.positions, handlers.open_orders):
        with pytest.raises(KISUsageError):
            fn(kis)


def test_ranking_rejects_bad_direction():
    from kis_trader.errors import KISUsageError
    with pytest.raises(KISUsageError):
        handlers.ranking_change(_fake_kis(), direction="sideways")


# --- 서버(도구 등록) -- mcp 있을 때만 -----------------------------------------


def test_server_registers_read_and_guarded_order_tools():
    pytest.importorskip("mcp")
    import asyncio

    from kis_trader.mcp._guardrails import CircuitBreaker, RealOrderGate
    from kis_trader.mcp.server import build_server

    server = build_server(_fake_kis(), gate=RealOrderGate.from_env({}, "paper"),
                          allowlist=None, breaker=CircuitBreaker())
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {"quote", "search", "ranking_change", "balance", "positions", "open_orders",
            "order_preview", "reconcile",
            "place_order", "cancel_order", "modify_order"} <= names
    # 원시 매매 메서드(buy/sell/credit)는 직접 노출하지 않는다 -- 가드레일 통과하는 place_order 만.
    assert not (names & {"buy", "sell", "credit_buy", "credit_sell"})


def test_build_client_missing_credentials_gives_clear_error(monkeypatch):
    """자격증명이 없으면 raw KeyError 가 아니라 안내 메시지를 담은 KISUsageError 를 올린다."""
    pytest.importorskip("mcp")
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import build_client
    for k in ("KIS_MCP_PROFILE", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(KISUsageError) as exc:
        build_client()
    msg = str(exc.value)
    assert "자격증명" in msg and "KIS_APP_KEY" in msg


def test_resolve_transport_defaults_to_stdio():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import _resolve_transport
    assert _resolve_transport({}) == ("stdio", {})


def test_resolve_transport_sse_local_passes_host_port():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import _resolve_transport
    transport, kw = _resolve_transport({"KIS_MCP_TRANSPORT": "sse", "KIS_MCP_PORT": "9001"})
    assert transport == "sse"
    assert kw == {"host": "127.0.0.1", "port": 9001}


def test_resolve_transport_rejects_nonlocal_bind():
    """돈이 오가는 서버 -- 비-로컬 bind 는 fail-closed(조용히 열어 주지 않음)."""
    pytest.importorskip("mcp")
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import _resolve_transport
    with pytest.raises(KISUsageError):
        _resolve_transport({"KIS_MCP_TRANSPORT": "streamable-http", "KIS_MCP_HOST": "0.0.0.0"})


def test_resolve_transport_rejects_unknown_transport():
    pytest.importorskip("mcp")
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import _resolve_transport
    with pytest.raises(KISUsageError):
        _resolve_transport({"KIS_MCP_TRANSPORT": "carrier-pigeon"})


def test_resolve_transport_allows_nonlocal_only_with_token():
    """비-로컬 bind 는 액세스 토큰이 있을 때만 허용(인증 없는 공개 노출 방지)."""
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import _resolve_transport
    transport, kw = _resolve_transport({
        "KIS_MCP_TRANSPORT": "streamable-http", "KIS_MCP_HOST": "0.0.0.0",
        "KIS_MCP_ACCESS_TOKEN": "s3cret",
    })
    assert transport == "streamable-http"
    assert kw == {"host": "0.0.0.0", "port": 8000}


# --- bearer 토큰 미들웨어(HTTP 전송 방어심층) ---------------------------------


def _drive_bearer(headers, token="s3cret"):
    """BearerTokenMiddleware 를 http scope 로 한 번 구동하고 (status, inner_호출여부)."""
    import asyncio

    from kis_trader.mcp._http_auth import BearerTokenMiddleware
    inner_called = {"v": False}

    async def inner(scope, receive, send):
        inner_called["v"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent: list = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    mw = BearerTokenMiddleware(inner, token)
    asyncio.run(mw({"type": "http", "headers": headers}, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, inner_called["v"]


def test_bearer_middleware_rejects_missing_or_wrong_token():
    pytest.importorskip("mcp")
    assert _drive_bearer([]) == (401, False)
    assert _drive_bearer([(b"authorization", b"Bearer nope")]) == (401, False)
    assert _drive_bearer([(b"authorization", b"Basic s3cret")]) == (401, False)


def test_bearer_middleware_passes_correct_token():
    pytest.importorskip("mcp")
    assert _drive_bearer([(b"authorization", b"Bearer s3cret")]) == (200, True)


def test_bearer_middleware_passes_through_non_http_scope():
    """lifespan 등 비-HTTP scope 는 토큰 검사 없이 통과."""
    pytest.importorskip("mcp")
    import asyncio

    from kis_trader.mcp._http_auth import BearerTokenMiddleware
    seen = {"v": False}

    async def inner(scope, receive, send):
        seen["v"] = True

    async def send(msg):
        ...

    async def receive():
        return {}

    asyncio.run(BearerTokenMiddleware(inner, "s3cret")({"type": "lifespan"}, receive, send))
    assert seen["v"] is True


def test_bearer_middleware_denies_websocket_scope():
    """deny-by-default -- websocket scope 는 인증 없이 통과시키지 않고 닫는다(미인증 경로 차단)."""
    pytest.importorskip("mcp")
    import asyncio

    from kis_trader.mcp._http_auth import BearerTokenMiddleware
    inner_called = {"v": False}

    async def inner(scope, receive, send):
        inner_called["v"] = True

    sent: list = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {}

    asyncio.run(BearerTokenMiddleware(inner, "s3cret")(
        {"type": "websocket", "headers": []}, receive, send))
    assert inner_called["v"] is False
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_bearer_middleware_rejects_duplicate_auth_headers():
    """같은 Authorization 헤더가 둘이면 거부(헤더 스머글링 방지)."""
    pytest.importorskip("mcp")
    dup = [(b"authorization", b"Bearer s3cret"), (b"authorization", b"Bearer s3cret")]
    assert _drive_bearer(dup) == (401, False)


def test_bearer_middleware_non_ascii_token_no_500():
    """비-ASCII 토큰/헤더도 예외(500) 없이 bytes 상수시간 비교 -- 일치 통과, 불일치 401."""
    pytest.importorskip("mcp")
    assert _drive_bearer([(b"authorization", "Bearer 비밀".encode())], token="비밀") == (200, True)
    assert _drive_bearer([(b"authorization", b"Bearer \x80\x81")], token="s3cret") == (401, False)


def test_bearer_middleware_reject_payload_shape():
    """401 응답은 www-authenticate 헤더와 JSON 본문을 갖는다."""
    pytest.importorskip("mcp")
    import asyncio

    from kis_trader.mcp._http_auth import BearerTokenMiddleware
    sent: list = []

    async def inner(scope, receive, send):
        ...

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    asyncio.run(BearerTokenMiddleware(inner, "s3cret")({"type": "http", "headers": []}, receive, send))
    assert sent[0]["status"] == 401
    assert (b"www-authenticate", b"Bearer") in sent[0]["headers"]
    assert sent[1]["body"] == b'{"error":"unauthorized"}'


def test_main_missing_credentials_exits_cleanly_without_traceback(monkeypatch, capsys):
    """진입점 main() 은 자격증명이 없을 때 raw 트레이스백이 아니라 한 줄 안내 + SystemExit(1)."""
    pytest.importorskip("mcp")
    from kis_trader.mcp import server
    for k in ("KIS_MCP_PROFILE", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KIS_MCP_ENVIRONMENT", "paper")
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("kis-mcp:") and "자격증명" in err
    assert "Traceback" not in err and "KeyError" not in err


def test_resolve_transport_rejects_non_numeric_port():
    """숫자가 아닌 KIS_MCP_PORT 는 raw ValueError 가 아니라 KISUsageError(깔끔한 종료)."""
    pytest.importorskip("mcp")
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import _resolve_transport
    with pytest.raises(KISUsageError):
        _resolve_transport({"KIS_MCP_TRANSPORT": "sse", "KIS_MCP_PORT": "abc"})
