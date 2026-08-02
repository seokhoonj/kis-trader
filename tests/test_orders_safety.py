"""주문 안전 코어 테스트 -- 타입->가격 강제, 원자적 멱등 dedup(동시성 포함), 지문 충돌
거부, 쓰기 재시도 금지, 타임아웃 후 재조회(reconcile), 접수 거부, 퇴직연금 차단, 손상
저장소 fail-closed, Decimal 와이어 포매팅. 전부 네트워크 없이 가짜 전송으로 검증한다.
"""

from __future__ import annotations

import re
import threading
import time
from decimal import Decimal

import pytest

from kis_openapi.errors import (
    AccountNotOrderable,
    KisError,
    KisUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
)
from kis_openapi.orders import Order, OrderStatus, OrderStore
from kis_openapi.orders.facade import Orders
from kis_openapi.orders.store import ClaimOutcome
from kis_openapi.transport import RawResponse, TransportTimeout

_ORDER_CASH = "/uapi/domestic-stock/v1/trading/order-cash"
_DAILY_CCLD = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"


class FakeTransport:
    """테스트용 전송. 기본 응답/예외, 순차 응답(``seq``), 경로별 응답(``by_path``),
    호출 지연(``delay``)을 지원하고 모든 호출을 기록한다."""

    def __init__(self, *, response=None, raises=None, seq=None, by_path=None, delay=0.0):
        self.response = response
        self.raises = raises
        self.seq = list(seq) if seq is not None else None
        self.by_path = by_path or {}
        self.delay = delay
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "body": body, "idempotent": idempotent})
        if self.delay:
            time.sleep(self.delay)
        if path in self.by_path:
            outcome = self.by_path[path]
            if isinstance(outcome, list):        # 경로별 순차 응답(페이지네이션 등)
                outcome = outcome.pop(0)
        elif self.seq is not None:
            outcome = self.seq.pop(0)
        elif self.raises is not None:
            outcome = self.raises
        else:
            outcome = self.response
        if isinstance(outcome, BaseException):
            raise outcome
        assert outcome is not None, "FakeTransport 에 응답을 줘야 한다"
        return outcome


ACCEPTED = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                       body={"output": {"ODNO": "0000117057", "ORD_TMD": "121052"}})
REJECTED = RawResponse(rt_cd="1", msg_cd="APBK1234", msg1="주문가능금액 부족", body={})
CCLD_FILLED = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="조회 완료", body={"output1": [
    {"odno": "0000117057", "pdno": "005930", "sll_buy_dvsn_cd": "02", "ord_dvsn_cd": "00",
     "ord_qty": "10", "ord_unpr": "70000", "tot_ccld_qty": "10", "avg_prvs": "70000"},
]})
CCLD_EMPTY = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="조회 완료", body={"output1": []})


def ccld_row(**over):
    row = {"odno": "9", "pdno": "005930", "sll_buy_dvsn_cd": "02", "ord_dvsn_cd": "00",
           "ord_qty": "10", "ord_unpr": "70000", "tot_ccld_qty": "0"}
    row.update(over)
    return RawResponse(rt_cd="0", msg_cd="0", msg1="ok", body={"output1": [row]})


def make_orders(transport, *, orderable=True, path=None):
    store = OrderStore(path=path)
    return Orders(transport, store, cano="12345678", product_code="01", orderable=orderable)


def limit_buy(coid=None):
    kw = {"client_order_id": coid} if coid else {}
    return Order.limit("005930", side="buy", quantity=10, limit_price=70000, **kw)


# --- Order 검증 -------------------------------------------------------
def test_limit_requires_price():
    with pytest.raises(KisUsageError):
        Order(symbol="005930", side="buy", order_type="limit", quantity=1)


def test_market_rejects_price():
    with pytest.raises(KisUsageError):
        Order(symbol="005930", side="buy", order_type="market", quantity=1, limit_price=100)


def test_stop_limit_accepts_both_positive_prices():
    o = Order.stop_limit("005930", side="sell", quantity=3, limit_price=69000, stop_price=69500)
    assert o.order_type == "stop_limit" and o.limit_price == 69000 and o.stop_price == 69500


def test_stop_rejects_zero_stop_price():
    with pytest.raises(KisUsageError):
        Order.stop("005930", side="sell", quantity=3, stop_price=0)


def test_quantity_must_be_positive():
    with pytest.raises(KisUsageError):
        Order.market("005930", side="buy", quantity=0)


def test_unknown_side_is_rejected():
    with pytest.raises(KisUsageError):
        Order(symbol="005930", side="long", order_type="market", quantity=1)  # type: ignore[arg-type]


def test_unknown_time_in_force_is_rejected():
    with pytest.raises(KisUsageError):
        Order.market("005930", side="buy", quantity=1, time_in_force="always")


def test_direct_construction_coerces_int_quantity_to_decimal():
    # 직접 Order(...) 로 int 를 줘도 Decimal 로 강제돼 와이어에서 크래시하지 않는다.
    o = Order(symbol="005930", side="buy", order_type="market", quantity=1)
    assert isinstance(o.quantity, Decimal) and o.quantity == Decimal(1)


def test_client_order_id_shape_and_uniqueness():
    a = Order.market("005930", side="buy", quantity=1)
    b = Order.market("005930", side="buy", quantity=1)
    assert re.fullmatch(r"\d{8}-[0-9a-f]{16}", a.client_order_id)
    assert a.client_order_id != b.client_order_id


# --- place: 정상 접수 --------------------------------------------------
def test_place_accepted_records_report():
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t)
    order = limit_buy()
    report = orders.place(order)
    assert report.status is OrderStatus.NEW
    assert report.order_id == "0000117057"
    assert report.client_order_id == order.client_order_id
    assert len(t.calls) == 1
    assert t.calls[0]["tr_id"] == "TTTC0012U"           # 실전 매수
    assert t.calls[0]["body"]["ORD_DVSN"] == "00"       # 지정가
    assert t.calls[0]["body"]["ORD_UNPR"] == "70000"


def test_market_order_sends_zero_price_and_market_dvsn():
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t)
    orders.sell("005930", quantity=5)                   # 편의 진입점(시장가 매도)
    assert t.calls[0]["tr_id"] == "TTTC0011U"           # 실전 매도
    assert t.calls[0]["body"]["ORD_DVSN"] == "01"       # 시장가
    assert t.calls[0]["body"]["ORD_UNPR"] == "0"


def test_decimal_exponent_is_sent_as_plain_digits():
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t)
    orders.place(Order.limit("005930", side="buy",
                             quantity=Decimal("1E+3"), limit_price=Decimal("7E+4")))
    assert t.calls[0]["body"]["ORD_QTY"] == "1000"      # 지수표기 아님
    assert t.calls[0]["body"]["ORD_UNPR"] == "70000"


# --- place: 멱등 dedup + 지문 --------------------------------------------
def test_duplicate_place_returns_prior_without_resending():
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t)
    order = limit_buy()
    first = orders.place(order)
    second = orders.place(order)                         # 같은 client_order_id 재요청
    assert second == first
    assert len(t.calls) == 1                             # 재전송 안 함


def test_same_client_order_id_with_different_order_is_rejected():
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t)
    orders.place(limit_buy(coid="FIXED"))
    with pytest.raises(KisUsageError):                  # 같은 id, 다른 주문 -> 충돌
        orders.place(Order.limit("000660", side="sell", quantity=2,
                                 limit_price=100000, client_order_id="FIXED"))
    assert len(t.calls) == 1                             # 두 번째는 전송 안 됨


def test_in_flight_client_order_id_reused_for_different_order_is_rejected():
    # 완료가 아니라 in-flight(타임아웃) 상태에서도 지문 충돌은 거부돼야 한다.
    t = FakeTransport(raises=TransportTimeout("t"))
    orders = make_orders(t)
    with pytest.raises(OrderTimeoutError):
        orders.place(limit_buy(coid="INF"))
    with pytest.raises(KisUsageError):
        orders.place(Order.limit("000660", side="sell", quantity=2,
                                 limit_price=100000, client_order_id="INF"))
    assert len(t.calls) == 1


def test_fingerprint_is_wire_canonical_so_10_equals_1e1():
    # 와이어가 같은 주문(ORD_QTY "10")은 지문도 같아야 멱등 판정이 깨지지 않는다.
    a = Order.limit("005930", side="buy", quantity=Decimal("1E1"), limit_price=Decimal("7E4"),
                    client_order_id="SAME")
    b = Order.limit("005930", side="buy", quantity=10, limit_price=70000, client_order_id="SAME")
    assert a.fingerprint == b.fingerprint
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t)
    orders.place(a)
    replay = orders.place(b)                             # 같은 주문 -> replay, 충돌 아님
    assert replay.order_id == "0000117057"
    assert len(t.calls) == 1


def test_try_claim_performs_check_and_claim_under_the_mutex():
    # 결정적 가드: try_claim 이 락 안에서 판정+확보한다. 락을 제거하면 entered 가 비어 실패.
    store = OrderStore()
    real_lock = store._lock
    entered = []

    class SpyLock:
        def __enter__(self):
            entered.append(1)
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)

    store._lock = SpyLock()
    store.try_claim("X", limit_buy(coid="X").fingerprint)
    assert entered == [1]                                    # 정확히 한 번, 락 안에서


def test_try_claim_is_atomic_under_contention():
    # 경합 스트레스: 두 스레드가 barrier로 동시에 try_claim 진입. 원자적 claim이면 정확히
    # 하나는 CLAIMED, 하나는 IN_FLIGHT. (락 제거를 결정적으로 잡는 가드는 아래 mutex-spy
    # 테스트이고, 이건 그 위의 실제 스레드 경합 확인이다.)
    store = OrderStore()
    fingerprint = limit_buy(coid="X").fingerprint
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def claim():
        barrier.wait()
        outcome, _ = store.try_claim("X", fingerprint)
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert set(outcomes) == {ClaimOutcome.CLAIMED, ClaimOutcome.IN_FLIGHT}  # 정확히 하나씩


def test_concurrent_place_same_id_sends_at_most_once():
    t = FakeTransport(response=ACCEPTED, delay=0.02)
    orders = make_orders(t)
    order = limit_buy(coid="RACE")
    results: list[object] = []
    lock = threading.Lock()

    def place_order():
        try:
            r: object = orders.place(order)
        except Exception as e:  # noqa: BLE001 -- 경합 패자의 예외를 수집
            r = e
        with lock:
            results.append(r)

    threads = [threading.Thread(target=place_order) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(t.calls) == 1                              # 이중 전송 없음
    reports = [r for r in results if hasattr(r, "order_id")]
    assert reports and all(r.order_id == "0000117057" for r in reports)


# --- place: 쓰기 타임아웃 재시도 금지 + 재조회 ------------------------
def test_write_timeout_raises_and_does_not_retry():
    t = FakeTransport(raises=TransportTimeout("network timeout"))
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError) as exc:
        orders.place(order)
    assert exc.value.client_order_id == order.client_order_id
    assert len(t.calls) == 1                             # 단 한 번, 재시도 없음
    assert t.calls[0]["idempotent"] is False


def test_after_timeout_resend_is_blocked_pending_reconcile():
    t = FakeTransport(raises=TransportTimeout("timeout"))
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    with pytest.raises(KisUsageError):                  # in-flight -> 재전송 거부
        orders.place(order)
    assert len(t.calls) == 1


def test_timeout_then_reconcile_confirms_fill():
    # place 는 타임아웃, 이어진 reconcile 은 일별체결조회를 스캔해 실제 체결을 확정한다.
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: CCLD_FILLED})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    report = orders.reconcile(order.client_order_id)
    assert report is not None
    assert report.status is OrderStatus.FILLED
    assert report.order_id == "0000117057"
    assert report.filled_quantity == Decimal(10)
    # 확정됐으니 재요청은 그 리포트를 replay(재전송 없음)
    assert orders.reconcile(order.client_order_id) == report


def test_reconcile_empty_scan_keeps_order_blocked_delayed_acceptance():
    # 핵심 안전 규약: 빈 스캔(반영 지연일 수 있음)을 '미접수'로 단정하지 않는다.
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: CCLD_EMPTY})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    assert orders.reconcile(order.client_order_id) is None   # 미확인(재전송 금지)
    # 지연 접수됐다가 늦게 뜬 상황을 흉내: 그 사이 재전송 시도는 여전히 차단돼야 한다.
    with pytest.raises(KisUsageError):
        orders.place(order)
    assert len(t.calls) == 2                                  # POST 1 + 재조회 1, 재전송 없음


def test_reconcile_error_response_keeps_order_blocked():
    error = RawResponse(rt_cd="1", msg_cd="EGW00201", msg1="유량 초과", body={})
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: error})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    with pytest.raises(KisError):                             # 에러를 '미접수'로 오인하지 않음
        orders.reconcile(order.client_order_id)
    with pytest.raises(KisUsageError):                        # 여전히 차단
        orders.place(order)


def test_reconcile_multiple_matches_requires_manual_resolution():
    row = {"pdno": "005930", "sll_buy_dvsn_cd": "02", "ord_dvsn_cd": "00",
           "ord_qty": "10", "ord_unpr": "70000", "tot_ccld_qty": "0"}
    two = RawResponse(rt_cd="0", msg_cd="0", msg1="ok",
                      body={"output1": [{**row, "odno": "1"}, {**row, "odno": "2"}]})
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: two})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    with pytest.raises(KisError):                            # 모호 -> 자동 확정 안 함
        orders.reconcile(order.client_order_id)
    with pytest.raises(KisUsageError):                       # 여전히 차단
        orders.place(order)


def test_reconcile_partial_fill():
    partial = ccld_row(tot_ccld_qty="4", avg_prvs="70000")
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: partial})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    report = orders.reconcile(order.client_order_id)
    assert report is not None
    assert report.status is OrderStatus.PARTIALLY_FILLED
    assert report.filled_quantity == Decimal(4)
    assert report.average_price == Decimal(70000)


def test_reconcile_maps_canceled_and_rejected():
    for over, expected in [
        ({"cncl_yn": "Y"}, OrderStatus.CANCELED),
        ({"rjct_qty": "10"}, OrderStatus.REJECTED),
    ]:
        t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: ccld_row(**over)})
        orders = make_orders(t)
        order = limit_buy()
        with pytest.raises(OrderTimeoutError):
            orders.place(order)
        report = orders.reconcile(order.client_order_id)
        assert report is not None and report.status is expected


def test_reconcile_ignores_unrelated_order_with_different_price():
    # 같은 종목/매매/수량이나 단가가 다른 무관한 주문은 우리 주문으로 오귀속되면 안 된다.
    unrelated = ccld_row(ord_unpr="69000", tot_ccld_qty="10")
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: unrelated})
    orders = make_orders(t)
    order = limit_buy()  # 단가 70000
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    assert orders.reconcile(order.client_order_id) is None   # 매칭 없음 -> 미확인(차단 유지)
    with pytest.raises(KisUsageError):
        orders.place(order)


def test_reconcile_requires_limit_price_present_on_row():
    # 지정가 주문인데 행에 단가가 없으면(구분 불가) 우리 것으로 확정하지 않는다(fail-closed).
    no_price = ccld_row(tot_ccld_qty="10")
    no_price.body["output1"][0].pop("ord_unpr")
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: no_price})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    assert orders.reconcile(order.client_order_id) is None


def test_reconcile_finds_matching_order_on_second_page():
    page1 = RawResponse(rt_cd="0", msg_cd="0", msg1="ok",
                        body={"output1": [], "ctx_area_nk100": "NEXT", "ctx_area_fk100": "FK"})
    page2 = ccld_row(tot_ccld_qty="10")  # 매칭 + 마지막 페이지(ctx 없음)
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: [page1, page2]})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    report = orders.reconcile(order.client_order_id)
    assert report is not None and report.status is OrderStatus.FILLED
    daily_calls = [c for c in t.calls if c["path"] == _DAILY_CCLD]
    assert len(daily_calls) == 2                              # 2페이지 다 읽음
    assert daily_calls[1]["params"]["CTX_AREA_NK100"] == "NEXT"  # 1페이지 연속키 전달


def test_reconcile_page_cap_exhaustion_fails_closed():
    never_ends = RawResponse(rt_cd="0", msg_cd="0", msg1="ok",
                             body={"output1": [], "ctx_area_nk100": "MORE"})
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: never_ends})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    with pytest.raises(KisError):                            # 부분 스캔을 '전부'로 오인 안 함
        orders.reconcile(order.client_order_id)


def test_reconcile_canceled_row_with_partial_fill_maps_canceled():
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"),
                               _DAILY_CCLD: ccld_row(cncl_yn="Y", tot_ccld_qty="4", avg_prvs="70000")})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    report = orders.reconcile(order.client_order_id)
    assert report is not None and report.status is OrderStatus.CANCELED
    assert report.filled_quantity == Decimal(4)              # 취소여도 부분체결 수량은 보존


def test_reconcile_partial_fill_with_rejected_remainder_is_partial():
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"),
                               _DAILY_CCLD: ccld_row(rjct_qty="6", tot_ccld_qty="4", avg_prvs="70000")})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    report = orders.reconcile(order.client_order_id)
    assert report is not None and report.status is OrderStatus.PARTIALLY_FILLED


def test_stop_limit_fingerprint_is_wire_canonical():
    a = Order.stop_limit("005930", side="sell", quantity=Decimal("1E1"),
                         limit_price=Decimal("6.9E4"), stop_price=Decimal("6.95E4"))
    b = Order.stop_limit("005930", side="sell", quantity=10, limit_price=69000, stop_price=69500,
                         client_order_id=a.client_order_id)
    assert a.fingerprint == b.fingerprint


def test_same_client_order_id_with_different_quantity_conflicts():
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t)
    orders.place(Order.limit("005930", side="buy", quantity=10, limit_price=70000, client_order_id="Q"))
    with pytest.raises(KisUsageError):                       # 수량만 달라도 다른 주문 -> 충돌
        orders.place(Order.limit("005930", side="buy", quantity=11, limit_price=70000, client_order_id="Q"))
    assert len(t.calls) == 1


def test_operations_after_close_are_rejected(tmp_path):
    store = OrderStore(path=tmp_path / "orders.json")
    store.close()
    with pytest.raises(KisError):                            # 락 놓은 뒤 변경 연산 거부
        store.try_claim("X", limit_buy(coid="X").fingerprint)


def test_failed_store_init_releases_single_writer_lock(tmp_path):
    path = tmp_path / "orders.json"
    path.write_text("{ corrupt", encoding="utf-8")
    with pytest.raises(KisError):
        OrderStore(path=path)                                # 락 획득 후 _load 실패
    # 파일을 고친 뒤 같은 프로세스에서 재시도가 가능해야 한다(락이 누수되지 않았으므로).
    path.write_text('{"schema_version":1,"in_flight":[],"fingerprints":{},"reports":{}}', encoding="utf-8")
    store = OrderStore(path=path)
    store.close()


def test_prune_drops_stale_completed_reports(tmp_path):
    from datetime import datetime, timedelta, timezone

    from kis_openapi.orders import ExecutionReport
    kst = timezone(timedelta(hours=9))
    store = OrderStore(path=tmp_path / "orders.json", retention_days=7)
    fp = limit_buy(coid="OLD").fingerprint
    old = ExecutionReport(client_order_id="OLD", order_id="1", symbol="005930", side="buy",
                          status=OrderStatus.FILLED, filled_quantity=Decimal(10), average_price=None,
                          submitted_at=datetime.now(kst) - timedelta(days=10))
    store.record(old, fp)                                    # record -> save -> prune
    assert store.report_for("OLD") is None                   # 보존 창 넘어 정리됨
    store.close()


def test_reconcile_unknown_id_raises_without_request():
    t = FakeTransport(response=CCLD_EMPTY)
    orders = make_orders(t)
    with pytest.raises(KisUsageError):                       # 보낸 적 없는 id
        orders.reconcile("NEVER-SEEN")
    assert len(t.calls) == 0                                 # 네트워크 안 침


def test_reconcile_malformed_quantity_fails_closed():
    bad = RawResponse(rt_cd="0", msg_cd="0", msg1="ok", body={"output1": [
        {"odno": "9", "pdno": "005930", "sll_buy_dvsn_cd": "02", "ord_qty": "not-a-number"},
    ]})
    t = FakeTransport(by_path={_ORDER_CASH: TransportTimeout("t"), _DAILY_CCLD: bad})
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderTimeoutError):
        orders.place(order)
    with pytest.raises(KisError):                            # 0으로 조작하지 않고 fail-closed
        orders.reconcile(order.client_order_id)


# --- place: 접수 거부 --------------------------------------------------
def test_rejected_order_clears_in_flight_and_can_be_resubmitted():
    t = FakeTransport(seq=[REJECTED, ACCEPTED])
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderRejectedError):
        orders.place(order)
    # 접수 안 됐으므로 같은 id로 재주문 가능(관찰 가능한 계약으로 검증)
    report = orders.place(order)
    assert report.status is OrderStatus.NEW
    assert len(t.calls) == 2


# --- 퇴직연금 차단 -----------------------------------------------------
def test_accepted_response_without_odno_remains_in_flight():
    no_odno = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="ok", body={"output": {}})
    t = FakeTransport(response=no_odno)
    orders = make_orders(t)
    order = limit_buy()
    with pytest.raises(OrderError):                      # 접수인데 ODNO 없음 -> 재조회 불가
        orders.place(order)
    with pytest.raises(KisUsageError):                   # 상태 불명 -> in-flight 유지, 재전송 차단
        orders.place(order)
    assert len(t.calls) == 1


def test_execution_report_raw_is_immutable():
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t)
    report = orders.place(limit_buy())
    with pytest.raises(TypeError):                       # frozen raw 는 변경 불가
        report.raw["output"] = {}  # type: ignore[index]


def test_non_orderable_account_blocked_before_wire():
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t, orderable=False)
    with pytest.raises(AccountNotOrderable):
        orders.place(limit_buy())
    assert len(t.calls) == 0                             # 와이어에 닿지 않음


# --- 미구현 라우트는 정직하게 NotImplementedError ---------------------
def test_overseas_order_is_not_implemented():
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t)
    with pytest.raises(NotImplementedError):
        orders.place(Order.limit("AAPL", side="buy", quantity=1, limit_price=200, exchange="XNAS"))
    assert len(t.calls) == 0


def test_stop_order_is_not_implemented():
    t = FakeTransport(response=ACCEPTED)
    orders = make_orders(t)
    with pytest.raises(NotImplementedError):
        orders.place(Order.stop("005930", side="sell", quantity=5, stop_price=68000))
    assert len(t.calls) == 0


# --- OrderStore 영속 + 손상 fail-closed -------------------------------
def _orders_with_store(transport, store):
    return Orders(transport, store, cano="12345678", product_code="01")


def test_store_persists_across_restart(tmp_path):
    path = tmp_path / "orders.json"
    order = limit_buy()
    store1 = OrderStore(path=path)
    first = _orders_with_store(FakeTransport(response=ACCEPTED), store1).place(order)
    store1.close()                                      # 프로세스 종료 흉내(락 해제)
    t2 = FakeTransport(response=ACCEPTED)
    replay = make_orders(t2, path=path).place(order)    # 재시작처럼 재로드
    assert replay.order_id == first.order_id
    assert len(t2.calls) == 0                            # 영속 dedup 이 재전송을 막음


def test_second_store_on_same_path_is_rejected(tmp_path):
    path = tmp_path / "orders.json"
    store1 = OrderStore(path=path)                       # 락 보유
    try:
        with pytest.raises(KisError):                   # 다른 프로세스/인스턴스의 동시 열기 거부
            OrderStore(path=path)
    finally:
        store1.close()


def test_corrupt_store_fails_closed(tmp_path):
    path = tmp_path / "orders.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(KisError):                       # 빈 상태로 조용히 시작하지 않는다
        OrderStore(path=path)


def test_unknown_schema_version_is_rejected(tmp_path):
    path = tmp_path / "orders.json"
    path.write_text('{"schema_version": 999, "in_flight": [], "reports": {}}', encoding="utf-8")
    with pytest.raises(KisError):
        OrderStore(path=path)


# --- 저장소 계약 완결성 -------------------------------------------------
def _make_report(coid="C", *, status=OrderStatus.NEW, days_old=0):
    from datetime import datetime, timedelta, timezone

    from kis_openapi.orders import ExecutionReport
    kst = timezone(timedelta(hours=9))
    return ExecutionReport(
        client_order_id=coid, order_id="1", symbol="005930", side="buy", status=status,
        filled_quantity=Decimal(0), average_price=None,
        submitted_at=datetime.now(kst) - timedelta(days=days_old),
    )


@pytest.mark.parametrize("mutate", [
    lambda s: s.try_claim("X", limit_buy(coid="X").fingerprint),
    lambda s: s.record(_make_report("X"), limit_buy(coid="X").fingerprint),
    lambda s: s.clear_in_flight("X"),
])
def test_all_mutators_after_close_are_rejected(tmp_path, mutate):
    # 단일라이터 보장이 사라진 뒤의 모든 변경 연산은 조용히 넘기지 않고 거부한다.
    store = OrderStore(path=tmp_path / "orders.json")
    store.close()
    with pytest.raises(KisError):
        mutate(store)


def test_prune_never_drops_in_flight_and_keeps_fresh(tmp_path):
    # 정리는 나이 지난 '완료' 리포트만 걷어낸다: in-flight 는 나이와 무관하게 유지되고,
    # 보존 창 안의 완료 리포트도 유지된다(멱등 장벽이 조용히 얇아지지 않게).
    store = OrderStore(path=tmp_path / "orders.json", retention_days=7)
    store.try_claim("OLD-INFLIGHT", limit_buy(coid="OLD-INFLIGHT").fingerprint)  # in-flight 확보
    store.record(_make_report("FRESH", days_old=0), limit_buy(coid="FRESH").fingerprint)
    store.record(_make_report("STALE", days_old=10), limit_buy(coid="STALE").fingerprint)
    assert store.is_in_flight("OLD-INFLIGHT")            # in-flight 는 절대 안 걷힘
    assert store.report_for("FRESH") is not None         # 보존 창 안은 유지
    assert store.report_for("STALE") is None             # 창 밖 완료만 정리
    store.close()


def test_try_claim_replays_recorded_report_as_completed():
    # 기록된 리포트가 있는 id 를 같은 지문으로 다시 claim 하면 전송 없이 그 리포트를 replay.
    store = OrderStore()
    fingerprint = limit_buy(coid="DONE").fingerprint
    report = _make_report("DONE")
    store.record(report, fingerprint)
    outcome, replayed = store.try_claim("DONE", fingerprint)
    assert outcome is ClaimOutcome.COMPLETED
    assert replayed is report


def test_persisted_report_with_invalid_side_fails_closed(tmp_path):
    # 스키마는 맞지만 side 값이 도메인 밖(JSON 손상)이면 빈 상태로 시작하지 않고 거부한다.
    path = tmp_path / "orders.json"
    path.write_text(
        '{"schema_version":1,"in_flight":[],"fingerprints":{},"reports":{"X":'
        '{"client_order_id":"X","order_id":"1","symbol":"005930","side":"hold",'
        '"status":"new","filled_quantity":"0","average_price":null,'
        '"submitted_at":"2026-01-01T00:00:00+09:00"}}}',
        encoding="utf-8",
    )
    with pytest.raises(KisError):
        OrderStore(path=path)


def test_persisted_report_with_invalid_quantity_fails_closed(tmp_path):
    # 수량 파싱 실패(InvalidOperation)도 도메인 에러로 fail-closed -- 0 으로 조작하지 않는다.
    path = tmp_path / "orders.json"
    path.write_text(
        '{"schema_version":1,"in_flight":[],"fingerprints":{},"reports":{"X":'
        '{"client_order_id":"X","order_id":"1","symbol":"005930","side":"buy",'
        '"status":"new","filled_quantity":"not-a-number","average_price":null,'
        '"submitted_at":"2026-01-01T00:00:00+09:00"}}}',
        encoding="utf-8",
    )
    with pytest.raises(KisError):
        OrderStore(path=path)


def test_path_backed_store_is_rejected_without_file_locking(tmp_path, monkeypatch):
    # fcntl 이 없는(비-Unix) 플랫폼에선 프로세스 간 단일라이터 보장을 강제할 수 없으므로
    # path-backed 스토어 생성 자체를 거부한다(보장 없음을 조용히 넘기지 않는다).
    import kis_openapi.orders.store as store_mod
    monkeypatch.setattr(store_mod, "fcntl", None)
    with pytest.raises(KisError):
        OrderStore(path=tmp_path / "orders.json")
