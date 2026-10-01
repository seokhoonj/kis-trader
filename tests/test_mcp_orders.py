"""MCP 실주문 가드레일 -- kis_trader.mcp._guardrails (이중게이트·allowlist·서킷브레이커·티켓 빌드).

전부 fail-closed: 조건 미충족이면 와이어 전에 KISUsageError. 모의(paper)는 자유, 실전(real)만 게이트.
"""

from __future__ import annotations

import pytest

from kis_trader.errors import KISUsageError
from kis_trader.mcp._guardrails import (
    CircuitBreaker,
    RealOrderGate,
    StockOrderPlan,
    build_stock_order,
    check_allowlist,
)
from kis_trader.mcp.handlers import plan_cancel, plan_modify, plan_place_order


# --- RealOrderGate (이중게이트) -------------------------------------------
def test_paper_gate_always_executable():
    gate = RealOrderGate.from_env({}, "paper")
    gate.require_executable()                 # 모의는 플래그 없어도 통과
    assert gate.is_real() is False


def test_real_gate_without_flags_rejected():
    gate = RealOrderGate.from_env({}, "real")
    with pytest.raises(KISUsageError, match="KIS_MCP_ALLOW_REAL"):
        gate.require_executable()


def test_real_gate_allow_only_needs_confirm():
    gate = RealOrderGate.from_env({"KIS_MCP_ALLOW_REAL": "1"}, "real")
    with pytest.raises(KISUsageError, match="KIS_MCP_REAL_CONFIRM"):
        gate.require_executable()


def test_real_gate_both_flags_executable():
    gate = RealOrderGate.from_env(
        {"KIS_MCP_ALLOW_REAL": "true", "KIS_MCP_REAL_CONFIRM": "i-understand-real-money"}, "real"
    )
    gate.require_executable()
    assert gate.is_real() is True


def test_real_gate_wrong_confirm_phrase_rejected():
    gate = RealOrderGate.from_env(
        {"KIS_MCP_ALLOW_REAL": "1", "KIS_MCP_REAL_CONFIRM": "yes"}, "real"
    )
    with pytest.raises(KISUsageError, match="KIS_MCP_REAL_CONFIRM"):
        gate.require_executable()


# --- allowlist -----------------------------------------------------------
def test_allowlist_paper_ignored():
    check_allowlist("005930", None, is_real=False)   # 모의는 allowlist 무관


def test_allowlist_real_none_rejected():
    with pytest.raises(KISUsageError, match="allowlist"):
        check_allowlist("005930", None, is_real=True)


def test_allowlist_real_empty_rejected():
    with pytest.raises(KISUsageError, match="allowlist"):
        check_allowlist("005930", frozenset(), is_real=True)


def test_allowlist_real_not_member_rejected():
    with pytest.raises(KISUsageError):
        check_allowlist("000660", frozenset({"005930"}), is_real=True)


def test_allowlist_real_member_ok():
    check_allowlist("005930", frozenset({"005930", "000660"}), is_real=True)


# --- CircuitBreaker -------------------------------------------------------
def test_circuit_breaker_under_limit_ok():
    cb = CircuitBreaker(max_real_orders=2)
    cb.record_and_check()
    cb.record_and_check()
    assert cb.halted is False


def test_circuit_breaker_trips_over_limit():
    cb = CircuitBreaker(max_real_orders=2)
    cb.record_and_check()
    cb.record_and_check()
    with pytest.raises(KISUsageError, match="HALT"):
        cb.record_and_check()
    assert cb.halted is True
    with pytest.raises(KISUsageError, match="HALT"):     # 이미 HALT 면 즉시 거부
        cb.record_and_check()


def test_circuit_breaker_reset_reenables():
    cb = CircuitBreaker(max_real_orders=1)
    cb.record_and_check()
    with pytest.raises(KISUsageError):
        cb.record_and_check()
    cb.reset()
    cb.record_and_check()                     # 수동 재개 후 다시 가능
    assert cb.halted is False


def test_circuit_breaker_bad_limit_rejected():
    with pytest.raises(KISUsageError):
        CircuitBreaker(max_real_orders=0)
    with pytest.raises(KISUsageError):
        CircuitBreaker(max_real_orders=True)   # bool 거부


# --- build_stock_order (taint 경계) --------------------------------------
def test_build_domestic_limit_order():
    plan = build_stock_order(venue="domestic", symbol="005930", side="buy", quantity=10,
                             limit_price="70000")
    assert plan == StockOrderPlan(venue="domestic", symbol="005930", side="buy",
                                  order_type="limit", quantity=10, limit_price="70000")


def test_build_domestic_market_order():
    plan = build_stock_order(venue="domestic", symbol="005930", side="sell", quantity=1)
    assert plan.order_type == "market" and plan.limit_price is None


def test_build_overseas_requires_limit():
    # 해외는 지정가만 -- 시장가(limit 없음) 거부
    with pytest.raises(KISUsageError, match="지정가"):
        build_stock_order(venue="overseas", symbol="AAPL", side="buy", quantity=10)
    plan = build_stock_order(venue="overseas", symbol="AAPL", side="buy", quantity=10,
                             limit_price="150")
    assert plan.order_type == "limit"


def test_build_rejects_bad_inputs():
    with pytest.raises(KISUsageError):
        build_stock_order(venue="crypto", symbol="X", side="buy", quantity=1)
    with pytest.raises(KISUsageError):
        build_stock_order(venue="domestic", symbol="005930", side="hold", quantity=1)
    with pytest.raises(KISUsageError):
        build_stock_order(venue="domestic", symbol="005930", side="buy", quantity=0)
    with pytest.raises(KISUsageError):
        build_stock_order(venue="domestic", symbol="005930", side="buy", quantity=True)  # bool


def test_build_rejects_nonscalar_symbol():
    # taint 경계: 읽기 도구 dict/객체를 symbol 로 넘기면 거부(스칼라만)
    with pytest.raises(KISUsageError):
        build_stock_order(venue="domestic", symbol={"code": "005930"},  # type: ignore[arg-type]
                          side="buy", quantity=1, limit_price="100")


# --- plan handlers (가드레일 합성, 와이어 전) ------------------------------
_PAPER = RealOrderGate.from_env({}, "paper")
_REAL = RealOrderGate.from_env(
    {"KIS_MCP_ALLOW_REAL": "1", "KIS_MCP_REAL_CONFIRM": "i-understand-real-money"}, "real"
)


def test_plan_place_paper_no_caps_needed():
    plan = plan_place_order(_PAPER, None, has_risk=False, venue="domestic",
                            symbol="005930", side="buy", quantity=1)
    assert plan.symbol == "005930" and plan.order_type == "market"


def test_plan_place_real_requires_risk():
    with pytest.raises(KISUsageError, match="RiskLimits"):
        plan_place_order(_REAL, frozenset({"005930"}), has_risk=False, venue="domestic",
                         symbol="005930", side="buy", quantity=1, limit_price="70000")


def test_plan_place_real_requires_allowlist():
    with pytest.raises(KISUsageError, match="allowlist"):
        plan_place_order(_REAL, None, has_risk=True, venue="domestic",
                         symbol="005930", side="buy", quantity=1, limit_price="70000")


def test_plan_place_real_gate_closed():
    gate = RealOrderGate.from_env({}, "real")          # 이중게이트 미설정
    with pytest.raises(KISUsageError, match="KIS_MCP_ALLOW_REAL"):
        plan_place_order(gate, frozenset({"005930"}), has_risk=True, venue="domestic",
                         symbol="005930", side="buy", quantity=1, limit_price="70000")


def test_plan_place_real_all_pass():
    plan = plan_place_order(_REAL, frozenset({"005930"}), has_risk=True, venue="domestic",
                            symbol="005930", side="buy", quantity=10, limit_price="70000")
    assert plan == StockOrderPlan(venue="domestic", symbol="005930", side="buy",
                                  order_type="limit", quantity=10, limit_price="70000")


def test_plan_cancel_gate_and_id():
    assert plan_cancel(_PAPER, client_order_id="20260101-abc") == "20260101-abc"
    with pytest.raises(KISUsageError):
        plan_cancel(_PAPER, client_order_id="")
    gate = RealOrderGate.from_env({}, "real")
    with pytest.raises(KISUsageError):
        plan_cancel(gate, client_order_id="x")          # 실전 게이트 닫힘


def test_plan_modify_real_requires_risk():
    with pytest.raises(KISUsageError, match="RiskLimits"):
        plan_modify(_REAL, has_risk=False, client_order_id="x", limit_price="100")
    out = plan_modify(_REAL, has_risk=True, client_order_id="x", limit_price="100", quantity=5)
    assert out == {"client_order_id": "x", "limit_price": "100", "quantity": 5}
