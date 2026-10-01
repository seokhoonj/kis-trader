"""MCP 실주문 가드레일 -- kis_trader.mcp._guardrails (이중게이트·allowlist·서킷브레이커·티켓 빌드).

전부 fail-closed: 조건 미충족이면 와이어 전에 KISUsageError. 모의(paper)는 자유, 실전(real)만 게이트.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from kis_trader import KISClient
from kis_trader.errors import KISUsageError
from kis_trader.mcp._guardrails import (
    CircuitBreaker,
    ModifyOrderPlan,
    RealOrderGate,
    StockOrderPlan,
    check_allowlist,
    make_stock_order_plan,
)
from kis_trader.mcp.handlers import plan_cancel_order, plan_modify_order, plan_place_order
from kis_trader.risk import RiskLimits
from kis_trader.transport import RawResponse


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
    plan = make_stock_order_plan(venue="domestic", symbol="005930", side="buy", quantity=10,
                             limit_price="70000")
    assert plan == StockOrderPlan(venue="domestic", symbol="005930", side="buy",
                                  order_type="limit", quantity=10, limit_price="70000")


def test_build_domestic_market_order():
    plan = make_stock_order_plan(venue="domestic", symbol="005930", side="sell", quantity=1)
    assert plan.order_type == "market" and plan.limit_price is None


def test_build_rejects_overseas():
    # MVP 는 국내만 -- 해외는 엔진이 사전 리스크 게이트를 지원 안 해 fail-closed 캡 보장 불가라 거부.
    with pytest.raises(KISUsageError, match="해외"):
        make_stock_order_plan(venue="overseas", symbol="AAPL", side="buy", quantity=10,
                              limit_price="150")


def test_build_rejects_bad_inputs():
    with pytest.raises(KISUsageError):
        make_stock_order_plan(venue="crypto", symbol="X", side="buy", quantity=1)
    with pytest.raises(KISUsageError):
        make_stock_order_plan(venue="domestic", symbol="005930", side="hold", quantity=1)
    with pytest.raises(KISUsageError):
        make_stock_order_plan(venue="domestic", symbol="005930", side="buy", quantity=0)
    with pytest.raises(KISUsageError):
        make_stock_order_plan(venue="domestic", symbol="005930", side="buy", quantity=True)  # bool


def test_build_rejects_nonscalar_symbol():
    # taint 경계: 읽기 도구 dict/객체를 symbol 로 넘기면 거부(스칼라만)
    with pytest.raises(KISUsageError):
        make_stock_order_plan(venue="domestic", symbol={"code": "005930"},  # type: ignore[arg-type]
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
    assert plan_cancel_order(_PAPER, client_order_id="20260101-abc") == "20260101-abc"
    with pytest.raises(KISUsageError):
        plan_cancel_order(_PAPER, client_order_id="")
    gate = RealOrderGate.from_env({}, "real")
    with pytest.raises(KISUsageError):
        plan_cancel_order(gate, client_order_id="x")          # 실전 게이트 닫힘


def test_plan_modify_real_requires_risk():
    with pytest.raises(KISUsageError, match="RiskLimits"):
        plan_modify_order(_REAL, has_risk=False, client_order_id="x", limit_price="100")
    out = plan_modify_order(_REAL, has_risk=True, client_order_id="x", limit_price="100", quantity=5)
    assert out == ModifyOrderPlan(client_order_id="x", limit_price="100", quantity=5)


# --- integration: run_place_order (가드레일 + elicitation 확인 + 집행) -------
_ACCEPT = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117057", "ORD_TMD": "121052"}},
)
_REAL_ENV = {"KIS_MCP_ALLOW_REAL": "1", "KIS_MCP_REAL_CONFIRM": "i-understand-real-money"}


class _FakeTransport:
    def __init__(self, environment):
        self.environment = environment
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id})
        return _ACCEPT


def _order_client(environment="paper", risk=None):
    transport = _FakeTransport(environment)
    kis = KISClient(app_key="k", app_secret="s", account="12345678-01",
                    environment=environment, transport=transport, risk=risk)
    return kis, transport


class _AcceptCtx:
    async def elicit(self, message, schema):
        from mcp.server.elicitation import AcceptedElicitation
        return AcceptedElicitation(data=schema(confirm=True))


class _DeclineCtx:
    async def elicit(self, message, schema):
        from mcp.server.elicitation import DeclinedElicitation
        return DeclinedElicitation()


def test_run_place_order_paper_executes():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("paper")
    out = asyncio.run(run_place_order(
        kis, RealOrderGate.from_env({}, "paper"), None, CircuitBreaker(), _AcceptCtx(),
        venue="domestic", symbol="005930", side="buy", quantity=1))
    assert out["sent"] is True and out["report"] is not None
    assert len(t.calls) == 1                           # 모의는 즉시 집행(확인 생략 가능)


def test_run_place_order_real_accept_executes():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    out = asyncio.run(run_place_order(
        kis, RealOrderGate.from_env(_REAL_ENV, "real"), frozenset({"005930"}), CircuitBreaker(),
        _AcceptCtx(), venue="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert out["sent"] is True and len(t.calls) == 1


def test_run_place_order_real_decline_no_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    out = asyncio.run(run_place_order(
        kis, RealOrderGate.from_env(_REAL_ENV, "real"), frozenset({"005930"}), CircuitBreaker(),
        _DeclineCtx(), venue="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert out["sent"] is False and t.calls == []      # 확인 거부 -> 전송 안 함


def test_run_place_order_real_gate_closed_raises():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    with pytest.raises(KISUsageError, match="KIS_MCP_ALLOW_REAL"):
        asyncio.run(run_place_order(
            kis, RealOrderGate.from_env({}, "real"), frozenset({"005930"}), CircuitBreaker(),
            _AcceptCtx(), venue="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert t.calls == []                               # 게이트 닫힘 -> 와이어 전 거부


def test_run_place_order_real_no_risk_raises():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=None)          # 캡 미설정
    with pytest.raises(KISUsageError, match="RiskLimits"):
        asyncio.run(run_place_order(
            kis, RealOrderGate.from_env(_REAL_ENV, "real"), frozenset({"005930"}), CircuitBreaker(),
            _AcceptCtx(), venue="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert t.calls == []                               # fail-closed


def test_run_place_order_breaker_halted_refuses():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    breaker = CircuitBreaker(max_real_orders=1)
    breaker.record_and_check()
    with pytest.raises(KISUsageError):
        breaker.record_and_check()                     # HALT
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    out = asyncio.run(run_place_order(
        kis, RealOrderGate.from_env(_REAL_ENV, "real"), frozenset({"005930"}), breaker,
        _AcceptCtx(), venue="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert out["sent"] is False and "HALT" in out["reason"] and t.calls == []


# --- confirm_order (elicitation 게이트) 직접 테스트 -- 보안 핵심, 전 분기 고정 ---
def test_confirm_order_accept_confirm_true():
    pytest.importorskip("mcp")
    from kis_trader.mcp._elicit import confirm_order
    assert asyncio.run(confirm_order(_AcceptCtx(), {"symbol": "005930"})) is True


def test_confirm_order_accept_but_confirm_false():
    pytest.importorskip("mcp")
    from mcp.server.elicitation import AcceptedElicitation

    from kis_trader.mcp._elicit import confirm_order

    class _Ctx:
        async def elicit(self, message, schema):
            return AcceptedElicitation(data=schema(confirm=False))   # 폼은 수락하되 confirm=False
    assert asyncio.run(confirm_order(_Ctx(), {})) is False


def test_confirm_order_decline_and_cancel_fail_closed():
    pytest.importorskip("mcp")
    from mcp.server.elicitation import CancelledElicitation, DeclinedElicitation

    from kis_trader.mcp._elicit import confirm_order

    class _Dec:
        async def elicit(self, message, schema):
            return DeclinedElicitation()

    class _Can:
        async def elicit(self, message, schema):
            return CancelledElicitation()
    assert asyncio.run(confirm_order(_Dec(), {})) is False
    assert asyncio.run(confirm_order(_Can(), {})) is False


def test_confirm_order_no_backchannel_fail_closed():
    pytest.importorskip("mcp")
    from mcp.shared.exceptions import NoBackChannelError

    from kis_trader.mcp._elicit import confirm_order

    class _NoBC:
        async def elicit(self, message, schema):
            raise NoBackChannelError("elicitation/create")           # 클라 elicitation 미지원
    assert asyncio.run(confirm_order(_NoBC(), {})) is False           # fail-closed


# --- taint 경계: limit_price 도 스칼라만 ----------------------------------
def test_build_rejects_nonscalar_limit_price():
    with pytest.raises(KISUsageError):
        make_stock_order_plan(venue="domestic", symbol="005930", side="buy", quantity=1,
                              limit_price={"p": 100})  # type: ignore[arg-type]


# --- 통합: 실전 allowlist 미스 -> 와이어 전 거부(no wire) ------------------
def test_run_place_order_real_allowlist_miss_no_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    with pytest.raises(KISUsageError, match="allowlist"):
        asyncio.run(run_place_order(
            kis, RealOrderGate.from_env(_REAL_ENV, "real"), frozenset({"005930"}), CircuitBreaker(),
            _AcceptCtx(), venue="domestic", symbol="000660", side="buy", quantity=1, limit_price="100000"))
    assert t.calls == []


# --- 통합: 실전 취소 사람 거부 -> 미전송(cancel 도 사람확인 게이트) --------
def test_run_cancel_order_real_decline_no_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_cancel_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    out = asyncio.run(run_cancel_order(
        kis, RealOrderGate.from_env(_REAL_ENV, "real"), CircuitBreaker(), _DeclineCtx(),
        client_order_id="20260101-abc"))
    assert out["sent"] is False and t.calls == []


def test_run_place_order_breaker_trips_this_order_no_wire():
    # 이 주문이 한도를 넘겨 trip 하는 경로(_authorize_order 가 breaker 예외를 잡아 일관 refusal) -- 미전송.
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    breaker = CircuitBreaker(max_real_orders=1)
    breaker.record_and_check()                         # count=1 (아직 halted 아님)
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    out = asyncio.run(run_place_order(
        kis, RealOrderGate.from_env(_REAL_ENV, "real"), frozenset({"005930"}), breaker,
        _AcceptCtx(), venue="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert out["sent"] is False and "HALT" in out["reason"] and t.calls == []


def test_build_server_rejects_env_mismatch():
    # split-brain 차단: 게이트 환경 != 클라이언트 환경이면 서버 생성 거부(직접 조립 경로 방어).
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import build_server
    kis, _ = _order_client("paper")
    with pytest.raises(KISUsageError, match="split-brain"):
        build_server(kis, gate=RealOrderGate.from_env(_REAL_ENV, "real"),
                     allowlist=frozenset({"005930"}), breaker=CircuitBreaker())


# --- 통합: cancel/modify execute(paper, 주문 시드 후) -- 공유 _authorize_order 경로 집행 확인 ---
def test_run_cancel_order_paper_executes():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_cancel_order
    kis, t = _order_client("paper")
    seed = kis.domestic.stock("005930").buy(quantity=1)        # 주문 1건 시드(store 기록)
    out = asyncio.run(run_cancel_order(
        kis, RealOrderGate.from_env({}, "paper"), CircuitBreaker(), _AcceptCtx(),
        client_order_id=seed.client_order_id))
    assert out["sent"] is True
    assert len(t.calls) >= 2                                   # place(시드) + cancel 와이어


def test_run_modify_order_paper_executes():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_modify_order
    kis, t = _order_client("paper")
    seed = kis.domestic.stock("005930").buy(quantity=1, limit_price="70000")
    out = asyncio.run(run_modify_order(
        kis, RealOrderGate.from_env({}, "paper"), CircuitBreaker(), _AcceptCtx(),
        client_order_id=seed.client_order_id, limit_price="71000"))
    assert out["sent"] is True
    assert len(t.calls) >= 2                                   # place(시드) + modify 와이어
