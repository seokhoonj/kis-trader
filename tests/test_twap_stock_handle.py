from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kis_trader import KISClient, OrderStore, RiskLimits
from kis_trader.errors import KISUsageError
from kis_trader.execution import TWAPExecutionResult, execute_twap, make_twap_schedule
from kis_trader.transport import RawResponse

_KST = timezone(timedelta(hours=9))


class _Kis:
    """execute_twap 는 목(mock)으로 가로채므로 클라이언트는 자리표시자면 충분하다."""


def test_stock_handle_twap_builds_schedule_and_delegates(monkeypatch):
    from kis_trader.domestic import stock as stock_module
    from kis_trader.domestic.stock import DomesticStock

    captured = {}

    def _fake_execute(client, schedule):
        captured["client"] = client
        captured["schedule"] = schedule
        return TWAPExecutionResult(schedule=schedule, outcomes=())

    monkeypatch.setattr(stock_module, "execute_twap", _fake_execute)

    kis = _Kis()
    handle = DomesticStock(kis, "005930", exchange="KRX")
    # start well in the future so the planner's past-guard/session-window accept it deterministically.
    result = handle.twap(side="buy", quantity=100, over="20m", slices=4,
                         start=datetime(2099, 1, 5, 10, tzinfo=_KST))

    assert isinstance(result, TWAPExecutionResult)
    assert captured["client"] is kis                                  # delegated to the session client
    schedule = captured["schedule"]
    assert schedule.symbol == "005930" and schedule.side == "buy"
    assert schedule.total_quantity == 100
    assert [s.quantity for s in schedule.slices] == [25, 25, 25, 25]  # 100 over 4 slices, evenly


@pytest.mark.parametrize("exchange", ["NXT", "UN"])
@pytest.mark.parametrize("over, start", [("1m", datetime(2099, 1, 5, 10, tzinfo=_KST)),
                                        ("invalid", datetime(2000, 1, 5, 10, tzinfo=_KST))])
def test_non_krx_twap_rejected_before_planner_or_runner(monkeypatch, exchange, over, start):
    from kis_trader.domestic import stock as module
    from kis_trader.domestic.stock import DomesticStock

    calls = []

    def planner(**kwargs):
        calls.append("planner")
        raise AssertionError("미지원 거래소의 TWAP 계획을 만들면 안 된다")

    def runner(*args, **kwargs):
        calls.append("runner")
        raise AssertionError("미지원 거래소의 TWAP 실행을 시작하면 안 된다")

    monkeypatch.setattr(module, "make_twap_schedule", planner)
    monkeypatch.setattr(module, "execute_twap", runner)
    with pytest.raises(KISUsageError, match="KRX"):
        DomesticStock(_Kis(), "005930", exchange=exchange).twap(
            side="buy", quantity=1, over=over, slices=1, start=start,
        )
    assert calls == []


@pytest.mark.parametrize("exchange", [None, "KRX", "NXT", "UN"])
def test_public_twap_preserves_krx_and_rejects_other_venues_without_side_effects(monkeypatch, exchange):
    from kis_trader.domestic import stock as module

    calls, claims, planned = [], [], []
    start = datetime(2026, 10, 20, 10, tzinfo=_KST)

    class RecordingTransport:
        def request(self, **kwargs):
            calls.append(kwargs)
            return RawResponse(rt_cd="0", msg_cd="accepted", msg1="synthetic",
                               body={"output": {"KRX_FWDG_ORD_ORGNO": "01", "ODNO": "0001", "ORD_TMD": "100000"}})

    def plan(**kwargs):
        planned.append(kwargs)
        return make_twap_schedule(**kwargs, now=start - timedelta(minutes=1))

    def execute(client, schedule):
        return execute_twap(client, schedule, now_fn=lambda: start, reconcile=False)

    monkeypatch.setattr(module, "make_twap_schedule", plan)
    monkeypatch.setattr(module, "execute_twap", execute)
    store = OrderStore()
    original_claim = store.try_claim

    def claim(*args, **kwargs):
        claims.append(args)
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(store, "try_claim", claim)
    with KISClient(app_key="k", app_secret="s", account="12345678-01", environment="real",
                   transport=RecordingTransport(), store=store, risk=RiskLimits(max_order_quantity=1)) as client:
        handle = client.domestic.stock("005930", exchange=exchange)
        if exchange in ("NXT", "UN"):
            with pytest.raises(KISUsageError, match="KRX"):
                handle.twap(side="buy", quantity=1, over="1m", slices=1, start=start)
            assert planned == claims == calls == []
        else:
            result = handle.twap(side="buy", quantity=1, over="1m", slices=1, start=start)
            assert len(result.outcomes) == 1
            assert len(planned) == len(claims) == len(calls) == 1
            assert calls[0]["method"] == "POST"
            assert calls[0]["body"]["EXCG_ID_DVSN_CD"] == "KRX"
