from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kis_trader.execution import TWAPExecutionResult

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
    handle = DomesticStock(kis, "005930", market="KRX")
    # start well in the future so the planner's past-guard/session-window accept it deterministically.
    result = handle.twap(side="buy", quantity=100, over="20m", slices=4,
                         start=datetime(2099, 1, 5, 10, tzinfo=_KST))

    assert isinstance(result, TWAPExecutionResult)
    assert captured["client"] is kis                                  # delegated to the session client
    schedule = captured["schedule"]
    assert schedule.symbol == "005930" and schedule.side == "buy"
    assert schedule.total_quantity == 100
    assert [s.quantity for s in schedule.slices] == [25, 25, 25, 25]  # 100 over 4 slices, evenly
