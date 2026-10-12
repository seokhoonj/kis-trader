"""kis_trader MCP 서버/핸들러 테스트.

핸들러(:mod:`kis_trader.mcp.handlers`)는 ``mcp`` 없이 테스트한다 -- 가짜 kis 로 공개 API 를 흉내내고,
직렬화가 ``_raw``/자격증명을 새지 않는지, 주문 미리보기가 전송하지 않고 계좌를 마스킹하는지 확인한다.
서버(:mod:`kis_trader.mcp.server`)는 ``mcp`` 가 있을 때만(importorskip) 도구 등록을 확인한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from kis_trader.account import StockAccount
from kis_trader.errors import KISUsageError
from kis_trader.mcp import handlers


@dataclass(frozen=True)
class _FakeQuote:
    symbol: str
    current_price: Decimal
    change_percent: Decimal
    _raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _FakeBalance:
    label: str
    total: Decimal
    _raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _FakePosition:
    symbol: str
    quantity: int
    _raw: dict[str, Any] = field(default_factory=dict)


class _FakeStockAccount(StockAccount):
    """isinstance(.., StockAccount) 를 만족하는 가짜 주식 계좌 뷰 -- product_code 분기와 시장별 뷰
    (domestic/overseas/pension)를 제어한다(super().__init__ 건너뜀)."""

    def __init__(self, *, product_code="01", domestic=None, overseas=None, pension=None):
        self._product_code = product_code
        self._domestic_view = domestic
        self._overseas_view = overseas
        self._pension_view = pension

    @property
    def product_code(self):
        return self._product_code

    @property
    def domestic(self):
        return self._domestic_view

    @property
    def overseas(self):
        return self._overseas_view

    @property
    def pension(self):
        if self._pension_view is None:
            raise KISUsageError("pension 뷰 없음(테스트)")
        return self._pension_view


def _fake_stock_kis(*, product_code="01", environment="paper", account="12345678-01",
                    domestic_balance=None, domestic_positions=None, domestic_open_orders=None,
                    overseas_balance=None, overseas_positions=None, pension_balance=None,
                    after_hours=None, quote=None):
    """활성 주식 계좌(StockAccount)를 든 가짜 kis -- 읽기 디스패치(국내/해외/연금·시간외)를 테스트한다."""
    domestic_view = SimpleNamespace(
        balance=lambda: domestic_balance,
        positions=lambda: domestic_positions or [],
        open_orders=lambda: domestic_open_orders or [],
    )
    overseas_view = SimpleNamespace(
        balance=lambda *, market=None: overseas_balance,
        positions=lambda *, market=None: overseas_positions or [],
    )
    pension_view = SimpleNamespace(balance=lambda: pension_balance)
    account_view = _FakeStockAccount(
        product_code=product_code, domestic=domestic_view, overseas=overseas_view,
        pension=pension_view,
    )
    domestic_ns = SimpleNamespace(
        stock=lambda sym: SimpleNamespace(quote=lambda: quote, after_hours_quote=lambda: after_hours),
    )
    return SimpleNamespace(
        domestic=domestic_ns, account=account_view, environment=environment, _account=account,
    )


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
        search_stocks=lambda query: hits or [],
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


def test_serialize_strips_credential_keys_in_nested_mappings():
    # 방어적 denylist -- 미래에 raw 매핑을 돌려주는 핸들러가 생겨도 자격증명 키는 새지 않는다.
    out = handlers._serialize({
        "symbol": "005930",
        "app_secret": "LEAK1", "access_token": "LEAK2", "approval_key": "LEAK3",
        "Authorization": "LEAK4", "app-key": "LEAK5",
        "nested": {"appsecret": "LEAK6", "ok": "keep"},
    })
    assert out == {"symbol": "005930", "nested": {"ok": "keep"}}
    for leaked in ("LEAK1", "LEAK2", "LEAK3", "LEAK4", "LEAK5", "LEAK6"):
        assert leaked not in str(out)


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


def test_serialize_recurses_mapping_proxy_from_frozen_body():
    """_serialize 는 ``MappingProxyType`` (freeze_vendor_payload 결과) 도 매핑으로 재귀 처리한다 --
    ``dict`` 만 보던 분기는 proxy 를 그대로 반환해 ``json.dumps`` 가 Decimal/proxy 에서 깨졌다."""
    from types import MappingProxyType

    frozen = MappingProxyType({"price": Decimal("10.5"), "rows": (MappingProxyType({"_raw": 1, "v": 2}),)})
    out = handlers._serialize(frozen)
    assert out == {"price": "10.5", "rows": [{"v": 2}]}  # proxy 재귀 + Decimal 문자열화 + 밑줄 키 제거
    import json
    json.dumps(out)  # 직렬화가 깨지지 않아야 한다


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


@pytest.mark.parametrize("bad", [-1, 0, True, "2"])
@pytest.mark.parametrize("fn", ["search", "ranking_change", "positions", "open_orders"])
def test_list_handlers_reject_bad_limit(fn, bad):
    # limit 검증은 API 호출 전에 돈다 -- bool(int 서브클래스)·비int·1 미만은 조용히 오답(음수는
    # [:limit] 가 마지막 행만 빼고 반환)하지 않고 KISUsageError 로 거부한다.
    from kis_trader.errors import KISUsageError
    kis = _fake_kis(hits=[], ranking=[])
    call = {
        "search": lambda: handlers.search(kis, "x", limit=bad),
        "ranking_change": lambda: handlers.ranking_change(kis, direction="gainers", limit=bad),
        "positions": lambda: handlers.positions(kis, limit=bad),
        "open_orders": lambda: handlers.open_orders(kis, limit=bad),
    }[fn]
    with pytest.raises(KISUsageError):
        call()


def test_order_preview_not_sent_and_masks_account():
    kis = _fake_kis(environment="real", account="12345678-01")
    out = handlers.order_preview(kis, frozenset(), symbol="005930", side="buy", quantity=10, limit_price="70000")
    assert out["sent"] is False
    assert out["environment"] == "real"
    assert out["account"] == "****7801"          # 마지막 4자리만
    assert "1234567801" not in str(out)          # 전체 계좌번호 비노출
    assert out["order_type"] == "limit"
    assert out["symbol"] == "005930" and out["side"] == "buy" and out["quantity"] == 10


def test_order_preview_market_when_no_price():
    out = handlers.order_preview(_fake_kis(), frozenset(), symbol="005930", side="sell", quantity=1)
    assert out["order_type"] == "market" and out["limit_price"] is None


@pytest.mark.parametrize("bad", [
    {"side": "hold", "quantity": 1},
    {"side": "buy", "quantity": 0},
    {"side": "buy", "quantity": -5},
])
def test_order_preview_rejects_bad_input(bad):
    from kis_trader.errors import KISUsageError
    with pytest.raises(KISUsageError):
        handlers.order_preview(_fake_kis(), frozenset(), symbol="005930", **bad)


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


# --- 계좌종류-인식 읽기 디스패치 ---------------------------------------------


def test_balance_domestic_product_01_uses_domestic_view():
    kis = _fake_stock_kis(product_code="01", domestic_balance=_FakeBalance("domestic", Decimal(100)))
    out = handlers.balance(kis, region="domestic", active_profile="general")
    assert out == {"label": "domestic", "total": "100"}       # C8: domestic.balance() shape


def test_balance_domestic_product_22_stays_domestic_not_pension():
    # 연금저축(22)은 IRP 가 아니다 -- 국내주식 잔고 경로를 타야 한다(스펙의 pension 전제는 틀렸다).
    kis = _fake_stock_kis(product_code="22", domestic_balance=_FakeBalance("dom22", Decimal(5)),
                          pension_balance=SimpleNamespace(positions=()))
    out = handlers.balance(kis, region="domestic", active_profile="pension")
    assert out == {"label": "dom22", "total": "5"}


def test_balance_domestic_product_29_uses_pension_lens():
    kis = _fake_stock_kis(product_code="29", pension_balance=_FakeBalance("pension", Decimal(7)),
                          domestic_balance=_FakeBalance("SHOULD_NOT", Decimal(0)))
    out = handlers.balance(kis, region="domestic", active_profile="irp")
    assert out == {"label": "pension", "total": "7"}


def test_positions_product_29_from_pension_balance():
    pos = _FakePosition("005930", 3, _raw={"secret": "x"})
    kis = _fake_stock_kis(product_code="29",
                          pension_balance=SimpleNamespace(positions=(pos,)))
    out = handlers.positions(kis, region="domestic", active_profile="irp")
    assert out == [{"symbol": "005930", "quantity": 3}]
    assert "secret" not in str(out)


def test_positions_product_01_from_domestic_view():
    kis = _fake_stock_kis(product_code="01", domestic_positions=[_FakePosition("000660", 2)])
    out = handlers.positions(kis, region="domestic", active_profile="general")
    assert out == [{"symbol": "000660", "quantity": 2}]


def test_overseas_balance_general_real_routes_to_overseas():
    kis = _fake_stock_kis(product_code="01", environment="real",
                          overseas_balance=[_FakeBalance("US", Decimal(42), _raw={"app_secret": "L"})])
    out = handlers.balance(kis, region="overseas", market=None, active_profile="general")
    assert out == [{"label": "US", "total": "42"}]
    assert "app_secret" not in str(out) and "L" not in str(out)   # 방화벽 유지


def test_overseas_positions_general_real_routes_to_overseas():
    kis = _fake_stock_kis(product_code="01", environment="real",
                          overseas_positions=[_FakePosition("AAPL", 1)])
    out = handlers.positions(kis, region="overseas", market="US", active_profile="general")
    assert out == [{"symbol": "AAPL", "quantity": 1}]


def test_overseas_read_rejected_on_paper_mcp_policy():
    kis = _fake_stock_kis(product_code="01", environment="paper")
    with pytest.raises(KISUsageError, match="MCP 정책"):
        handlers.balance(kis, region="overseas", active_profile="general")


@pytest.mark.parametrize("profile", ["isa", "pension", "irp"])
def test_overseas_read_rejected_for_non_general_profile(profile):
    kis = _fake_stock_kis(product_code="01", environment="real")
    with pytest.raises(KISUsageError, match="general"):
        handlers.balance(kis, region="overseas", active_profile=profile)


def test_open_orders_product_01_ok_but_22_and_29_refused():
    ok = _fake_stock_kis(product_code="01", domestic_open_orders=[_FakePosition("005930", 1)])
    assert handlers.open_orders(ok) == [{"symbol": "005930", "quantity": 1}]
    for product in ("22", "29"):
        with pytest.raises(KISUsageError, match="위탁"):
            handlers.open_orders(_fake_stock_kis(product_code=product))


def test_quote_after_hours_routes_to_after_hours_quote():
    ah = _FakeQuote("005930", Decimal(71000), Decimal("0.1"), _raw={"secret": "x"})
    kis = _fake_stock_kis(product_code="01", after_hours=ah)
    out = handlers.quote(kis, "005930", region="domestic", session="after_hours")
    assert out["symbol"] == "005930" and out["current_price"] == "71000"
    assert "secret" not in str(out)


def test_quote_after_hours_overseas_refused_region_wins():
    kis = _fake_stock_kis(product_code="01")
    with pytest.raises(KISUsageError, match="국내"):
        handlers.quote(kis, "AAPL", region="overseas", session="after_hours")


def test_quote_rejects_bad_session():
    with pytest.raises(KISUsageError, match="session"):
        handlers.quote(_fake_stock_kis(), "005930", session="premarket")


# --- 서버(도구 등록) -- mcp 있을 때만 -----------------------------------------


def _build_test_server(kis=None, *, environment="paper", profile="general", allowlist=None,
                       capped_currencies=frozenset()):
    from kis_trader.mcp._guardrails import CircuitBreaker
    from kis_trader.mcp._session import McpSession, _StartupAuthorization
    from kis_trader.mcp.server import build_server

    kis = kis if kis is not None else _fake_kis(environment=environment)
    session = McpSession(profile=profile, environment=environment, startup_client=kis,
                         auth=_StartupAuthorization(allow_real=False, confirmed=False),
                         build=lambda p, e: kis)
    return build_server(session, allowlist=allowlist, breaker=CircuitBreaker(),
                        capped_currencies=capped_currencies)


def test_server_registers_read_switch_and_guarded_order_tools():
    pytest.importorskip("mcp")
    import asyncio

    server = _build_test_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    # 정확집합(==)으로 못박는다: subset(<=) 이면 의도치 않은 툴이 하나 더 등록돼도 통과해버린다.
    # 노출 표면이 늘면 이 테스트가 깨져 의식적 결정을 강제한다(실주문 안전 표면의 핵심 불변식).
    assert names == {"quote", "search", "ranking_change", "balance", "positions", "open_orders",
                     "reconcile",
                     "use_environment", "use_profile", "current_account",
                     "order_preview", "place_order", "cancel_order", "modify_order"}
    # 원시 매매 메서드(buy/sell/credit)는 직접 노출하지 않는다 -- 가드레일 통과하는 place_order 만.
    assert not (names & {"buy", "sell", "credit_buy", "credit_sell"})


def test_order_tool_input_schemas_pin_switch_region_shape():
    # 전환+해외 확장 스키마를 고정: place_order/order_preview 는 region(domestic/overseas)·exchange 를
    # 노출하고 division/side 를 유지하며 side enum 은 buy/sell 로 좁혀져 있다. quote 는 session(regular/
    # after_hours), balance/positions 는 region/market 을 노출한다.
    pytest.importorskip("mcp")
    import asyncio

    server = _build_test_server()
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    # 주문 4도구는 정확집합(==)으로 못박는다: subset(<=) 이면 taint 운반 파라미터(예: account, dict 인자)가
    # 주문 도구에 하나 더 붙어도 통과해버린다. 읽기 도구는 <= 로 둬(무해한 읽기 파라미터 추가에 안 깨짐).
    _ORDER_PARAMS = {"symbol", "side", "quantity", "limit_price", "division", "region", "exchange"}
    place = tools["place_order"].input_schema["properties"]
    assert set(place) == _ORDER_PARAMS
    assert place["side"]["enum"] == ["buy", "sell"]            # Side 리터럴이 넓어지지 않게
    assert place["region"]["enum"] == ["domestic", "overseas"]
    preview = tools["order_preview"].input_schema["properties"]
    assert set(preview) == _ORDER_PARAMS                       # dry-run 쌍둥이와 형상 일치
    assert preview["region"]["enum"] == ["domestic", "overseas"]
    assert set(tools["cancel_order"].input_schema["properties"]) == {"client_order_id"}
    assert set(tools["modify_order"].input_schema["properties"]) == {
        "client_order_id", "limit_price", "quantity"}
    # use_profile 파라미터는 SwitchableProfile Literal 에서 파생한 enum 을 싣는다(스키마<->런타임 가드 단일 출처).
    assert tools["use_profile"].input_schema["properties"]["profile"]["enum"] == [
        "general", "isa", "pension", "irp"]
    q = tools["quote"].input_schema["properties"]
    assert {"region", "session"} <= set(q)
    assert q["session"]["enum"] == ["regular", "after_hours"]
    for read_tool in ("balance", "positions"):
        props = tools[read_tool].input_schema["properties"]
        assert {"region", "market"} <= set(props)


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


def _isolate_config(monkeypatch, tmp_path):
    """실 사용자의 ~/.config 를 읽지 않도록 XDG 를 임시 경로로 돌리고 KIS_* 를 지운다."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for var in [name for name in os.environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)


def test_build_session_warns_on_legacy_store_but_not_v2(monkeypatch, tmp_path, caplog):
    """레거시(스키마 마커 없는) 저장소로 기동하면 런타임 전환 경계를 한 줄 경고로 알리고, schema-2
    저장소로 기동하면 경고하지 않는다. 어느 쪽도 기동을 거부하지는 않는다(레거시도 계속 동작)."""
    pytest.importorskip("mcp")
    import logging

    from kis_trader import KISConfig
    from kis_trader.mcp.server import _build_session

    _isolate_config(monkeypatch, tmp_path)
    config_dir = tmp_path / "config" / "kis-trader"
    config_dir.mkdir(parents=True)

    # 레거시: 스키마 마커 없이 프로필 섹션만.
    (config_dir / "credentials.json").write_text(
        json.dumps({"main": {"app_key": "k", "app_secret": "s", "account": "12345678-01"}}),
        encoding="utf-8",
    )
    environ = {"KIS_MCP_PROFILE": "main"}
    with caplog.at_level(logging.WARNING, logger="kis_trader.mcp"):
        _build_session(environ, "paper", None)
    assert any("legacy" in rec.message and "kis config migrate" in rec.message
               for rec in caplog.records)

    # v2(schema 2): 같은 프로필을 두 축 저장소로 다시 쓴다.
    caplog.clear()
    (config_dir / "credentials.json").unlink()
    KISConfig(profile="main", app_key="k", app_secret="s",
              account="12345678-01", config_dir=config_dir).save()
    with caplog.at_level(logging.WARNING, logger="kis_trader.mcp"):
        _build_session(environ, "paper", None)
    assert not caplog.records


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


def test_breaker_from_env_honors_explicit_zero_and_value():
    # 명시적 KIS_MCP_MAX_REAL_ORDERS=0 은 조용히 기본 10 이 되면 안 된다 -- 값으로 분기(falsy)하면
    # 사용자가 둔 가장 엄격한 cap 이 사라져 실주문 10건이 허용된다. 설정 여부로 분기해 명시값을 그대로
    # CircuitBreaker 에 넘긴다(0/음수는 거기서 "양의 정수여야 한다"로 fail-closed).
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import _breaker_from_env

    with pytest.raises(KISUsageError):
        _breaker_from_env({"KIS_MCP_MAX_REAL_ORDERS": "0"})
    # 빈 문자열(명시적으로 설정은 됐으나 값이 빔)도 조용히 기본 10 이 되면 안 된다 -- fail-closed.
    with pytest.raises(KISUsageError):
        _breaker_from_env({"KIS_MCP_MAX_REAL_ORDERS": ""})
    assert _breaker_from_env({"KIS_MCP_MAX_REAL_ORDERS": "3"})._max == 3
    assert _breaker_from_env({})._max == 10
