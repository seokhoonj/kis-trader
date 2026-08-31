from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader.errors import AccountNotOrderableError, OrderRejectedError
from kis_trader.execution import TwapExecutionResult, build_twap_schedule, run_twap
from kis_trader.report import ExecutionReport, OrderStatus

_KST = timezone(timedelta(hours=9))


def _at(h, m):
    return datetime(2026, 8, 31, h, m, tzinfo=_KST)


def _report(cid, filled, price):
    return ExecutionReport(
        client_order_id=cid, order_id="O" + cid, symbol="005930", side="buy",
        status=OrderStatus.FILLED, filled_quantity=Decimal(filled),
        average_price=Decimal(price), recorded_at=_at(10, 0))


class _Stock:
    def __init__(self, log, *, fail_slices=(), raise_exc=None):
        self._log = log
        self._fail = set(fail_slices)   # 1-based slice indices that raise OrderRejectedError
        self._raise = raise_exc
        self._n = 0

    def buy(self, *, quantity):
        self._n += 1
        self._log.append(("buy", quantity))
        if self._raise is not None:
            raise self._raise
        if self._n in self._fail:
            raise OrderRejectedError("거부", rt_cd="1", msg_cd="X", msg1="거부")
        return _report(f"c{self._n}", quantity, 70000)

    def sell(self, *, quantity):
        self._n += 1
        self._log.append(("sell", quantity))
        return _report(f"c{self._n}", quantity, 70000)


class _Orders:
    def reconcile(self, cid):
        return None   # place 리포트를 그대로 쓰게 한다


class _Kis:
    def __init__(self, stock):
        self._stock = stock
        self.orders = _Orders()

    class _Dom:
        def __init__(self, stock):
            self._stock = stock

        def stock(self, symbol):
            return self._stock

    @property
    def domestic(self):
        return _Kis._Dom(self._stock)


def _sched():
    return build_twap_schedule(symbol="005930", side="buy", quantity=100,
                               duration="20m", slices=3, now=_at(10, 0))


def test_run_places_each_slice_at_its_time_no_early_fire():
    log = []
    stock = _Stock(log)
    sleeps = []
    # 시각을 슬라이스 시각에 맞춰 순차 진행: 각 슬라이스마다 now 가 정확히 그 시각.
    times = iter([_at(10, 0), _at(10, 10), _at(10, 20)])
    now_holder = {"t": _at(10, 0)}

    def now_fn():
        return now_holder["t"]

    def sleep_fn(delay):
        sleeps.append(delay)
        now_holder["t"] = next(times)   # 자면 다음 슬라이스 시각으로 점프

    # 첫 슬라이스는 now==at 라 즉시, 이후는 sleep 후 점프
    now_holder["t"] = _at(10, 0)
    result = run_twap(_Kis(stock), _sched(), now_fn=now_fn, sleep_fn=sleep_fn)
    assert [q for _, q in log] == [34, 33, 33]        # 세 슬라이스 모두 발주
    assert isinstance(result, TwapExecutionResult)
    assert result.filled_quantity == Decimal(100)
    assert result.shortfall == Decimal(0)


def test_run_partial_failure_continues_and_reports_shortfall():
    log = []
    stock = _Stock(log, fail_slices=(2,))            # 두 번째 슬라이스 거부
    result = run_twap(_Kis(stock), _sched(),
                      now_fn=lambda: _at(15, 0), sleep_fn=lambda d: None)
    assert len(log) == 3                              # 세 슬라이스 모두 시도
    assert result.submitted_quantity == 67           # 34 + 33 (거부된 33 제외)
    assert result.filled_quantity == Decimal(67)
    assert result.shortfall == Decimal(33)
    assert result.outcomes[1].report is None and result.outcomes[1].error is not None


def test_run_systemic_error_propagates():
    stock = _Stock([], raise_exc=AccountNotOrderableError("조회전용"))
    with pytest.raises(AccountNotOrderableError):
        run_twap(_Kis(stock), _sched(), now_fn=lambda: _at(15, 0), sleep_fn=lambda d: None)


def test_run_keyboard_interrupt_returns_partial():
    log = []

    class _Interrupting(_Stock):
        def buy(self, *, quantity):
            if self._n >= 1:                          # 두 번째 슬라이스에서 중단
                raise KeyboardInterrupt
            return super().buy(quantity=quantity)

    stock = _Interrupting(log)
    result = run_twap(_Kis(stock), _sched(),
                      now_fn=lambda: _at(15, 0), sleep_fn=lambda d: None)
    assert result.submitted_quantity == 34            # 첫 슬라이스만
    assert len(result.outcomes) == 1


def test_average_price_weighted():
    result = run_twap(_Kis(_Stock([])), _sched(),
                      now_fn=lambda: _at(15, 0), sleep_fn=lambda d: None)
    assert result.average_price == Decimal(70000)     # 모두 70000
