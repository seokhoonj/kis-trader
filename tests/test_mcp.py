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
    with pytest.raises(ValueError):
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
