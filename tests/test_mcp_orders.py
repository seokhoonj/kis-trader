"""MCP 실주문 가드레일 -- kis_trader.mcp._guardrails (이중게이트·allowlist·서킷브레이커·티켓 빌드).

전부 fail-closed: 조건 미충족이면 와이어 전에 KISUsageError. 모의(paper)는 자유, 실전(real)만 게이트.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

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
from kis_trader.mcp._session import MCPSession, OrderSnapshot, _StartupAuthorization
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
    plan = make_stock_order_plan(region="domestic", symbol="005930", side="buy", quantity=10,
                             limit_price="70000")
    assert plan == StockOrderPlan(region="domestic", symbol="005930", side="buy",
                                  order_type="limit", quantity=10, limit_price="70000")


def test_build_domestic_market_order():
    plan = make_stock_order_plan(region="domestic", symbol="005930", side="sell", quantity=1)
    assert plan.order_type == "market" and plan.limit_price is None


def test_build_carries_division():
    # 주문구분(최유리지정가 등)이 plan 에 실려 집행까지 전달된다 -- preview 가 echo 한 값이 조용히
    # 버려지지 않도록.
    plan = make_stock_order_plan(region="domestic", symbol="005930", side="buy", quantity=10,
                                 limit_price=None, division="immediate_limit")
    assert plan.division == "immediate_limit"


def test_build_rejects_unknown_division():
    with pytest.raises(KISUsageError, match="division"):
        make_stock_order_plan(region="domestic", symbol="005930", side="buy", quantity=1,
                              division="bogus")


@pytest.mark.parametrize("division", ["gtp_limit", "gtp_immediate_limit", "gtp_priority_limit"])
def test_build_rejects_nxt_gtp_divisions_at_boundary(division):
    # NXT 프리마켓(GTP) 전용 구분은 MCP 가 KRX 기본 경로로만 보내 와이어에서 항상 거부되므로,
    # 깊은 라이브러리 에러 대신 taint 경계에서 명확한 사유로 막아야 한다(docs/mcp.md 와 일치).
    with pytest.raises(KISUsageError, match="GTP"):
        make_stock_order_plan(region="domestic", symbol="005930", side="buy", quantity=1,
                              division=division)


def test_build_accepts_overseas_limit():
    # 해외는 이제 지원된다(지정가 전용·exchange 필수·division 금지). 통화별 fail-closed 캡은 안전 엔진이 건다.
    plan = make_stock_order_plan(region="overseas", symbol="AAPL", side="buy", quantity=10,
                                 limit_price="150", exchange="NAS")
    assert plan == StockOrderPlan(region="overseas", symbol="AAPL", side="buy", order_type="limit",
                                  quantity=10, limit_price="150", exchange="NAS")


def test_build_overseas_requires_limit_price():
    with pytest.raises(KISUsageError, match="지정가"):
        make_stock_order_plan(region="overseas", symbol="AAPL", side="buy", quantity=10, exchange="NAS")


def test_build_overseas_requires_exchange():
    with pytest.raises(KISUsageError, match="거래소"):
        make_stock_order_plan(region="overseas", symbol="AAPL", side="buy", quantity=10,
                              limit_price="150")


def test_build_overseas_rejects_unknown_exchange():
    with pytest.raises(KISUsageError, match="거래소"):
        make_stock_order_plan(region="overseas", symbol="AAPL", side="buy", quantity=10,
                              limit_price="150", exchange="XXX")


def test_build_overseas_rejects_division():
    with pytest.raises(KISUsageError, match="division"):
        make_stock_order_plan(region="overseas", symbol="AAPL", side="buy", quantity=10,
                              limit_price="150", exchange="NAS", division="immediate_limit")


def test_build_domestic_rejects_exchange():
    with pytest.raises(KISUsageError, match="exchange"):
        make_stock_order_plan(region="domestic", symbol="005930", side="buy", quantity=1,
                              limit_price="70000", exchange="NAS")


def test_build_rejects_bad_inputs():
    with pytest.raises(KISUsageError):
        make_stock_order_plan(region="crypto", symbol="X", side="buy", quantity=1)
    with pytest.raises(KISUsageError):
        make_stock_order_plan(region="domestic", symbol="005930", side="hold", quantity=1)
    with pytest.raises(KISUsageError):
        make_stock_order_plan(region="domestic", symbol="005930", side="buy", quantity=0)
    with pytest.raises(KISUsageError):
        make_stock_order_plan(region="domestic", symbol="005930", side="buy", quantity=True)  # bool


def test_build_rejects_nonscalar_symbol():
    # taint 경계: 읽기 도구 dict/객체를 symbol 로 넘기면 거부(스칼라만)
    with pytest.raises(KISUsageError):
        make_stock_order_plan(region="domestic", symbol={"code": "005930"},  # type: ignore[arg-type]
                          side="buy", quantity=1, limit_price="100")


# --- plan handlers (가드레일 합성, 와이어 전) ------------------------------
_PAPER = RealOrderGate.from_env({}, "paper")
_REAL = RealOrderGate.from_env(
    {"KIS_MCP_ALLOW_REAL": "1", "KIS_MCP_REAL_CONFIRM": "i-understand-real-money"}, "real"
)


def test_plan_place_paper_no_caps_needed():
    plan = plan_place_order(_PAPER, None, has_risk=False, region="domestic",
                            symbol="005930", side="buy", quantity=1)
    assert plan.symbol == "005930" and plan.order_type == "market"


def test_plan_place_real_requires_risk():
    with pytest.raises(KISUsageError, match="RiskLimits"):
        plan_place_order(_REAL, frozenset({"005930"}), has_risk=False, region="domestic",
                         symbol="005930", side="buy", quantity=1, limit_price="70000")


def test_plan_place_real_requires_allowlist():
    with pytest.raises(KISUsageError, match="allowlist"):
        plan_place_order(_REAL, None, has_risk=True, region="domestic",
                         symbol="005930", side="buy", quantity=1, limit_price="70000")


def test_plan_place_real_gate_closed():
    gate = RealOrderGate.from_env({}, "real")          # 이중게이트 미설정
    with pytest.raises(KISUsageError, match="KIS_MCP_ALLOW_REAL"):
        plan_place_order(gate, frozenset({"005930"}), has_risk=True, region="domestic",
                         symbol="005930", side="buy", quantity=1, limit_price="70000")


def test_plan_place_real_all_pass():
    plan = plan_place_order(_REAL, frozenset({"005930"}), has_risk=True, region="domestic",
                            symbol="005930", side="buy", quantity=10, limit_price="70000")
    assert plan == StockOrderPlan(region="domestic", symbol="005930", side="buy",
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


def test_plan_modify_real_runs_allowlist_on_resolved_symbol():
    # 정정은 노출을 늘릴 수 있어(재가격) 원주문 종목이 실전 allowlist 를 통과해야 한다(place 와 동일).
    with pytest.raises(KISUsageError, match="allowlist"):
        plan_modify_order(_REAL, has_risk=True, allowlist=frozenset({"000660"}),
                          client_order_id="x", limit_price="100", symbol="005930")
    out = plan_modify_order(_REAL, has_risk=True, allowlist=frozenset({"005930"}),
                            client_order_id="x", limit_price="100", symbol="005930", side="buy")
    assert out.symbol == "005930" and out.side == "buy"


def test_plan_modify_without_resolved_symbol_skips_allowlist():
    # 저장소에서 종목을 못 찾으면(symbol=None) allowlist 는 건너뛴다 -- 그 외 게이트/사람확인은 유지.
    out = plan_modify_order(_REAL, has_risk=True, allowlist=frozenset({"000660"}),
                            client_order_id="x", limit_price="100")
    assert out.symbol is None


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


def _order_client(environment="paper", risk=None, account="12345678-01"):
    transport = _FakeTransport(environment)
    kis = KISClient(app_key="k", app_secret="s", account=account,
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


def _snap(kis, gate, *, profile="general"):
    """run_* 가 받는 OrderSnapshot 을 직접 조립한다(server.order_snapshot 을 거치지 않는 단위 경로).
    주문 게이트는 계좌 orderable(상품코드 01/22=True, 29=False)로 가르며 프로필 이름은 보지 않는다
    -- profile 라벨은 전환 맥락 표시일 뿐이다(기본값 'general')."""
    return OrderSnapshot(profile=profile, environment=kis.environment, client=kis, gate=gate)


def test_run_place_order_paper_executes():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("paper")
    out = asyncio.run(run_place_order(
        _snap(kis, RealOrderGate.from_env({}, "paper")), None, CircuitBreaker(), _AcceptCtx(),
        region="domestic", symbol="005930", side="buy", quantity=1))
    assert out["sent"] is True and out["report"] is not None
    assert out["ticket"]["region"] == "domestic" and "venue" not in out["ticket"]
    assert len(t.calls) == 1                           # 모의는 즉시 집행(확인 생략 가능)


def test_run_place_order_real_accept_executes():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    out = asyncio.run(run_place_order(
        _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"005930"}), CircuitBreaker(),
        _AcceptCtx(), region="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert out["sent"] is True and len(t.calls) == 1


def test_run_place_order_real_decline_no_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    out = asyncio.run(run_place_order(
        _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"005930"}), CircuitBreaker(),
        _DeclineCtx(), region="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert out["sent"] is False and t.calls == []      # 확인 거부 -> 전송 안 함


def test_run_place_order_real_gate_closed_raises():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    with pytest.raises(KISUsageError, match="KIS_MCP_ALLOW_REAL"):
        asyncio.run(run_place_order(
            _snap(kis, RealOrderGate.from_env({}, "real")), frozenset({"005930"}), CircuitBreaker(),
            _AcceptCtx(), region="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert t.calls == []                               # 게이트 닫힘 -> 와이어 전 거부


def test_run_place_order_real_no_risk_raises():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=None)          # 캡 미설정
    with pytest.raises(KISUsageError, match="RiskLimits"):
        asyncio.run(run_place_order(
            _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"005930"}), CircuitBreaker(),
            _AcceptCtx(), region="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
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
        _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"005930"}), breaker,
        _AcceptCtx(), region="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
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
        make_stock_order_plan(region="domestic", symbol="005930", side="buy", quantity=1,
                              limit_price={"p": 100})  # type: ignore[arg-type]


# --- 통합: 실전 allowlist 미스 -> 와이어 전 거부(no wire) ------------------
def test_run_place_order_real_allowlist_miss_no_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    with pytest.raises(KISUsageError, match="allowlist"):
        asyncio.run(run_place_order(
            _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"005930"}), CircuitBreaker(),
            _AcceptCtx(), region="domestic", symbol="000660", side="buy", quantity=1, limit_price="100000"))
    assert t.calls == []


# --- 통합: 실전 취소 사람 거부 -> 미전송(cancel 도 사람확인 게이트) --------
def test_run_cancel_order_real_decline_no_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_cancel_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    out = asyncio.run(run_cancel_order(
        _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), CircuitBreaker(), _DeclineCtx(),
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
        _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"005930"}), breaker,
        _AcceptCtx(), region="domestic", symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert out["sent"] is False and "HALT" in out["reason"] and t.calls == []


# split-brain 차단(게이트 환경 != 클라이언트 환경)은 이제 MCPSession.order_snapshot 생성 시점에
# 재단언된다 -- test_mcp_session::test_s3_* 가 방문 슬롯마다 환경 일치를 전수 고정하고,
# test_mcp_session::test_order_snapshot_rejects_env_mismatch 가 어긋난 build 를 거부함을 확인한다.


# --- 통합: cancel/modify execute(paper, 주문 시드 후) -- 공유 _authorize_order 경로 집행 확인 ---
def test_run_cancel_order_paper_executes():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_cancel_order
    kis, t = _order_client("paper")
    seed = kis.domestic.stock("005930").buy(quantity=1)        # 주문 1건 시드(store 기록)
    out = asyncio.run(run_cancel_order(
        _snap(kis, RealOrderGate.from_env({}, "paper")), CircuitBreaker(), _AcceptCtx(),
        client_order_id=seed.client_order_id))
    assert out["sent"] is True
    assert len(t.calls) >= 2                                   # place(시드) + cancel 와이어


def test_run_modify_order_paper_executes():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_modify_order
    kis, t = _order_client("paper")
    seed = kis.domestic.stock("005930").buy(quantity=1, limit_price="70000")
    out = asyncio.run(run_modify_order(
        _snap(kis, RealOrderGate.from_env({}, "paper")), None, CircuitBreaker(), _AcceptCtx(),
        client_order_id=seed.client_order_id, limit_price="71000"))
    assert out["sent"] is True
    assert len(t.calls) >= 2                                   # place(시드) + modify 와이어
    # 티켓이 원주문 종목/매수매도를 보여준다(사람이 '어느 종목 정정'인지 확인 가능).
    assert out["ticket"]["symbol"] == "005930"
    assert out["ticket"]["side"] == "buy"


# --- 해외 주문 라우팅 + 통화 캡 (plan + e2e) --------------------------------
def test_plan_place_overseas_real_requires_currency_cap():
    # 해외 실전은 결제통화 한도가 없으면 와이어 전에 거부(통화 이름을 담아). 있으면 통과.
    with pytest.raises(KISUsageError, match="KIS_MCP_MAX_ORDER_NOTIONAL_USD"):
        plan_place_order(_REAL, frozenset({"AAPL"}), has_risk=True, capped_currencies=frozenset(),
                         region="overseas", symbol="AAPL", side="buy", quantity=1,
                         limit_price="150", exchange="NAS")
    plan = plan_place_order(_REAL, frozenset({"AAPL"}), has_risk=True,
                            capped_currencies=frozenset({"USD"}), region="overseas", symbol="AAPL",
                            side="buy", quantity=1, limit_price="150", exchange="NAS")
    assert plan.region == "overseas" and plan.exchange == "NAS"


def test_plan_place_overseas_paper_needs_no_cap():
    # 모의 해외는 캡 선검사 대상 아님(gate.is_real() False).
    plan = plan_place_order(_PAPER, None, has_risk=False, capped_currencies=frozenset(),
                            region="overseas", symbol="AAPL", side="buy", quantity=1,
                            limit_price="150", exchange="NAS")
    assert plan.region == "overseas"


def test_run_place_order_overseas_paper_routes_to_overseas_buy():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("paper")
    out = asyncio.run(run_place_order(
        _snap(kis, RealOrderGate.from_env({}, "paper")), None, CircuitBreaker(), _AcceptCtx(),
        frozenset(), region="overseas", symbol="AAPL", side="buy", quantity=1,
        limit_price="1.00", exchange="NAS"))
    assert out["sent"] is True
    assert out["ticket"]["region"] == "overseas" and out["ticket"]["exchange"] == "NAS"
    assert len(t.calls) == 1 and t.calls[0]["tr_id"] == "VTTT1002U"   # 미국 매수 모의 TR


def test_run_place_order_overseas_real_full_gated_sends():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_notional_by_currency={"USD": "1000000"}))
    out = asyncio.run(run_place_order(
        _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"AAPL"}), CircuitBreaker(),
        _AcceptCtx(), frozenset({"USD"}), region="overseas", symbol="AAPL", side="buy", quantity=1,
        limit_price="1.00", exchange="NAS"))
    assert out["sent"] is True
    assert out["ticket"]["region"] == "overseas" and out["ticket"]["exchange"] == "NAS"
    assert len(t.calls) == 1 and t.calls[0]["tr_id"] == "TTTT1002U"   # 미국 매수 실전 TR


def test_run_place_order_overseas_real_missing_cap_no_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_notional_by_currency={"JPY": "1000000"}))
    with pytest.raises(KISUsageError, match="KIS_MCP_MAX_ORDER_NOTIONAL_USD"):
        asyncio.run(run_place_order(
            _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"AAPL"}),
            CircuitBreaker(), _AcceptCtx(), frozenset({"JPY"}), region="overseas", symbol="AAPL",
            side="buy", quantity=1, limit_price="1.00", exchange="NAS"))
    assert t.calls == []                                   # early fail-closed -- 와이어 전 거부


def test_run_place_order_overseas_real_no_risk_no_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=None)
    with pytest.raises(KISUsageError, match="RiskLimits"):
        asyncio.run(run_place_order(
            _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"AAPL"}),
            CircuitBreaker(), _AcceptCtx(), frozenset(), region="overseas", symbol="AAPL",
            side="buy", quantity=1, limit_price="1.00", exchange="NAS"))
    assert t.calls == []


def test_run_place_order_overseas_real_allowlist_miss_no_wire():
    # 해외도 국내와 같이 실전 allowlist 미스면 와이어 전 거부(통화캡은 통과시키고 allowlist 에서 막힘).
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_notional_by_currency={"USD": "1000000"}))
    with pytest.raises(KISUsageError, match="allowlist"):
        asyncio.run(run_place_order(
            _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"AAPL"}),
            CircuitBreaker(), _AcceptCtx(), frozenset({"USD"}), region="overseas", symbol="MSFT",
            side="buy", quantity=1, limit_price="1.00", exchange="NAS"))
    assert t.calls == []


def test_run_place_order_overseas_real_decline_no_wire():
    # 해외도 국내와 같이 사람 확인 거부면 미전송.
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("real", risk=RiskLimits(max_order_notional_by_currency={"USD": "1000000"}))
    out = asyncio.run(run_place_order(
        _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"AAPL"}),
        CircuitBreaker(), _DeclineCtx(), frozenset({"USD"}), region="overseas", symbol="AAPL",
        side="buy", quantity=1, limit_price="1.00", exchange="NAS"))
    assert out["sent"] is False and t.calls == []


# --- order_preview: 해외 + 정직한 선거부 ------------------------------------
def test_order_preview_overseas_shows_region_exchange_not_sent():
    from kis_trader.mcp.handlers import order_preview
    kis = SimpleNamespace(environment="paper", _account="12345678-01")
    out = order_preview(kis, frozenset(), symbol="AAPL", side="buy", quantity=1,
                        limit_price="150", region="overseas", exchange="NAS")
    assert out["sent"] is False and out["region"] == "overseas" and out["exchange"] == "NAS"
    assert out["order_type"] == "limit" and out["division"] is None


def test_order_preview_overseas_real_honest_early_cap_rejection():
    from kis_trader.mcp.handlers import order_preview
    kis = SimpleNamespace(environment="real", _account="12345678-01")
    with pytest.raises(KISUsageError, match="KIS_MCP_MAX_ORDER_NOTIONAL_USD"):
        order_preview(kis, frozenset(), symbol="AAPL", side="buy", quantity=1,
                      limit_price="150", region="overseas", exchange="NAS")


# --- 주문 능력 게이트 (C7): 프로필 이름이 아니라 계좌 orderable 로 가른다 ----
# ISA/연금저축 맥락(프로필 라벨)으로 전환해도 계좌가 주문 가능한 주식 계좌면 주문이 열린다 --
# 과거의 'general 이름' 게이트는 폐지됐다(ISA·연금저축은 주문 가능이므로 막으면 안 된다).
@pytest.mark.parametrize("profile", ["general", "isa", "pension"])
def test_order_allowed_for_orderable_account_any_profile(profile):
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("paper", account="12345678-01")   # 주문 가능(위탁/ISA=01)
    snap = _snap(kis, RealOrderGate.from_env({}, "paper"), profile=profile)
    out = asyncio.run(run_place_order(snap, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                      region="domestic", symbol="005930", side="buy", quantity=1))
    assert out["sent"] is True and len(t.calls) == 1


# --- DOMESTIC 주문 가능 계좌(위탁/ISA 01·연금저축 22): place/cancel/modify/preview 전부 통과 ----
@pytest.mark.parametrize(("account", "profile"), [
    ("12345678-01", "general"), ("12345678-01", "isa"), ("12345678-22", "pension"),
])
def test_domestic_order_tools_pass_gate_for_orderable_accounts(account, profile):
    # 주문 가능 계좌(위탁/ISA=01, 연금저축=22)는 네 주문 도구(place/cancel/modify/order_preview)가
    # 모두 주문 권한 게이트를 통과한다 -- 상품코드 orderable 로 가르므로 프로필 라벨은 무관하다.
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import (
        run_cancel_order,
        run_modify_order,
        run_order_preview,
        run_place_order,
    )
    kis, _t = _order_client("paper", account=account)
    snap = _snap(kis, RealOrderGate.from_env({}, "paper"), profile=profile)
    placed = asyncio.run(run_place_order(snap, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                         region="domestic", symbol="005930", side="buy", quantity=1))
    assert placed["sent"] is True
    preview = run_order_preview(snap, frozenset(), symbol="005930", side="buy", quantity=1,
                                limit_price="70000")
    assert preview["sent"] is False                 # preview 는 게이트 통과하되 전송 0
    cancel_seed = kis.domestic.stock("005930").buy(quantity=1, limit_price="70000")
    cancelled = asyncio.run(run_cancel_order(snap, CircuitBreaker(), _AcceptCtx(),
                                             client_order_id=cancel_seed.client_order_id))
    assert cancelled["sent"] is True
    modify_seed = kis.domestic.stock("005930").buy(quantity=2, limit_price="70000")
    modified = asyncio.run(run_modify_order(snap, None, CircuitBreaker(), _AcceptCtx(),
                                            client_order_id=modify_seed.client_order_id,
                                            limit_price="71000"))
    assert modified["sent"] is True
    # 티켓에 활성 계좌 상품코드가 실려 사람이 어느 계좌 종류로 나가는지 확인한다(FIX 5).
    assert placed["ticket"]["product_code"] == account.split("-")[1]


# --- IRP(29)는 조회전용 -- orderable=False 라 세 주문 도구 모두 거부, 와이어 0 ----
def test_order_refused_for_irp_product_29_no_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_cancel_order, run_modify_order, run_place_order
    kis, t = _order_client("paper", account="12345678-29")   # StockAccount 지만 IRP(29)=조회전용
    snap = _snap(kis, RealOrderGate.from_env({}, "paper"), profile="irp")
    with pytest.raises(KISUsageError, match="주문 대상"):
        asyncio.run(run_place_order(snap, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                    region="domestic", symbol="005930", side="buy", quantity=1))
    with pytest.raises(KISUsageError, match="주문 대상"):
        asyncio.run(run_cancel_order(snap, CircuitBreaker(), _AcceptCtx(), client_order_id="x"))
    with pytest.raises(KISUsageError, match="주문 대상"):
        asyncio.run(run_modify_order(snap, None, CircuitBreaker(), _AcceptCtx(),
                                     client_order_id="x", limit_price="100"))
    assert t.calls == []


# --- OVERSEAS 주문은 위탁(general) 전용 -- 주문 가능 계좌라도 ISA/연금저축은 해외 거부(와이어 0) ----
@pytest.mark.parametrize(("account", "profile"), [("12345678-01", "isa"), ("12345678-22", "pension")])
def test_overseas_order_refused_for_non_general_profile_no_wire(account, profile):
    # 국내는 주문 가능한 ISA(01)/연금저축(22)이라도 해외 주문/미리보기는 위탁(general) 전용 게이트에
    # 걸려 거부된다(ISA/연금저축의 해외 주식 주문 가능 여부 미확인). 와이어 0.
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_order_preview, run_place_order
    kis, t = _order_client("paper", account=account)
    snap = _snap(kis, RealOrderGate.from_env({}, "paper"), profile=profile)
    with pytest.raises(KISUsageError, match="해외 주문"):
        asyncio.run(run_place_order(snap, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                    region="overseas", symbol="AAPL", side="buy", quantity=1,
                                    limit_price="1.00", exchange="NAS"))
    with pytest.raises(KISUsageError, match="해외 주문"):
        run_order_preview(snap, frozenset(), symbol="AAPL", side="buy", quantity=1,
                          limit_price="1.00", region="overseas", exchange="NAS")
    assert t.calls == []


def test_overseas_order_allowed_for_general_product_01_paper():
    # 반대편 고정(allow side): 위탁(general) + 상품코드 01 은 모의 해외 주문이 통과해 와이어에 닿는다.
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("paper", account="12345678-01")
    snap = _snap(kis, RealOrderGate.from_env({}, "paper"), profile="general")
    out = asyncio.run(run_place_order(snap, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                      region="overseas", symbol="AAPL", side="buy", quantity=1,
                                      limit_price="1.00", exchange="NAS"))
    assert out["sent"] is True and out["ticket"]["region"] == "overseas"
    assert len(t.calls) == 1


# --- 직접-자격증명(profile=None) 해외: 상품코드 01 은 허용(운영자 슬롯), 22 는 거부 -------------
def test_overseas_order_direct_cred_product_01_allowed_product_22_refused():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    # profile=None + 상품코드 01 -> 이름 게이트 건너뛰고 상품코드 01 통과 -> 해외 모의 전송.
    kis01, t01 = _order_client("paper", account="12345678-01")
    snap01 = _snap(kis01, RealOrderGate.from_env({}, "paper"), profile=None)
    out = asyncio.run(run_place_order(snap01, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                      region="overseas", symbol="AAPL", side="buy", quantity=1,
                                      limit_price="1.00", exchange="NAS"))
    assert out["sent"] is True and len(t01.calls) == 1
    # profile=None + 상품코드 22 -> 이름 게이트는 건너뛰어도 상품코드 01 아님에서 거부(와이어 0).
    kis22, t22 = _order_client("paper", account="12345678-22")
    snap22 = _snap(kis22, RealOrderGate.from_env({}, "paper"), profile=None)
    with pytest.raises(KISUsageError, match="해외 주문"):
        asyncio.run(run_place_order(snap22, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                    region="overseas", symbol="AAPL", side="buy", quantity=1,
                                    limit_price="1.00", exchange="NAS"))
    assert t22.calls == []


# --- 파생(비-StockAccount) 계좌: 네 주문 도구 전부 거부, 와이어 0(기록 transport 로 단언) ----
def test_order_tools_refused_for_derivatives_account_zero_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import (
        run_cancel_order,
        run_modify_order,
        run_order_preview,
        run_place_order,
    )
    kis, t = _order_client("paper", account="12345678-03")   # product 03 -> DomesticDerivativesAccount
    snap = _snap(kis, RealOrderGate.from_env({}, "paper"), profile=None)
    with pytest.raises(KISUsageError, match="주문 대상"):
        asyncio.run(run_place_order(snap, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                    region="domestic", symbol="005930", side="buy", quantity=1))
    with pytest.raises(KISUsageError, match="주문 대상"):
        asyncio.run(run_cancel_order(snap, CircuitBreaker(), _AcceptCtx(), client_order_id="x"))
    with pytest.raises(KISUsageError, match="주문 대상"):
        asyncio.run(run_modify_order(snap, None, CircuitBreaker(), _AcceptCtx(),
                                     client_order_id="x", limit_price="100"))
    with pytest.raises(KISUsageError, match="주문 대상"):
        run_order_preview(snap, frozenset(), symbol="005930", side="buy", quantity=1, limit_price="100")
    assert t.calls == []


# --- 직접-자격증명 기동(profile=None): 이름 게이트 없이 계좌 orderable 로만 가른다 ------------
def test_order_direct_cred_orderable_account_passes():
    # profile=None + 상품코드 01(주문 가능) + StockAccount -> 통상 게이트로 진행(모의 전송).
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("paper", account="12345678-01")
    snap = _snap(kis, RealOrderGate.from_env({}, "paper"), profile=None)
    out = asyncio.run(run_place_order(snap, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                      region="domestic", symbol="005930", side="buy", quantity=1))
    assert out["sent"] is True and len(t.calls) == 1


def test_order_direct_cred_irp_product_29_refused_no_wire():
    # profile=None + 상품코드 29(IRP 조회전용) -> orderable=False 라 거부(와이어 0).
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    kis, t = _order_client("paper", account="12345678-29")
    snap = _snap(kis, RealOrderGate.from_env({}, "paper"), profile=None)
    with pytest.raises(KISUsageError, match="주문 대상"):
        asyncio.run(run_place_order(snap, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                    region="domestic", symbol="005930", side="buy", quantity=1))
    assert t.calls == []


def test_order_direct_cred_non_stock_account_refused():
    # profile=None + 비-StockAccount 뷰(파생) -> is_orderable_account False 로 거부(방어심층 isinstance 분기).
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order

    class _NonStockClient:
        environment = "paper"
        _account = "12345678-03"

        @property
        def account(self):
            return SimpleNamespace(product_code="03")    # StockAccount 아님

    snap = OrderSnapshot(profile=None, environment="paper", client=_NonStockClient(),
                         gate=RealOrderGate.from_env({}, "paper"))
    with pytest.raises(KISUsageError, match="주문 대상"):
        asyncio.run(run_place_order(snap, None, CircuitBreaker(), _AcceptCtx(), frozenset(),
                                    region="domestic", symbol="005930", side="buy", quantity=1))


# --- order_preview 주문 게이트 parity (place 와 같은 검증) ------------------
def test_run_order_preview_refused_on_non_orderable_account():
    # preview 는 place_order 와 같은 주문 능력 게이트를 거친다 -- IRP(29)면 미리보기도 거부.
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_order_preview
    kis, _ = _order_client("paper", account="12345678-29")   # IRP(29)=조회전용
    snap = _snap(kis, RealOrderGate.from_env({}, "paper"), profile="irp")
    with pytest.raises(KISUsageError, match="주문 대상"):
        run_order_preview(snap, frozenset(), symbol="005930", side="buy", quantity=1, limit_price="100")


# --- C9: 다른 슬롯에서 낸 cid 를 활성 슬롯에서 취소하면 깔끔한 에러, 와이어 0 --
def test_cancel_unknown_cid_on_active_slot_clean_error_no_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_cancel_order
    kis, t = _order_client("paper")        # 이 슬롯 저장소엔 이 cid 가 없다(다른 슬롯에서 냈다고 가정)
    with pytest.raises(KISUsageError):
        asyncio.run(run_cancel_order(
            _snap(kis, RealOrderGate.from_env({}, "paper")), CircuitBreaker(), _AcceptCtx(),
            client_order_id="unknown-20260101-abc"))
    assert t.calls == []                   # 저장소 미보유 cid -> 와이어 전 거부


# --- 환경변수 통화별 캡 파싱(해외) -------------------------------------------
def test_notional_by_currency_env_single_and_multi():
    from kis_trader.mcp.server import _notional_by_currency_from_env
    assert _notional_by_currency_from_env({"KIS_MCP_MAX_ORDER_NOTIONAL_USD": "10000"}) == {"USD": "10000"}
    multi = _notional_by_currency_from_env(
        {"KIS_MCP_MAX_ORDER_NOTIONAL_USD": "10000", "KIS_MCP_MAX_ORDER_NOTIONAL_JPY": "1500000"})
    assert multi == {"USD": "10000", "JPY": "1500000"}


def test_notional_by_currency_env_boundary_and_empty():
    from kis_trader.mcp.server import _notional_by_currency_from_env
    # 접두어 없는 국내(KRW) 한도는 통화 맵으로 새지 않는다(경계).
    assert _notional_by_currency_from_env({"KIS_MCP_MAX_ORDER_NOTIONAL": "5000000"}) == {}
    # 빈 값은 건너뛴다.
    assert _notional_by_currency_from_env({"KIS_MCP_MAX_ORDER_NOTIONAL_USD": ""}) == {}


def test_build_risk_from_env_wires_by_currency_and_keeps_krw():
    from kis_trader.mcp.server import _build_risk_from_env
    risk = _build_risk_from_env({"KIS_MCP_MAX_ORDER_NOTIONAL": "5000000",
                                 "KIS_MCP_MAX_ORDER_NOTIONAL_USD": "10000"})
    assert str(risk.max_order_notional) == "5000000"
    assert set(risk.max_order_notional_by_currency) == {"USD"}


def test_build_risk_from_env_empty_suffix_and_nonnumeric_reject():
    from kis_trader.mcp.server import _build_risk_from_env
    with pytest.raises(KISUsageError):      # 빈 통화 키 -> RiskLimits 거부
        _build_risk_from_env({"KIS_MCP_MAX_ORDER_NOTIONAL_": "10000"})
    with pytest.raises(KISUsageError):      # 비숫자 값 -> RiskLimits 거부
        _build_risk_from_env({"KIS_MCP_MAX_ORDER_NOTIONAL_USD": "abc"})


def test_build_risk_from_env_none_when_unset():
    from kis_trader.mcp.server import _build_risk_from_env
    assert _build_risk_from_env({}) is None


# --- 안전 불변 e2e (S4-S7): 전환이 실주문 언락을 못 건드린다 -------------------
def _slot_session(startup_env="paper", *, risk=None, flags=False, profile="general"):
    """실제 MCPSession(슬롯마다 KISClient+FakeTransport)를 만들고 (session, transports) 를 돌려준다.
    flags=True 면 기동 언락(ALLOW_REAL+REAL_CONFIRM)이 켜진 상태로 얼린다."""
    transports: dict[tuple, _FakeTransport] = {}

    def build(p, e):
        transport = _FakeTransport(e)
        transports[(p, e)] = transport
        kis = KISClient(app_key="k", app_secret="s", account="12345678-01",
                        environment=e, transport=transport, risk=risk)
        return kis

    startup = build(profile, startup_env)
    env = _REAL_ENV if flags else {}
    auth = _StartupAuthorization.from_env(env)
    session = MCPSession(profile=profile, environment=startup_env, startup_client=startup,
                         auth=auth, build=build)
    return session, transports


def test_s4_switch_to_real_without_flags_order_refused_zero_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    session, transports = _slot_session("paper", flags=False)
    session.use_environment("real")                        # 읽기 맥락만 real 로
    assert session.read_client().environment == "real"     # 읽기는 열림(맥락 전환됨)
    with pytest.raises(KISUsageError, match="KIS_MCP_ALLOW_REAL"):
        asyncio.run(run_place_order(session.order_snapshot(), frozenset({"005930"}),
                                    CircuitBreaker(), _AcceptCtx(), frozenset(), region="domestic",
                                    symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert transports[("general", "real")].calls == []     # 전환해도 실주문 와이어 0


def test_s5_switch_to_real_with_flags_decline_zero_wire():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    session, transports = _slot_session("paper", flags=True,
                                        risk=RiskLimits(max_order_quantity=1000))
    session.use_environment("real")
    out = asyncio.run(run_place_order(session.order_snapshot(), frozenset({"005930"}),
                                      CircuitBreaker(), _DeclineCtx(), frozenset(), region="domestic",
                                      symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert out["sent"] is False and transports[("general", "real")].calls == []


def test_s6_switch_to_real_with_flags_accept_sends():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    session, transports = _slot_session("paper", flags=True,
                                        risk=RiskLimits(max_order_quantity=1000))
    session.use_environment("real")
    out = asyncio.run(run_place_order(session.order_snapshot(), frozenset({"005930"}),
                                      CircuitBreaker(), _AcceptCtx(), frozenset(), region="domestic",
                                      symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert out["sent"] is True and len(transports[("general", "real")].calls) == 1


def test_s7_switch_during_elicitation_executes_on_snapshotted_client():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_place_order
    session, transports = _slot_session("real", flags=True,
                                        risk=RiskLimits(max_order_quantity=1000))

    class _SwitchDuringElicit:
        async def elicit(self, message, schema):
            from mcp.server.elicitation import AcceptedElicitation
            session.use_environment("paper")               # 집행 중(elicitation) 동시 전환
            return AcceptedElicitation(data=schema(confirm=True))

    snap = session.order_snapshot()                        # real 슬롯을 얼려 든다
    out = asyncio.run(run_place_order(snap, frozenset({"005930"}), CircuitBreaker(),
                                      _SwitchDuringElicit(), frozenset(), region="domestic",
                                      symbol="005930", side="buy", quantity=1, limit_price="70000"))
    assert out["sent"] is True
    assert len(transports[("general", "real")].calls) == 1  # 스냅샷의 real 클라이언트로 집행
    assert ("general", "paper") not in transports or transports[("general", "paper")].calls == []


# --- F2: 해외 정정·취소도 해외 전용(위탁) 게이트를 받는다 (T5) ------------------
# run_cancel_order/run_modify_order 는 저장된 원주문의 거래소로 region 을 판별한다 -- 해외 원주문이면
# 해외 전용(위탁 general) 게이트가 걸려, ISA/연금저축 슬롯에서의 해외 정정·취소는 거부된다(default
# domestic 으로 흘러 게이트를 건너뛰지 않는다). 일반(general)에서는 통과한다.
def _seed_overseas(kis, cid="os-f2"):
    """해외(NAS) 주문 1건을 store 에 시드하고 그 client_order_id 를 돌려준다."""
    report = kis.overseas.stock("AAPL", exchange="NAS").buy(
        quantity=1, limit_price="1.00", client_order_id=cid)
    return report.client_order_id


@pytest.mark.parametrize("profile", ["isa", "pension"])
def test_f2_overseas_cancel_refused_under_non_general_profile(profile):
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_cancel_order
    kis, t = _order_client("paper")
    cid = _seed_overseas(kis)
    seeded = len(t.calls)                                   # 시드(place) 와이어
    with pytest.raises(KISUsageError, match="general"):
        asyncio.run(run_cancel_order(
            _snap(kis, RealOrderGate.from_env({}, "paper"), profile=profile),
            CircuitBreaker(), _AcceptCtx(), client_order_id=cid))
    assert len(t.calls) == seeded                           # 시드 외 추가 와이어 0


@pytest.mark.parametrize("profile", ["isa", "pension"])
def test_f2_overseas_modify_refused_under_non_general_profile(profile):
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_modify_order
    kis, t = _order_client("paper")
    cid = _seed_overseas(kis)
    seeded = len(t.calls)
    with pytest.raises(KISUsageError, match="general"):
        asyncio.run(run_modify_order(
            _snap(kis, RealOrderGate.from_env({}, "paper"), profile=profile),
            None, CircuitBreaker(), _AcceptCtx(), client_order_id=cid, limit_price="2.00"))
    assert len(t.calls) == seeded


def test_f2_overseas_cancel_allowed_under_general():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_cancel_order
    kis, t = _order_client("paper")
    cid = _seed_overseas(kis)
    out = asyncio.run(run_cancel_order(
        _snap(kis, RealOrderGate.from_env({}, "paper"), profile="general"),
        CircuitBreaker(), _AcceptCtx(), client_order_id=cid))
    assert out["sent"] is True                              # general 은 해외 게이트 통과 -> 집행
    assert len(t.calls) >= 2                                # place(시드) + cancel 와이어


def test_f2_overseas_modify_allowed_under_general():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_modify_order
    kis, t = _order_client("paper")
    cid = _seed_overseas(kis)
    out = asyncio.run(run_modify_order(
        _snap(kis, RealOrderGate.from_env({}, "paper"), profile="general"),
        None, CircuitBreaker(), _AcceptCtx(), client_order_id=cid, limit_price="2.00"))
    assert out["sent"] is True
    assert len(t.calls) >= 2                                # place(시드) + modify 와이어
    assert out["ticket"]["symbol"] == "AAPL"               # 원주문 종목이 티켓에 보인다


# --- FIX E: HALT 뒤에도 실전 취소는 열려 있다(노출 축소), place/modify 는 막힌다 -------------
def test_run_cancel_order_breaker_halted_still_reaches_wire_real():
    # 서킷브레이커가 HALT 된 뒤에도 실전 취소는 사람 확인 후 와이어에 닿는다 -- 취소는 노출을
    # 줄이므로 브레이커 슬롯을 쓰지도, HALT 에 막히지도 않는다(위험한 주문을 지우지 못하는 것
    # 자체가 위험이라, 라이브러리도 취소를 리스크 게이트에 걸지 않는 것과 일관).
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_cancel_order
    breaker = CircuitBreaker(max_real_orders=1)
    breaker.record_and_check()
    with pytest.raises(KISUsageError):
        breaker.record_and_check()                          # HALT
    assert breaker.halted is True
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    seed = kis.domestic.stock("005930").buy(quantity=1, limit_price="70000")   # 원주문 시드
    wired = len(t.calls)
    out = asyncio.run(run_cancel_order(
        _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), breaker, _AcceptCtx(),
        client_order_id=seed.client_order_id))
    assert out["sent"] is True                              # HALT 뒤에도 취소는 열려 있다
    assert len(t.calls) == wired + 1                        # 취소 와이어가 실제로 나갔다
    assert breaker.halted is True                           # 취소는 브레이커 상태를 건드리지 않는다


def test_run_modify_order_breaker_halted_refuses_no_wire():
    # 정정은 노출을 늘릴 수 있어(재가격) HALT 상태에서 일관되게 거부되고 와이어 0 이다.
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import run_modify_order
    breaker = CircuitBreaker(max_real_orders=1)
    breaker.record_and_check()
    with pytest.raises(KISUsageError):
        breaker.record_and_check()                          # HALT
    kis, t = _order_client("real", risk=RiskLimits(max_order_quantity=1000))
    seed = kis.domestic.stock("005930").buy(quantity=1, limit_price="70000")
    wired = len(t.calls)
    out = asyncio.run(run_modify_order(
        _snap(kis, RealOrderGate.from_env(_REAL_ENV, "real")), frozenset({"005930"}), breaker,
        _AcceptCtx(), client_order_id=seed.client_order_id, limit_price="71000"))
    assert out["sent"] is False and "HALT" in out["reason"]
    assert len(t.calls) == wired                            # 정정 와이어 0 -- place/modify 는 HALT 에 막힌다
