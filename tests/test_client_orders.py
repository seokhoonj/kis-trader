"""주문 실행 안전 재검증 (새 API) -- kis.ticker(...).buy/sell + kis.reconcile.

멱등 dedup(replay/충돌/in-flight), 쓰기 타임아웃 재시도 금지, 접수 거부, ODNO 부재, 퇴직연금
차단, 시장가/지정가 와이어, 보수적 재조회를 네트워크 없이 가짜 전송으로 검증한다. 안전 엔진은
검증된 코어를 새 구조로 이관한 것이라, 여기서 새 진입점(ticker.buy/sell, kis.reconcile)이 그
불변식을 그대로 구동하는지 확인한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import ExecutionReport, KISClient, OrderStatus, OrderStore
from kis_openapi.errors import (
    AccountNotOrderable,
    KISError,
    KISUsageError,
    OrderRejectedError,
    OrderTimeoutError,
)
from kis_openapi.transport import RawResponse, TransportTimeout

_ORDER_CASH = "/uapi/domestic-stock/v1/trading/order-cash"
_ORDER_CHANGE = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
_DAILY_CCLD = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"


class FakeTransport:
    """가짜 전송. 경로별 순차 응답/예외(by_path 리스트), 기본 응답, 예외 지원. 호출 기록."""

    def __init__(self, *, response=None, raises=None, by_path=None):
        self.response = response
        self.raises = raises
        self.by_path = by_path or {}
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "body": body, "idempotent": idempotent})
        if path in self.by_path:
            outcome = self.by_path[path]
            if isinstance(outcome, list):
                outcome = outcome.pop(0)
        elif self.raises is not None:
            outcome = self.raises
        else:
            outcome = self.response
        if isinstance(outcome, BaseException) or (isinstance(outcome, type) and issubclass(outcome, BaseException)):
            raise outcome
        assert outcome is not None, "FakeTransport 에 응답을 줘야 한다"
        return outcome


_ACCEPTED_ORDER_RESPONSE = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117057",
                     "ORD_TMD": "121052"}},
)
_REJECTED_ORDER_RESPONSE = RawResponse(rt_cd="1", msg_cd="APBK1234", msg1="주문가능금액 부족", body={})


def _daily_orders_response(rows):
    return RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="조회 완료", body={"output1": rows})


def _daily_order_row(*, odno="0000117057", symbol="005930", side_code="02", order_division="00",
                     order_quantity="10", order_unit_price="70000", filled_quantity="10",
                     average_price="70000", rejected_quantity="0", canceled="N"):
    return {"odno": odno, "pdno": symbol, "sll_buy_dvsn_cd": side_code,
            "ord_dvsn_cd": order_division, "ord_qty": order_quantity, "ord_unpr": order_unit_price,
            "tot_ccld_qty": filled_quantity, "avg_prvs": average_price,
            "rjct_qty": rejected_quantity, "cncl_yn": canceled}


def _client(transport, *, environment="real", account="12345678-01", store=None, orderable=True):
    return KISClient(app_key="k", app_secret="s", account=account, environment=environment,
                     transport=transport, store=store, orderable=orderable)


# --- 정상 전송 -------------------------------------------------------------
def test_buy_limit_places_order():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    report = _client(fake).ticker("005930").buy(quantity=10, price=70000)
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000117057"
    assert report.symbol == "005930"
    assert report.side == "buy"
    assert report.status is OrderStatus.NEW
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _ORDER_CASH
    assert call["idempotent"] is False              # 주문은 타임아웃 재시도 금지
    assert call["tr_id"] == "TTTC0012U"             # 실전 매수
    assert call["body"]["ORD_DVSN"] == "00"         # 지정가
    assert call["body"]["ORD_UNPR"] == "70000"


def test_buy_market_uses_market_division():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    _client(fake).ticker("005930").buy(quantity=10)
    assert fake.calls[0]["body"]["ORD_DVSN"] == "01"    # 시장가
    assert fake.calls[0]["body"]["ORD_UNPR"] == "0"


def test_sell_uses_sell_tr():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    report = _client(fake).ticker("005930").sell(quantity=10, price=70000)
    assert report.side == "sell"
    assert fake.calls[0]["tr_id"] == "TTTC0011U"        # 실전 매도


def test_demo_uses_demo_tr():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    _client(fake, environment="demo").ticker("005930").buy(quantity=10, price=70000)
    assert fake.calls[0]["tr_id"] == "VTTC0012U"


def test_cancel_domestic_order_uses_original_identifiers_and_deduplicates():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake)
    kis.ticker("005930").buy(
        quantity=10, price=70000, client_order_id="original-1"
    )
    first = kis.cancel_order("original-1", request_id="cancel-1")
    second = kis.cancel_order("original-1", request_id="cancel-1")

    assert first.status is OrderStatus.PENDING_CANCEL
    assert second == first
    assert len(fake.calls) == 2
    call = fake.calls[1]
    assert call["path"] == _ORDER_CHANGE
    assert call["tr_id"] == "TTTC0013U"
    assert call["idempotent"] is False
    assert call["body"]["KRX_FWDG_ORD_ORGNO"] == "01790"
    assert call["body"]["ORGN_ODNO"] == "0000117057"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "02"
    assert call["body"]["ORD_QTY"] == "10"
    assert call["body"]["ORD_UNPR"] == "0"
    assert call["body"]["QTY_ALL_ORD_YN"] == "Y"


def test_replace_domestic_order_maps_new_quantity_and_price():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake, environment="demo")
    kis.ticker("005930").sell(
        quantity=10, price=70000, client_order_id="original-2"
    )
    report = kis.replace_order(
        "original-2", quantity=4, price=71000, request_id="replace-1"
    )

    assert report.status is OrderStatus.PENDING_REPLACE
    call = fake.calls[1]
    assert call["tr_id"] == "VTTC0013U"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "01"
    assert call["body"]["ORD_QTY"] == "4"
    assert call["body"]["ORD_UNPR"] == "71000"
    assert call["body"]["QTY_ALL_ORD_YN"] == "N"


def test_domestic_change_timeout_stays_in_flight_and_is_not_resent():
    fake = FakeTransport(
        by_path={
            _ORDER_CASH: _ACCEPTED_ORDER_RESPONSE,
            _ORDER_CHANGE: TransportTimeout(),
        }
    )
    kis = _client(fake)
    kis.ticker("005930").buy(
        quantity=10, price=70000, client_order_id="original-3"
    )
    with pytest.raises(OrderTimeoutError):
        kis.cancel_order("original-3", request_id="cancel-timeout")
    with pytest.raises(KISUsageError, match="재전송하지"):
        kis.cancel_order("original-3", request_id="cancel-timeout")
    assert len([call for call in fake.calls if call["path"] == _ORDER_CHANGE]) == 1


# --- 멱등 dedup ------------------------------------------------------------
def test_same_client_order_id_replays_without_resend():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake)
    first = kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    second = kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    assert second.order_id == first.order_id
    assert len(fake.calls) == 1                          # 재전송 안 함(replay)


def test_same_id_different_order_conflicts():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake)
    kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    with pytest.raises(KISUsageError):                   # 같은 id 다른 주문 -> 충돌 거부
        kis.ticker("005930").buy(quantity=99, price=70000, client_order_id="ID-1")


# --- 타임아웃 = 재시도 금지, in-flight 유지 --------------------------------
def test_write_timeout_raises_and_does_not_retry():
    fake = FakeTransport(raises=TransportTimeout("timeout"))
    with pytest.raises(OrderTimeoutError) as excinfo:
        _client(fake).ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    assert excinfo.value.client_order_id == "ID-1"
    assert len(fake.calls) == 1                          # 재시도 없음


def test_in_flight_after_timeout_refuses_resend():
    fake = FakeTransport(by_path={_ORDER_CASH: [TransportTimeout("t"), _ACCEPTED_ORDER_RESPONSE]})
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    with pytest.raises(KISUsageError):                   # in-flight -> 재전송 거부(재조회 요구)
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")


# --- 접수 거부 / ODNO 부재 -------------------------------------------------
def test_rejected_raises_and_clears_in_flight():
    fake = FakeTransport(by_path={_ORDER_CASH: [_REJECTED_ORDER_RESPONSE, _ACCEPTED_ORDER_RESPONSE]})
    kis = _client(fake)
    with pytest.raises(OrderRejectedError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    # 거부는 in-flight 를 해제하므로 같은 id 재전송이 허용된다(이번엔 접수).
    report = kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    assert report.order_id == "0000117057"


def test_accepted_without_odno_raises():
    resp = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="ok", body={"output": {}})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).ticker("005930").buy(quantity=10, price=70000)


# --- 계좌 가드 -------------------------------------------------------------
def test_retirement_account_blocked():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(AccountNotOrderable):
        _client(fake, orderable=False).ticker("005930").buy(quantity=10, price=70000)
    assert fake.calls == []                              # 와이어에 닿기 전 차단


def test_order_requires_account():
    kis = KISClient(app_key="k", app_secret="s", transport=FakeTransport(response=_ACCEPTED_ORDER_RESPONSE))
    with pytest.raises(KISUsageError):
        kis.ticker("005930").buy(quantity=10, price=70000)


# --- 재조회(reconcile) -----------------------------------------------------
def test_reconcile_replays_completed():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake)
    placed = kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    again = kis.reconcile("ID-1")
    assert again is not None
    assert again.order_id == placed.order_id
    assert len(fake.calls) == 1                          # 완료 replay -- 추가 호출 없음


def test_reconcile_unknown_id_raises():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)).reconcile("never-sent")


def test_timeout_then_reconcile_confirms_fill():
    # 매수가 타임아웃 -> in-flight -> 재조회가 일별체결조회에서 지문 일치 1건 발견 -> 체결 확정.
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row()])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    report = kis.reconcile("ID-1")
    assert report is not None
    assert report.status is OrderStatus.FILLED
    assert report.filled_quantity == Decimal(10)


def test_reconcile_empty_scan_stays_in_flight():
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    assert kis.reconcile("ID-1") is None                 # 0건 -> 미접수로 단정 안 함(재전송 금지 유지)


def test_reconcile_ambiguous_multiple_matches_raises():
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(odno="A"), _daily_order_row(odno="B")])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    with pytest.raises(KISError):                         # 지문 일치 2건 -> 자동 확정 불가
        kis.reconcile("ID-1")


# --- 세션 store 공유 -------------------------------------------------------
def test_dedup_shared_across_tickers_via_client_store():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake)
    kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")   # 같은 세션 store
    assert len(fake.calls) == 1


def test_injected_store_used():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake, store=store)
    report = kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    assert store.report_for("ID-1") is not None
    assert store.report_for("ID-1").order_id == report.order_id


# --- 미구현 TIF fail-closed -------------------------------------------------
def test_non_day_time_in_force_rejected_before_wire():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(NotImplementedError):     # ioc/fok 는 아직 미구현 -> 조용히 day 로 안 보냄
        _client(fake).ticker("005930").buy(quantity=10, price=70000, time_in_force="ioc")
    assert fake.calls == []


# --- 주문 와이어(수량/구분/TR) 전수 ----------------------------------------
@pytest.mark.parametrize(
    ("side", "price", "expected_tr", "expected_dvsn", "expected_unpr"),
    [("buy", 70000, "TTTC0012U", "00", "70000"), ("buy", None, "TTTC0012U", "01", "0"),
     ("sell", 70000, "TTTC0011U", "00", "70000"), ("sell", None, "TTTC0011U", "01", "0")],
)
def test_order_wire_quantity_and_division(side, price, expected_tr, expected_dvsn, expected_unpr):
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    getattr(_client(fake).ticker("005930"), side)(quantity=17, price=price)
    body = fake.calls[0]["body"]
    assert body["ORD_QTY"] == "17"
    assert body["ORD_DVSN"] == expected_dvsn
    assert body["ORD_UNPR"] == expected_unpr
    assert fake.calls[0]["tr_id"] == expected_tr


def test_rejected_then_retry_sends_twice():
    fake = FakeTransport(by_path={_ORDER_CASH: [_REJECTED_ORDER_RESPONSE, _ACCEPTED_ORDER_RESPONSE]})
    kis = _client(fake)
    with pytest.raises(OrderRejectedError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    report = kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    assert report.order_id == "0000117057"
    assert len(fake.calls) == 2                   # 거부 후 재전송이 실제로 한 번 더 나감


# --- 재조회 지문 매칭 정확성 -----------------------------------------------
@pytest.mark.parametrize(
    "mismatch",
    [{"order_unit_price": "70001"}, {"symbol": "000660"}, {"side_code": "01"},
     {"order_quantity": "11"}],
    ids=["wrong-price", "wrong-symbol", "wrong-side", "wrong-quantity"],
)
def test_reconcile_ignores_mismatched_fingerprint(mismatch):
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(**mismatch)])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    assert kis.reconcile("ID-1") is None          # 지문 불일치 -> 0건 -> 오귀속 안 함


@pytest.mark.parametrize(
    ("row_updates", "expected_status", "expected_filled"),
    [({"filled_quantity": "4"}, OrderStatus.PARTIALLY_FILLED, Decimal(4)),
     ({"filled_quantity": "0", "rejected_quantity": "10", "average_price": "0"}, OrderStatus.REJECTED, Decimal(0)),
     ({"filled_quantity": "3", "canceled": "Y"}, OrderStatus.CANCELED, Decimal(3))],
    ids=["partial", "rejected", "canceled"],
)
def test_reconcile_maps_daily_row_status(row_updates, expected_status, expected_filled):
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(**row_updates)])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    report = kis.reconcile("ID-1")
    assert report is not None
    assert report.status is expected_status
    assert report.filled_quantity == expected_filled


def test_reconcile_daily_ccld_failure_fails_closed():
    failed = RawResponse(rt_cd="1", msg_cd="APBK9999", msg1="조회 실패", body={})
    fake = FakeTransport(by_path={_ORDER_CASH: [TransportTimeout("t")], _DAILY_CCLD: [failed]})
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    with pytest.raises(KISError):                 # 조회 실패를 빈 결과(미접수)로 오인하지 않음
        kis.reconcile("ID-1")


def test_reconcile_scans_all_daily_ccld_pages():
    page1 = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="ok",
                        body={"output1": [_daily_order_row(odno="A")],
                              "ctx_area_fk100": "FK2", "ctx_area_nk100": "NK2"})
    page2 = _daily_orders_response([_daily_order_row(odno="B")])
    fake = FakeTransport(by_path={_ORDER_CASH: [TransportTimeout("t")], _DAILY_CCLD: [page1, page2]})
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    with pytest.raises(KISError):                 # 두 페이지 걸쳐 2건 -> 모호 -> 자동확정 불가
        kis.reconcile("ID-1")
    assert fake.calls[2]["params"]["CTX_AREA_NK100"] == "NK2"   # 2페이지째에 연속키 전달


def test_reconcile_full_report_semantics():
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(filled_quantity="10", average_price="69950")])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.ticker("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    report = kis.reconcile("ID-1")
    assert report is not None
    assert report.client_order_id == "ID-1"
    assert report.order_id == "0000117057"
    assert report.symbol == "005930"
    assert report.side == "buy"
    assert report.status is OrderStatus.FILLED
    assert report.average_price == Decimal(69950)
