from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader.errors import (
    AccountNotOrderableError,
    KISUsageError,
    OrderRejectedError,
    OrderTimeoutError,
)
from kis_trader.execution import TWAPExecutionResult, execute_twap, make_twap_schedule
from kis_trader.report import ExecutionReport, OrderStatus

_KST = timezone(timedelta(hours=9))


def _scheduled_at(hour, minute):
    return datetime(2026, 8, 31, hour, minute, tzinfo=_KST)


def _report(cid, filled, price):
    return ExecutionReport(
        client_order_id=cid, order_id="O" + cid, symbol="005930", side="buy",
        status=OrderStatus.FILLED, filled_quantity=Decimal(filled),
        average_price=None if price is None else Decimal(price), recorded_at=_scheduled_at(10, 0))


class _Stock:
    def __init__(self, log, *, fail_slices=(), timeout_slices=(), raise_exc=None, prices=None):
        self._log = log
        self._reject = set(fail_slices)      # 1-based slice indices -> OrderRejectedError
        self._timeout = set(timeout_slices)  # 1-based slice indices -> OrderTimeoutError
        self._raise = raise_exc
        self._prices = prices                # per-slice fill price override (list) or None
        self._n = 0

    def buy(self, *, quantity):
        self._n += 1
        self._log.append(("buy", quantity))
        if self._raise is not None:
            raise self._raise
        if self._n in self._reject:
            raise OrderRejectedError("거부", rt_cd="1", msg_cd="X", msg1="거부")
        if self._n in self._timeout:
            raise OrderTimeoutError("타임아웃", client_order_id=f"c{self._n}")
        price = 70000 if self._prices is None else self._prices[self._n - 1]
        return _report(f"c{self._n}", quantity, price)

    def sell(self, *, quantity):
        self._n += 1
        self._log.append(("sell", quantity))
        return _report(f"c{self._n}", quantity, 70000)


class _Orders:
    def __init__(self, confirm=None):
        self._confirm = confirm          # reconcile return value (None -> keep place report)

    def reconcile(self, cid):
        return self._confirm


class _Kis:
    def __init__(self, stock, *, orders=None):
        self._stock = stock
        self.orders = orders or _Orders()

    class _Dom:
        def __init__(self, stock):
            self._stock = stock

        def stock(self, symbol):
            return self._stock

    @property
    def domestic(self):
        return _Kis._Dom(self._stock)


def _sched():
    return make_twap_schedule(symbol="005930", side="buy", quantity=100,
                              duration="20m", slices=3, now=_scheduled_at(10, 0))


def test_run_places_each_slice_at_its_time_and_asserts_sleeps():
    log, sleeps = [], []
    stock = _Stock(log)
    now_holder = {"t": _scheduled_at(10, 0)}

    def now_fn():
        return now_holder["t"]

    def sleep_fn(delay):                     # advance the injected clock by exactly the requested delay
        sleeps.append(delay)
        now_holder["t"] = now_holder["t"] + timedelta(seconds=delay)

    result = execute_twap(_Kis(stock), _sched(), now_fn=now_fn, sleep_fn=sleep_fn)
    assert [q for _, q in log] == [34, 33, 33]           # all three slices placed
    assert sleeps == [400.0, 400.0]                       # 20m/3 = 6m40s = 400s between slices
    assert isinstance(result, TWAPExecutionResult)
    assert result.filled_quantity == Decimal(100)
    assert result.shortfall == Decimal(0)


def test_run_partial_rejection_continues_and_reports_shortfall():
    log = []
    stock = _Stock(log, fail_slices=(2,))                # second slice rejected
    result = execute_twap(_Kis(stock), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert len(log) == 3                                  # all three attempted
    assert result.submitted_quantity == 67               # 34 + 33 (rejected 33 excluded)
    assert result.filled_quantity == Decimal(67)
    assert result.shortfall == Decimal(33)
    assert result.outcomes[1].report is None and result.outcomes[1].error is not None


def test_run_partial_timeout_continues_and_reports_shortfall():
    log = []
    stock = _Stock(log, timeout_slices=(1,))             # first slice times out
    result = execute_twap(_Kis(stock), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert len(log) == 3
    assert result.submitted_quantity == 66               # 33 + 33 (timed-out 34 excluded)
    assert result.shortfall == Decimal(34)
    assert result.outcomes[0].report is None and "타임아웃" in result.outcomes[0].error


def test_run_reconcile_replaces_place_report():
    confirmed = _report("cX", 34, 71000)                 # a distinct confirmed report
    stock = _Stock([])
    result = execute_twap(_Kis(stock, orders=_Orders(confirm=confirmed)),
                          make_twap_schedule(symbol="005930", side="buy", quantity=34,
                                             duration="10m", slices=1, now=_scheduled_at(10, 0)),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.outcomes[0].report is confirmed        # reconcile snapshot won over the place report


def test_run_systemic_error_propagates_with_partial_note():
    log = []

    class _FailAfterFirst(_Stock):
        def buy(self, *, quantity):
            if self._n >= 1:
                raise AccountNotOrderableError("조회전용")
            return super().buy(quantity=quantity)

    with pytest.raises(AccountNotOrderableError) as excinfo:
        execute_twap(_Kis(_FailAfterFirst(log)), _sched(),
                     now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert any("TWAP 부분 실행됨" in note for note in excinfo.value.__notes__)


def test_run_keyboard_interrupt_returns_partial():
    log = []

    class _Interrupting(_Stock):
        def buy(self, *, quantity):
            if self._n >= 1:                             # interrupt at the second slice
                raise KeyboardInterrupt
            return super().buy(quantity=quantity)

    result = execute_twap(_Kis(_Interrupting(log)), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.submitted_quantity == 34               # first slice only
    assert len(result.outcomes) == 1


def test_run_raises_when_clock_does_not_advance():
    # frozen now_fn + no-op sleep_fn would spin forever; the runner must fail loud instead.
    with pytest.raises(KISUsageError, match="전진"):
        execute_twap(_Kis(_Stock([])),
                     make_twap_schedule(symbol="005930", side="buy", quantity=2, duration="10m",
                                        slices=2, start=_scheduled_at(13, 0), now=_scheduled_at(9, 0)),
                     now_fn=lambda: _scheduled_at(9, 0), sleep_fn=lambda d: None)


def test_average_price_is_fill_weighted_over_different_prices():
    stock = _Stock([], prices=[100, 200, 200])           # 34@100 + 33@200 + 33@200
    result = execute_twap(_Kis(stock), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    expected = (Decimal(34) * 100 + Decimal(33) * 200 + Decimal(33) * 200) / Decimal(100)
    assert result.average_price == expected              # != a plain mean of 100/200/200


def test_average_price_excludes_priceless_fills_from_both_numerator_and_denominator():
    # a filled slice with average_price=None must not drag the weighted mean down.
    stock = _Stock([], prices=[70000, None, 70000])
    result = execute_twap(_Kis(stock), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.average_price == Decimal(70000)        # priced fills only, not understated
