"""예약주문 발주 -- kis.domestic.stock(...).reserve_buy/reserve_sell (order-resv CTSC0008U).

즉시주문과 별개 라이프사이클(rsvn_ord_seq·체결개념 없음)이지만 이중발주 방지·재시도 금지·보수적
재조회(예약주문조회 기반)를 공유한다. 네트워크 없이 가짜 전송으로 안전 불변식을 검증한다.
"""

from __future__ import annotations

import datetime as _dt
import threading
from decimal import Decimal

import pytest

from kis_openapi import ExecutionReport, KISClient, OrderStatus, OrderStore
from kis_openapi.errors import (
    AccountNotOrderableError,
    KISError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
)
from kis_openapi.transport import RawResponse, TransportTimeout


class _FrozenDatetime(_dt.datetime):
    """now() 만 고정, strptime 등은 실제 datetime 을 상속 -- reconcile 날짜창 결정성 확보."""

    @classmethod
    def now(cls, tz=None):
        return _dt.datetime(2024, 6, 3, 10, 0, tzinfo=tz)

_PLACE = "/uapi/domestic-stock/v1/trading/order-resv"
_INQUIRE = "/uapi/domestic-stock/v1/trading/order-resv-ccnl"

# 원장상 발주 응답은 output.rsvn_ord_seq 만.
_ACCEPTED = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="예약주문 접수",
                        body={"output": [{"rsvn_ord_seq": "42401"}]})
_REJECTED = RawResponse(rt_cd="1", msg_cd="APBK9999", msg1="예약주문 불가", body={})


def _reserved_row(*, seq="42401", symbol="005930", side_code="02", div_cd="00",
                  qty="1", unpr="70000"):
    return {"rsvn_ord_seq": seq, "rsvn_ord_ord_dt": "20240605", "pdno": symbol,
            "sll_buy_dvsn_cd": side_code, "ord_dvsn_cd": div_cd, "ord_rsvn_qty": qty,
            "ord_rsvn_unpr": unpr, "tot_ccld_qty": "0", "prcs_rslt": "미처리",
            "ord_dvsn_name": "현금매수", "kor_item_shtn_name": "삼성전자",
            "rjct_rson2": "", "odno": "", "rsvn_end_dt": "20240605"}


class FakeTransport:
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
        assert outcome is not None
        return outcome


def _client(transport, *, environment="real", account="12345678-01", store=None):
    return KISClient(app_key="k", app_secret="s", account=account, environment=environment,
                     transport=transport, store=store)


# --- 정상 전송 -------------------------------------------------------------
def test_reserve_buy_limit_wire():
    fake = FakeTransport(response=_ACCEPTED)
    report = _client(fake).domestic.stock("005930").reserve_buy(quantity=1, price=70000, end_date="20240605")
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "42401"               # 예약주문순번
    assert report.status is OrderStatus.PENDING_NEW  # 접수됨·미집행
    assert report.filled_quantity == Decimal(0)
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _PLACE
    assert call["idempotent"] is False              # 예약주문도 타임아웃 재시도 금지
    assert call["tr_id"] == "CTSC0008U"
    assert call["body"]["PDNO"] == "005930"
    assert call["body"]["SLL_BUY_DVSN_CD"] == "02"  # 매수
    assert call["body"]["ORD_DVSN_CD"] == "00"      # 지정가
    assert call["body"]["ORD_UNPR"] == "70000"
    assert call["body"]["ORD_OBJT_CBLC_DVSN_CD"] == "10"   # 현금
    assert call["body"]["RSVN_ORD_END_DT"] == "20240605"


def test_reserve_sell_market_wire():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake).domestic.stock("005930").reserve_sell(quantity=2)   # 시장가
    call = fake.calls[0]
    assert call["body"]["SLL_BUY_DVSN_CD"] == "01"  # 매도
    assert call["body"]["ORD_DVSN_CD"] == "01"      # 시장가
    assert call["body"]["ORD_UNPR"] == "0"
    assert call["body"]["RSVN_ORD_END_DT"] == ""


# --- 검증/거부 (와이어 접촉 전) --------------------------------------------
@pytest.mark.parametrize("bad_qty", ["0", "-1", "1.5"])
def test_reserve_bad_quantity_rejected_before_io(bad_qty):
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("005930").reserve_buy(quantity=bad_qty, price=70000)
    assert fake.calls == []


def test_reserve_bad_price_rejected_before_io():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("005930").reserve_buy(quantity=1, price=0)
    assert fake.calls == []


def test_reserve_bad_end_date_rejected_before_io():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("005930").reserve_buy(quantity=1, price=1, end_date="2024-06-05")
    assert fake.calls == []


def test_reserve_demo_rejected_before_io():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").domestic.stock("005930").reserve_buy(quantity=1, price=1)
    assert fake.calls == []


def test_reserve_overseas_stock_rejects_end_date():
    # 해외(미국) 티커의 reserve_buy 는 해외예약으로 라우팅되며 end_date 를 지원하지 않는다(전송 전 거부)
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, price=1, end_date="20240605")
    assert fake.calls == []


def test_reserve_missing_sequence_fails_closed_holds_in_flight():
    store = OrderStore()
    cid = "20240101-reserved-noseq01"
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": [{}]}, tr_cont="")
    with pytest.raises(OrderError):  # 순번 없으면 재조회 불가
        _client(FakeTransport(response=resp), store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=1, client_order_id=cid)
    assert store.fingerprint_for(cid) is not None    # in-flight 유지(재전송 금지)


def test_reserve_multi_row_sequence_treated_as_missing():
    # 발주 응답 output 이 다건이면 특정 불가 -> 순번 없음으로 취급, in-flight 유지·raise
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="",
                       body={"output": [{"rsvn_ord_seq": "1"}, {"rsvn_ord_seq": "2"}]}, tr_cont="")
    with pytest.raises(OrderError):
        _client(FakeTransport(response=resp)).domestic.stock("005930").reserve_buy(quantity=1, price=1)


def test_reserve_non_orderable_account_rejected_before_io():
    # 조회전용(퇴직연금 등) 계좌는 예약주문도 전송 전에 거부해야 한다(즉시주문과 동일 가드)
    fake = FakeTransport(response=_ACCEPTED)
    client = KISClient(app_key="k", app_secret="s", account="12345678-01",
                       environment="real", transport=fake, orderable=False)
    with pytest.raises(AccountNotOrderableError):
        client.domestic.stock("005930").reserve_buy(quantity=1, price=70000)
    assert fake.calls == []


# --- 안전 불변식 -----------------------------------------------------------
def test_reserve_rejected_clears_in_flight_and_reusable():
    store = OrderStore()
    cid = "20240101-reserved-rej01"
    t = _client(FakeTransport(response=_REJECTED), store=store).domestic.stock("005930")
    with pytest.raises(OrderRejectedError):
        t.reserve_buy(quantity=1, price=70000, client_order_id=cid)
    assert store.fingerprint_for(cid) is None       # 거부 -> in-flight 해제
    fake2 = FakeTransport(response=_ACCEPTED)
    report = _client(fake2, store=store).domestic.stock("005930").reserve_buy(
        quantity=1, price=70000, client_order_id=cid)
    assert report.order_id == "42401"
    assert len(fake2.calls) == 1


def test_reserve_timeout_no_retry_holds_in_flight():
    store = OrderStore()
    fake = FakeTransport(raises=TransportTimeout("t"))
    cid = "20240101-reserved-to01"
    with pytest.raises(OrderTimeoutError):
        _client(fake, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=70000, client_order_id=cid)
    assert len(fake.calls) == 1                      # 재전송 없음
    assert store.fingerprint_for(cid) is not None    # in-flight 유지


def test_reserve_replay_same_id_returns_prior():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    cid = "20240101-reserved-rp01"
    t = _client(fake, store=store).domestic.stock("005930")
    r1 = t.reserve_buy(quantity=1, price=70000, client_order_id=cid)
    r2 = t.reserve_buy(quantity=1, price=70000, client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert len(fake.calls) == 1                      # replay -- 재전송 없음


def test_reserve_fingerprint_namespaced_from_immediate():
    # 같은 종목/수량/가격이라도 예약주문 지문은 exchange="reserved" 라 즉시주문과 다르다
    store = OrderStore()
    cid = "20240101-reserved-ns01"
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake, store=store).domestic.stock("005930").reserve_buy(quantity=1, price=70000, client_order_id=cid)
    fp = store.fingerprint_for(cid)
    assert fp is not None and fp.exchange == "reserved"


# --- reconcile (예약주문조회 기반, 보수적) ---------------------------------
def test_reserve_reconcile_confirms_single_match():
    store = OrderStore()
    cid = "20240101-reserved-rc01"
    # 발주 타임아웃 -> in-flight
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=70000, client_order_id=cid)
    # 재조회: 예약주문조회에 지문과 맞는 미처리 예약 하나
    recon_body = {"output": [_reserved_row(qty="1", unpr="70000")],
                  "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=recon_body)})
    report = _client(recon_t, store=store).orders.reconcile(cid)
    assert report is not None
    assert report.order_id == "42401"
    assert report.status is OrderStatus.PENDING_NEW
    assert all(c["method"] == "GET" for c in recon_t.calls)   # 읽기만


def test_reserve_reconcile_no_match_stays_in_flight():
    store = OrderStore()
    cid = "20240101-reserved-rc02"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=70000, client_order_id=cid)
    # 조회 결과가 비면 미접수로 단정하지 않는다 -> None, in-flight 유지
    empty_body = {"output": [], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=empty_body)})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None    # 여전히 in-flight


def test_reserve_reconcile_multi_match_raises():
    store = OrderStore()
    cid = "20240101-reserved-rc03"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=70000, client_order_id=cid)
    body = {"output": [_reserved_row(seq="42401", qty="1", unpr="70000"),
                       _reserved_row(seq="42402", qty="1", unpr="70000")],
            "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    with pytest.raises(KISError):
        _client(recon_t, store=store).orders.reconcile(cid)


def test_reserve_reconcile_scans_all_pages_before_confirming():
    # 재조회는 조기 종료 금지: 첫 페이지가 tr_cont 소진(D)이라도 연속조회 커서(ctx_area_nk200)가
    # 남아 있으면 계속 스캔해야 한다. 그러지 않으면 다음 페이지의 두 번째 일치 예약을 놓쳐 ≥2 모호를
    # 1건으로 오판, 접수 안 됐을 수도 있는 주문을 잘못 확정한다(이중발주 위험). 즉시/해외 재조회와
    # 같은 보수적 종료(tr_cont 소진 '그리고' 커서 소진일 때만 멈춤).
    store = OrderStore()
    cid = "20240101-reserved-pg01"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=70000, client_order_id=cid)
    page1 = RawResponse(rt_cd="0", msg_cd="M", msg1="", tr_cont="D",
                        body={"output": [_reserved_row(seq="42401", qty="1", unpr="70000")],
                              "ctx_area_nk200": "NEXT", "ctx_area_fk200": "FK"})
    page2 = RawResponse(rt_cd="0", msg_cd="M", msg1="", tr_cont="",
                        body={"output": [_reserved_row(seq="42402", qty="1", unpr="70000")],
                              "ctx_area_nk200": "", "ctx_area_fk200": ""})
    recon_t = FakeTransport(by_path={_INQUIRE: [page1, page2]})
    with pytest.raises(KISError):                      # 두 페이지 모두 스캔 -> 2건 -> 모호로 거부
        _client(recon_t, store=store).orders.reconcile(cid)
    assert len(recon_t.calls) == 2                     # 커서가 남아 둘째 페이지도 조회했다


def test_reserve_same_id_different_order_conflicts_no_wire():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    cid = "20240101-reserved-cf01"
    t = _client(fake, store=store).domestic.stock("005930")
    t.reserve_buy(quantity=1, price=70000, client_order_id=cid)
    with pytest.raises(KISUsageError):               # 같은 id, 다른 수량 -> 충돌
        t.reserve_buy(quantity=2, price=70000, client_order_id=cid)
    assert len(fake.calls) == 1                      # 충돌은 와이어에 닿지 않는다


def test_reserve_and_immediate_same_id_cross_lifecycle_conflicts():
    # 같은 종목/수량/가격의 즉시주문과 예약주문을 같은 client_order_id 로 내면 지문이 달라 CONFLICT,
    # 두 번째(예약)는 와이어에 닿지 않는다.
    store = OrderStore()
    cid = "20240101-reserved-xl01"
    immediate = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="",
        body={"output": {"KRX_FWDG_ORD_ORGNO": "01", "ODNO": "0001", "ORD_TMD": "1"}}))
    _client(immediate, store=store).domestic.stock("005930").buy(quantity=1, price=70000, client_order_id=cid)
    reserved = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(reserved, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=70000, client_order_id=cid)
    assert reserved.calls == []                      # 예약 POST 없음


def test_reserve_end_date_distinguishes_fingerprint():
    # 종료일만 다른 두 예약을 같은 id 로 내면 서로 다른 주문 -> 두 번째는 CONFLICT(silent replay 아님)
    store = OrderStore()
    cid = "20240101-reserved-ed01"
    t = _client(FakeTransport(response=_ACCEPTED), store=store).domestic.stock("005930")
    t.reserve_buy(quantity=1, price=70000, end_date="20240605", client_order_id=cid)
    fake2 = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake2, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=70000, end_date="20240610", client_order_id=cid)
    assert fake2.calls == []


@pytest.mark.parametrize("changed", [
    {"pdno": "000660"}, {"sll_buy_dvsn_cd": "01"}, {"ord_dvsn_cd": "01"},
    {"ord_rsvn_qty": "2"}, {"ord_rsvn_unpr": "70001"},
])
def test_reserve_reconcile_rejects_partial_match(changed):
    # 지문의 한 필드라도 다른 행은 확정하지 않는다(오귀속 방지) -> None, in-flight 유지
    store = OrderStore()
    cid = "20240101-reserved-pm01"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=70000, client_order_id=cid)
    row = _reserved_row(qty="1", unpr="70000")
    row.update(changed)
    body = {"output": [row], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None


def test_reserve_reconcile_window_and_process_params(monkeypatch):
    monkeypatch.setattr("kis_openapi._domestic.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore()
    cid = "20240101-reserved-win01"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=70000, client_order_id=cid)
    body = {"output": [], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    _client(recon_t, store=store).orders.reconcile(cid)
    params = recon_t.calls[0]["params"]
    # 고정된 오늘 20240603 기준 양방향 창(오늘-7 ~ 오늘+31), 처리/미처리 모두(0)
    assert params["RSVN_ORD_ORD_DT"] == "20240527"   # 20240603 - 7
    assert params["RSVN_ORD_END_DT"] == "20240704"   # 20240603 + 31
    assert params["PRCS_DVSN_CD"] == "0"             # all


def test_reserve_reconcile_ignores_blank_sequence_row():
    # 순번 없는 매칭 행은 정정·취소가 불가라 확정하지 않는다 -> None, in-flight 유지
    store = OrderStore()
    cid = "20240101-reserved-bs01"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, price=70000, client_order_id=cid)
    row = _reserved_row(seq="", qty="1", unpr="70000")
    body = {"output": [row], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None


def test_reserve_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_ACCEPTED), account=None).domestic.stock("005930").reserve_buy(
            quantity=1, price=1)
