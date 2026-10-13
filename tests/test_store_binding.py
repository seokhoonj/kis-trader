"""OrderStore.binding_for -- (리포트, 지문)의 원자 스냅샷(머니패스 TOCTOU 가드).

정정/취소는 잔량(지문 수량 - 리포트 체결량)을 (리포트, 지문) 쌍에서 계산해 와이어에 싣는다.
둘을 각각 읽으면 그 사이 동시 리바인드(정정)가 끼어들어 세대가 어긋난 쌍(옛 지문 + 새 리포트)을
볼 수 있다. binding_for 는 한 락으로 둘을 함께 읽어 쌍이 항상 같은 세대임을 보장한다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kis_trader.order import Order
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.store import Binding, OrderStore

_KST = timezone(timedelta(hours=9))
_CID = "20240101-bind01"


def _report(order_id: str) -> ExecutionReport:
    return ExecutionReport(
        client_order_id=_CID, order_id=order_id, symbol="005930", side="buy",
        status=OrderStatus.PENDING_REPLACE, filled_quantity=Decimal(0), average_price=None,
        recorded_at=datetime(2024, 6, 3, 10, 0, tzinfo=_KST),
    )


def _fingerprint(quantity: int):
    # client_order_id 를 _CID 로 고정해 record 가 _CID 아래에 바인딩하게 한다.
    return Order(symbol="005930", side="buy", order_type="limit", quantity=quantity,
                 limit_price=70000, client_order_id=_CID).fingerprint


# 두 세대: 주문번호 A<->수량 10, 주문번호 B<->수량 20 이 짝. 어긋난 쌍은 수량≠기대값으로 드러난다.
_GEN_A = Binding(_report("A"), _fingerprint(10))
_GEN_B = Binding(_report("B"), _fingerprint(20))
_EXPECTED_QTY = {"A": "10", "B": "20"}


def test_binding_for_none_when_unknown_or_in_flight_only():
    store = OrderStore()
    assert store.binding_for(_CID) is None            # 미기록
    store.try_claim(_CID, _fingerprint(10))           # in-flight (지문만, 리포트 없음)
    assert store.binding_for(_CID) is None            # 리포트 없으면 None (확정 안 됨)


def test_binding_for_returns_recorded_pair():
    store = OrderStore()
    store.record(_GEN_A.report, _GEN_A.fingerprint)
    binding = store.binding_for(_CID)
    assert binding is not None
    assert binding.report.order_id == "A"
    assert binding.fingerprint.quantity == "10"


def test_binding_for_reads_pair_atomically_under_concurrent_rebind():
    # 한 스레드가 cid 를 두 세대 사이로 반복 리바인드하는 동안, 다른 스레드가 binding_for 로
    # 읽는 쌍은 항상 같은 세대여야 한다(옛 지문 + 새 리포트 같은 찢긴 쌍이 없어야). binding_for 가
    # 한 락 스냅샷이 아니면(회귀) 찢긴 쌍이 관측돼 이 테스트가 깨진다.
    store = OrderStore()
    store.record(_GEN_A.report, _GEN_A.fingerprint)
    stop = threading.Event()
    torn: list[tuple[str, str]] = []
    start = threading.Barrier(2)

    def writer() -> None:
        start.wait()
        gen = _GEN_B
        for _ in range(20000):
            store.record(gen.report, gen.fingerprint)
            gen = _GEN_A if gen is _GEN_B else _GEN_B
        stop.set()

    def reader() -> None:
        start.wait()
        while not stop.is_set():
            binding = store.binding_for(_CID)
            if binding is None or binding.report.order_id is None:
                continue
            if binding.fingerprint.quantity != _EXPECTED_QTY[binding.report.order_id]:
                torn.append((binding.report.order_id, binding.fingerprint.quantity))

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    assert not torn, f"찢긴 (리포트, 지문) 쌍 관측: {torn[:5]}"
