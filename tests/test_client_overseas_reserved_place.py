"""미국 해외예약주문 발주 -- kis.overseas.stock("AAPL", exchange="NAS").reserve_buy/sell (order-resv TTTT3014U/3016U).

즉시주문과 별개 라이프사이클(예약번호·체결개념 없음)이지만 이중발주 방지·재시도 금지·보수적
재조회(예약주문조회 기반)를 공유한다. 네트워크 없이 가짜 전송으로 안전 불변식을 검증한다.
"""

from __future__ import annotations

import datetime as _dt
import threading

import pytest

from kis_openapi import ExecutionReport, KISClient, OrderStatus, OrderStore
from kis_openapi.errors import (
    AccountNotOrderable,
    KISError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
)
from kis_openapi.transport import RawResponse, TransportTimeout

_PLACE = "/uapi/overseas-stock/v1/trading/order-resv"
_LIST = "/uapi/overseas-stock/v1/trading/order-resv-list"

# 원장상 US 발주 응답은 output.ODNO(= 취소 시 OVRS_RSVN_ODNO).
_ACCEPTED = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="예약 접수",
                        body={"output": {"ODNO": "0031111234"}})
_REJECTED = RawResponse(rt_cd="1", msg_cd="APBK9999", msg1="예약 불가", body={})


class _FrozenDatetime(_dt.datetime):
    """now() 만 고정 -- reconcile 날짜창·접수일자 판별 결정성 확보."""

    @classmethod
    def now(cls, tz=None):
        return _dt.datetime(2024, 6, 3, 10, 0, tzinfo=tz)


def _list_row(*, odno="0031111234", symbol="AAPL", side_code="02", qty="1", unpr="150", cncl="N",
              receipt="20240603"):
    return {"ovrs_rsvn_odno": odno, "pdno": symbol, "sll_buy_dvsn_cd": side_code,
            "ft_ord_qty": qty, "ft_ord_unpr3": unpr, "ft_ccld_qty": "0", "cncl_yn": cncl,
            "ovrs_excg_cd": "NASD", "prdt_name": "애플", "ovrs_rsvn_ord_stat_cd_name": "접수",
            "rsvn_ord_rcit_dt": receipt, "ord_dt": "", "odno_exec": ""}


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


def _client(transport, *, environment="real", account="12345678-01", store=None, orderable=True):
    return KISClient(app_key="k", app_secret="s", account=account, environment=environment,
                     transport=transport, store=store, orderable=orderable)


# --- 정상 전송 -------------------------------------------------------------
def test_overseas_reserve_buy_wire():
    fake = FakeTransport(response=_ACCEPTED)
    report = _client(fake).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, price="150.25")
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0031111234"          # 해외예약주문번호
    assert report.status is OrderStatus.PENDING_NEW
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _PLACE
    assert call["idempotent"] is False
    assert call["tr_id"] == "TTTT3014U"             # 미국 예약 매수
    assert call["body"]["PDNO"] == "AAPL"
    assert call["body"]["OVRS_EXCG_CD"] == "NASD"
    assert call["body"]["FT_ORD_QTY"] == "1"
    assert call["body"]["FT_ORD_UNPR3"] == "150.25"
    assert call["body"]["ORD_DVSN"] == "00"


def test_overseas_reserve_sell_uses_sell_tr():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake).overseas.stock("AAPL", exchange="NYS").reserve_sell(quantity=2, price="150")
    assert fake.calls[0]["tr_id"] == "TTTT3016U"    # 미국 예약 매도
    assert fake.calls[0]["body"]["OVRS_EXCG_CD"] == "NYSE"


# --- 검증/거부 -------------------------------------------------------------
def test_overseas_reserve_non_us_rejected():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):   # 홍콩은 해외예약 미지원(미국만)
        _client(fake).overseas.stock("00700", exchange="HKS").reserve_buy(quantity=1, price="1")
    assert fake.calls == []


def test_overseas_reserve_requires_price():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):   # 지정가만
        _client(fake).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1)
    assert fake.calls == []


def test_overseas_reserve_demo_rejected():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, price="1")
    assert fake.calls == []


def test_overseas_reserve_non_orderable_rejected():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(AccountNotOrderable):
        _client(fake, orderable=False).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, price="1")
    assert fake.calls == []


def test_overseas_reserve_missing_id_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": {}}, tr_cont="")
    with pytest.raises(OrderError):
        _client(FakeTransport(response=resp)).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, price="1")


# --- 안전 불변식 -----------------------------------------------------------
def test_overseas_reserve_rejected_clears_in_flight_and_reusable():
    store = OrderStore()
    cid = "20240101-ovsresv-rej01"
    with pytest.raises(OrderRejectedError):
        _client(FakeTransport(response=_REJECTED), store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, price="150", client_order_id=cid)
    assert store.fingerprint_for(cid) is None
    report = _client(FakeTransport(response=_ACCEPTED), store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
        quantity=1, price="150", client_order_id=cid)
    assert report.order_id == "0031111234"


def test_overseas_reserve_timeout_no_retry():
    store = OrderStore()
    fake = FakeTransport(raises=TransportTimeout("t"))
    cid = "20240101-ovsresv-to01"
    with pytest.raises(OrderTimeoutError):
        _client(fake, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, price="150", client_order_id=cid)
    assert len(fake.calls) == 1
    assert store.fingerprint_for(cid).exchange == "overseas-reserved"


def test_overseas_reserve_replay_same_id():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    cid = "20240101-ovsresv-rp01"
    t = _client(fake, store=store).overseas.stock("AAPL", exchange="NAS")
    r1 = t.reserve_buy(quantity=1, price="150", client_order_id=cid)
    r2 = t.reserve_buy(quantity=1, price="150", client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert len(fake.calls) == 1


# --- 검증 경계 + 충돌 (B2 보강) --------------------------------------------
@pytest.mark.parametrize("bad_qty", ["0", "-1", "1.5"])
def test_overseas_reserve_invalid_quantity_before_wire(bad_qty):
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=bad_qty, price="150")
    assert fake.calls == []


@pytest.mark.parametrize("bad_price", ["0", "-1"])
def test_overseas_reserve_nonpositive_price_before_wire(bad_price):
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, price=bad_price)
    assert fake.calls == []


def test_overseas_reserve_same_id_different_order_conflicts_no_wire():
    store = OrderStore()
    cid = "20240101-ovsresv-cf01"
    fake = FakeTransport(response=_ACCEPTED)
    t = _client(fake, store=store).overseas.stock("AAPL", exchange="NAS")
    t.reserve_buy(quantity=1, price="150", client_order_id=cid)
    with pytest.raises(KISUsageError):       # 다른 수량 -> 지문 불일치 -> 충돌
        t.reserve_buy(quantity=2, price="150", client_order_id=cid)
    assert len(fake.calls) == 1


def test_overseas_immediate_and_reserved_same_id_cross_lifecycle_conflict():
    store = OrderStore()
    cid = "20240101-ovsresv-xl01"
    immediate = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="", body={"output": {"ODNO": "0001", "ORD_TMD": "1"}}))
    _client(immediate, store=store).overseas.stock("AAPL", exchange="NAS").buy(
        quantity=1, price="150", client_order_id=cid)
    reserved = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):       # 즉시 vs 예약(세션 다름) -> 충돌
        _client(reserved, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, price="150", client_order_id=cid)
    assert reserved.calls == []


# --- reconcile (예약주문조회 기반) -----------------------------------------
def test_overseas_reserve_reconcile_confirms_single_match(monkeypatch):
    monkeypatch.setattr("kis_openapi._overseas.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore()
    cid = "20240101-ovsresv-rc01"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, price="150", client_order_id=cid)
    body = {"output": [_list_row(qty="1", unpr="150")], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_LIST: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    report = _client(recon_t, store=store).reconcile(cid)
    assert report is not None
    assert report.order_id == "0031111234"
    assert all(c["method"] == "GET" for c in recon_t.calls)
    # 고정된 오늘 20240603 기준 양방향 창(오늘-7 ~ 오늘+31)
    assert recon_t.calls[0]["params"]["INQR_STRT_DT"] == "20240527"
    assert recon_t.calls[0]["params"]["INQR_END_DT"] == "20240704"


def test_overseas_reserve_reconcile_ignores_canceled_mismatch_and_old_receipt(monkeypatch):
    monkeypatch.setattr("kis_openapi._overseas.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore()
    cid = "20240101-ovsresv-rc02"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, price="150", client_order_id=cid)
    # 취소된 예약 + 수량 다른 예약 + 접수일자가 오래된 동일지문 예약 -> 매칭 0 -> None(in-flight 유지)
    rows = [_list_row(qty="1", unpr="150", cncl="Y"),
            _list_row(qty="9", unpr="150"),
            _list_row(qty="1", unpr="150", receipt="20240401")]  # 옛 접수 -> 배제
    body = {"output": rows, "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_LIST: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    assert _client(recon_t, store=store).reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None


def test_overseas_reserve_reconcile_multi_match_raises(monkeypatch):
    monkeypatch.setattr("kis_openapi._overseas.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore()
    cid = "20240101-ovsresv-rc03"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, price="150", client_order_id=cid)
    rows = [_list_row(odno="0031111234", qty="1", unpr="150"),
            _list_row(odno="0031111299", qty="1", unpr="150")]
    body = {"output": rows, "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_LIST: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    with pytest.raises(KISError):
        _client(recon_t, store=store).reconcile(cid)
    assert store.fingerprint_for(cid) is not None


def test_overseas_reserve_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_ACCEPTED), account=None).overseas.stock(
            "AAPL", exchange="NAS").reserve_buy(quantity=1, price="1")
