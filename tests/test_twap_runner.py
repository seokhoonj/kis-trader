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
from kis_trader.execution import (
    TWAPExecutionResult,
    TWAPSliceOutcome,
    execute_twap,
    make_twap_schedule,
)
from kis_trader.report import ExecutionReport, OrderStatus

_KST = timezone(timedelta(hours=9))


def _scheduled_at(hour, minute):
    return datetime(2026, 8, 31, hour, minute, tzinfo=_KST)


def _report(cid, filled, price, *, status=OrderStatus.FILLED):
    return ExecutionReport(
        client_order_id=cid, order_id="O" + cid, symbol="005930", side="buy",
        status=status, filled_quantity=Decimal(filled),
        average_price=None if price is None else Decimal(price), recorded_at=_scheduled_at(10, 0))


def _placed(cid):
    """place 현실: 접수(NEW)됐고 체결은 아직 0 -- 실제 체결은 이후 reconcile 로 확인한다."""
    return _report(cid, 0, None, status=OrderStatus.NEW)


_UNSET = object()


class _Stock:
    def __init__(self, log, *, fail_slices=(), timeout_slices=(), raise_exc=None, prices=None):
        self._log = log
        self._reject = set(fail_slices)      # 1-based slice indices -> OrderRejectedError
        self._timeout = set(timeout_slices)  # 1-based slice indices -> OrderTimeoutError
        self._raise = raise_exc
        self._prices = prices                # per-slice fill price override (list) or None
        self._n = 0
        self.placed: dict[str, tuple] = {}   # cid -> (체결수량, 단가) -- reconcile 가 읽는 '브로커 진실'

    def _place(self, action, quantity, client_order_id=None):
        self._n += 1
        self._log.append((action, quantity))
        if self._raise is not None:
            raise self._raise
        if self._n in self._reject:
            raise OrderRejectedError("거부", rt_cd="1", msg_cd="X", msg1="거부")
        if self._n in self._timeout:
            raise OrderTimeoutError("타임아웃", client_order_id=f"c{self._n}")
        cid = client_order_id or f"c{self._n}"
        price = 70000 if self._prices is None else self._prices[self._n - 1]
        self.placed[cid] = (Decimal(quantity), price)   # 브로커가 이 수량을 체결할 것(reconcile 가 확인)
        return _placed(cid)                              # place 는 NEW/0 만 돌려준다(현실)

    def buy(self, *, quantity, client_order_id=None):
        return self._place("buy", quantity, client_order_id)

    def sell(self, *, quantity, client_order_id=None):
        return self._place("sell", quantity, client_order_id)


class _Orders:
    """가짜 주문 네임스페이스. 기본은 stock.placed 에서 브로커 체결을 재구성해 돌려준다(refresh 재스캔
    모사). ``confirm`` 을 명시하면(``None`` 포함) 그 값을 돌려준다 -- None 은 '모호' 경로 테스트용."""

    def __init__(self, stock=None, *, confirm=_UNSET):
        self._stock = stock
        self._confirm = confirm

    def reconcile(self, cid, *, refresh=False):
        if self._confirm is not _UNSET:
            return self._confirm
        if self._stock is None:
            return None
        entry = self._stock.placed.get(cid)
        if entry is None:
            return None
        qty, price = entry
        return _report(cid, qty, price)


class _Kis:
    def __init__(self, stock, *, orders=None):
        self._stock = stock
        self.orders = orders if orders is not None else _Orders(stock)

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


def test_run_routes_buy_schedule_to_stock_buy():
    log = []
    execute_twap(_Kis(_Stock(log)), _sched(),
                 now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert [action for action, _ in log] == ["buy", "buy", "buy"]


def test_run_routes_sell_schedule_to_stock_sell():
    # 회귀: sell 스케줄은 모든 슬라이스를 stock.sell() 로 보낸다(buy 아님) -- runner 의 side 분기.
    log = []
    schedule = make_twap_schedule(symbol="005930", side="sell", quantity=100,
                                  duration="20m", slices=3, now=_scheduled_at(10, 0))
    execute_twap(_Kis(_Stock(log)), schedule,
                 now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert [action for action, _ in log] == ["sell", "sell", "sell"]


def test_run_rejects_invalid_side_without_placing_order():
    log = []
    schedule = _sched()
    object.__setattr__(schedule, "side", "hold")
    with pytest.raises(KISUsageError, match="buy/sell"):
        execute_twap(_Kis(_Stock(log)), schedule,
                     now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert log == []


def test_reconcile_read_failure_keeps_submitted_slice():
    # 재조회(읽기)가 일시적 KISError 를 내도 이미 접수된 슬라이스는 결과에 남아야 한다(폐기/크래시 금지).
    class _RaisingOrders:
        def reconcile(self, cid, *, refresh=False):
            raise KISUsageError("일시적 재조회 실패")

    log: list = []
    result = execute_twap(_Kis(_Stock(log), orders=_RaisingOrders()), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert len(log) == 3                                        # 세 슬라이스 모두 발주됨
    assert all(o.report is not None for o in result.outcomes)   # 미확정이어도 report 유지(유실 아님)
    assert result.submitted_quantity == 100
    # 재조회 실패 슬라이스는 '확정 성공'으로 집계되면 안 된다 -- client_order_id 를 실어
    # pending_reconcile_ids 에 넣어야 사후 재조회로 확인된다(안 그러면 미확정 물량 재주문 시 이중매수).
    assert len(result.pending_reconcile_ids) == 3


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
    assert result.filled_quantity == Decimal(66)         # 확정 체결만
    # 타임아웃 34주는 체결 불명이라 shortfall 이 아니라 pending 으로 분류된다 -- shortfall 을 재주문해도
    # 아직 살아 있을 수 있는 그 34주를 이중매수하지 않는다(reconcile 로 먼저 확정하라).
    assert result.pending_quantity == Decimal(34)
    assert result.shortfall == Decimal(0)                # 66 확정 + 34 pending = 100(목표) -> 미설명 갭 0
    assert result.outcomes[0].report is None and "타임아웃" in result.outcomes[0].error
    # 타임아웃 슬라이스는 client_order_id 를 유실하지 않고 실어 재조회 가능해야 한다(예전엔 str 로 뭉갰다).
    assert result.outcomes[0].client_order_id == "c1"
    assert result.pending_reconcile_ids == ("c1",)       # 사후 reconcile 대상
    # 거부 슬라이스는 재조회 불필요 -- client_order_id 없음(타임아웃과 구별).
    rejected = execute_twap(_Kis(_Stock([], fail_slices=(1,))), _sched(),
                            now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert rejected.outcomes[0].client_order_id is None and rejected.pending_reconcile_ids == ()


def test_run_reconcile_replaces_place_report():
    confirmed = _report("cX", 34, 71000)                 # a distinct confirmed report
    stock = _Stock([])
    result = execute_twap(_Kis(stock, orders=_Orders(confirm=confirmed)),
                          make_twap_schedule(symbol="005930", side="buy", quantity=34,
                                             duration="10m", slices=1, now=_scheduled_at(10, 0)),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.outcomes[0].report is confirmed        # reconcile snapshot won over the place report


def test_run_reconcile_disabled_marks_every_slice_pending():
    # reconcile=False 면 접수 직후 NEW/0 스냅샷만 있고 체결은 불명 -> 모든 슬라이스를 pending 으로 돌려
    # shortfall 에서 뺀다. '체결 0 확정'으로 집계하면 사용자가 shortfall(전량)을 재주문해 이중매수.
    stock = _Stock([])
    result = execute_twap(_Kis(stock), _sched(), reconcile=False,
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.filled_quantity == Decimal(0)              # 확정 체결 없음(전부 미확정)
    assert result.pending_quantity == Decimal(100)
    assert result.shortfall == Decimal(0)                    # 전량 pending -> 미설명 갭 0(재주문 유발 안 함)
    assert len(result.pending_reconcile_ids) == 3


def test_run_reconcile_ambiguous_none_marks_slice_pending():
    # refresh 재조회가 0건(모호 -> None)이면 체결 여부 불명 -> pending(확정 0체결로 집계하지 않는다).
    stock = _Stock([])
    result = execute_twap(_Kis(stock, orders=_Orders(confirm=None)), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.filled_quantity == Decimal(0)
    assert result.pending_quantity == Decimal(100)
    assert len(result.pending_reconcile_ids) == 3


def test_run_reconcile_nonterminal_report_marks_pending():
    # refresh 재조회가 비종료(NEW) 리포트를 돌려주면 -- 주문이 아직 살아 있어 체결 불명 -- '확정'으로
    # 집계하지 않고 pending 으로 돌린다. 집계하면 미체결 잔량이 shortfall 이 돼 재주문 시 이중매수.
    stock = _Stock([])
    new0 = _report("cX", 0, None, status=OrderStatus.NEW)
    result = execute_twap(_Kis(stock, orders=_Orders(confirm=new0)), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.filled_quantity == Decimal(0)        # 비종료라 확정 체결로 세지 않는다
    assert result.pending_quantity == Decimal(100)
    assert len(result.pending_reconcile_ids) == 3


def test_run_reconcile_partial_fill_marks_pending_not_shortfall():
    # 부분체결(살아있는 잔량)도 종료가 아니므로 pending -- 미체결 잔량을 shortfall 로 집계하면 아직
    # 체결될 수 있는 그 물량을 재주문해 이중매수한다.
    stock = _Stock([])
    partial = _report("cX", 5, 70000, status=OrderStatus.PARTIALLY_FILLED)
    result = execute_twap(_Kis(stock, orders=_Orders(confirm=partial)), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.filled_quantity == Decimal(0)        # 부분체결은 확정 집계에서 제외
    assert result.pending_quantity == Decimal(100)     # 슬라이스 전량 pending(잔량 재주문 금지)
    assert result.shortfall == Decimal(0)


def test_run_reconcile_terminal_rejected_counts_as_shortfall():
    # 종료상태(거부)는 주문이 죽어 미체결 잔량이 '진짜' shortfall 이다(재주문 안전) -- pending 아님.
    stock = _Stock([])
    rejected = _report("cX", 0, None, status=OrderStatus.REJECTED)
    result = execute_twap(_Kis(stock, orders=_Orders(confirm=rejected)), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.pending_reconcile_ids == ()          # 종료라 pending 아님
    assert result.shortfall == Decimal(100)            # 죽은 주문 -> 전량 진짜 미달


def test_run_systemic_error_propagates_with_partial_note():
    log = []

    class _FailAfterFirst(_Stock):
        def buy(self, *, quantity, client_order_id=None):
            if self._n >= 1:
                raise AccountNotOrderableError("조회전용")
            return super().buy(quantity=quantity, client_order_id=client_order_id)

    with pytest.raises(AccountNotOrderableError) as excinfo:
        execute_twap(_Kis(_FailAfterFirst(log)), _sched(),
                     now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert any("TWAP 부분 실행됨" in note for note in excinfo.value.__notes__)


def test_run_keyboard_interrupt_returns_partial():
    log = []

    class _Interrupting(_Stock):
        def buy(self, *, quantity, client_order_id=None):
            if self._n >= 1:                             # interrupt at the second slice
                raise KeyboardInterrupt
            return super().buy(quantity=quantity, client_order_id=client_order_id)

    result = execute_twap(_Kis(_Interrupting(log)), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.submitted_quantity == 34               # first slice only
    assert len(result.outcomes) == 2                      # interrupted second slice is pending
    assert result.pending_quantity == Decimal(33)
    assert result.interrupted is True
    assert result.outcome == "interrupted"


def test_run_keyboard_interrupt_after_broker_acceptance_marks_slice_pending():
    broker_live: list[tuple[str, int]] = []

    class _InterruptAfterAcceptance(_Stock):
        def buy(self, *, quantity, client_order_id=None):
            assert client_order_id is not None
            broker_live.append((client_order_id, quantity))
            raise KeyboardInterrupt

    result = execute_twap(_Kis(_InterruptAfterAcceptance([])), _sched(),
                          now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda d: None)
    assert result.pending_reconcile_ids == (broker_live[0][0],)
    assert result.pending_quantity == Decimal(34)
    assert result.interrupted is True
    assert result.outcome == "interrupted"
    assert len(broker_live) == 1
    assert result.shortfall == Decimal(66)


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


@pytest.mark.parametrize("case,expected", [
    ("complete", "completed"), ("priceless", "completed"), ("missing", "incomplete"),
    ("error", "incomplete"), ("no_report", "incomplete"), ("nonterminal", "incomplete"),
    ("partial", "incomplete"), ("pending", "unknown"), ("pending_error", "unknown"),
    ("canceled", "incomplete"), ("expired", "incomplete"), ("rejected", "incomplete"),
])
def test_result_execution_outcome(case, expected):
    schedule = _sched()
    outcomes = [TWAPSliceOutcome(entry.at, entry.quantity,
                _report(str(index), entry.quantity, None if case == "priceless" else 100), None)
                for index, entry in enumerate(schedule.slices)]
    first = schedule.slices[0]
    if case == "missing":
        outcomes.pop()
    elif case in ("error", "no_report", "pending", "pending_error"):
        outcomes[0] = TWAPSliceOutcome(first.at, first.quantity, None,
            "synthetic" if case in ("error", "pending_error") else None,
            "pending-id" if case.startswith("pending") else None)
    elif case in ("nonterminal", "partial", "canceled", "expired", "rejected"):
        status = {"nonterminal": OrderStatus.NEW, "partial": OrderStatus.FILLED,
                  "canceled": OrderStatus.CANCELED, "expired": OrderStatus.EXPIRED,
                  "rejected": OrderStatus.REJECTED}[case]
        outcomes[0] = TWAPSliceOutcome(first.at, first.quantity,
            _report("first", first.quantity if case == "nonterminal" else 1, 100, status=status), None)
    result = TWAPExecutionResult(schedule, tuple(outcomes))
    assert result.outcome == expected
    assert result.interrupted is False
    if case.startswith("pending"):
        assert result.shortfall == 0
        assert result.pending_reconcile_ids == ("pending-id",)


def test_execution_interrupt_before_submission_has_explicit_outcome():
    log = []
    def interrupt(delay):
        raise KeyboardInterrupt
    result = execute_twap(_Kis(_Stock(log)), _sched(),
        now_fn=lambda: _scheduled_at(9, 0), sleep_fn=interrupt)
    assert result.interrupted is True
    assert result.outcome == "interrupted"
    assert result.outcomes == ()
    assert result.pending_reconcile_ids == ()
    assert log == []


def test_execution_interrupt_during_reconcile_keeps_pending_id():
    log = []
    class Orders:
        def reconcile(self, cid, *, refresh=False):
            raise KeyboardInterrupt
    result = execute_twap(_Kis(_Stock(log), orders=Orders()), _sched(),
        now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda delay: None)
    assert result.interrupted is True
    assert result.outcome == "interrupted"
    assert len(result.outcomes) == 1
    assert len(result.pending_reconcile_ids) == 1
    assert log == [("buy", 34)]


def test_interrupt_after_append_does_not_duplicate_pending_outcome():
    import inspect
    import sys
    lines, first_line = inspect.getsourcelines(execute_twap)
    interrupt_line = max(first_line + index for index, line in enumerate(lines)
                         if line.strip() == "in_flight = None")
    previous_trace = sys.gettrace()
    triggered = []
    def trace(frame, event, argument):
        if frame.f_code is execute_twap.__code__ and event == "line" and frame.f_lineno == interrupt_line:
            triggered.append(True)
            sys.settrace(None)
            raise KeyboardInterrupt
        return trace
    try:
        sys.settrace(trace)
        result = execute_twap(_Kis(_Stock([])), _sched(),
            now_fn=lambda: _scheduled_at(15, 0), sleep_fn=lambda delay: None)
    finally:
        sys.settrace(previous_trace)
    assert triggered == [True]
    assert result.interrupted is True
    assert result.outcome == "interrupted"
    assert len(result.outcomes) == 1
    assert result.filled_quantity == 34
    assert result.pending_reconcile_ids == ()
    assert result.shortfall == 66
