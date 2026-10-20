"""예약주문 발주 -- kis.domestic.stock(...).reserve_buy/reserve_sell (order-resv CTSC0008U).

즉시주문과 별개 라이프사이클(rsvn_ord_seq·체결개념 없음)이지만 이중발주 방지·재시도 금지·보수적
재조회(예약주문조회 기반)를 공유한다. 네트워크 없이 가짜 전송으로 안전 불변식을 검증한다.
"""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import replace
from decimal import Decimal

import pytest

from kis_trader import ExecutionReport, KISClient, OrderStatus, OrderStore, RiskLimits
from kis_trader.errors import (
    AccountNotOrderableError,
    KISAuthError,
    KISError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
    PreTradeRiskError,
)
from kis_trader.transport import RawResponse, TransportTimeoutError


class _FrozenDatetime(_dt.datetime):
    """now() 만 고정, strptime 등은 실제 datetime 을 상속 -- reconcile 날짜창 결정성 확보."""

    @classmethod
    def now(cls, tz=None):
        return _dt.datetime(2024, 6, 3, 10, 0, tzinfo=tz)


def _frozen_now():
    """store claim 시계를 reconcile 날짜창(_FrozenDatetime)과 같은 날(2024-06-03 KST)로 고정 --
    reconcile 의 접수일자 매칭 창이 claim 발주일에 앵커하므로 store 시각도 결정적이어야 한다."""
    return _dt.datetime(2024, 6, 3, 10, 0, tzinfo=_dt.timezone(_dt.timedelta(hours=9)))


_PLACE = "/uapi/domestic-stock/v1/trading/order-resv"
_INQUIRE = "/uapi/domestic-stock/v1/trading/order-resv-ccnl"

# 발주(order) 응답이라 output 키는 대문자다(원장 order_resv/chk = RSVN_ORD_SEQ; ODNO 와 같은 규칙).
_ACCEPTED = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="예약주문 접수",
                        body={"output": [{"RSVN_ORD_SEQ": "42401"}]})
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


def _client(transport, *, environment="real", account="12345678-01", store=None, risk=None):
    return KISClient(app_key="k", app_secret="s", account=account, environment=environment,
                     transport=transport, store=store, risk=risk)


# --- 정상 전송 -------------------------------------------------------------
def test_reserve_buy_limit_wire():
    fake = FakeTransport(response=_ACCEPTED)
    report = _client(fake).domestic.stock("005930").reserve_buy(quantity=1, limit_price=70000, end_date="20240605")
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
        _client(fake).domestic.stock("005930").reserve_buy(quantity=bad_qty, limit_price=70000)
    assert fake.calls == []


def test_reserve_bad_price_rejected_before_io():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("005930").reserve_buy(quantity=1, limit_price=0)
    assert fake.calls == []


def test_reserve_bad_end_date_rejected_before_io():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("005930").reserve_buy(quantity=1, limit_price=1, end_date="2024-06-05")
    assert fake.calls == []


def test_reserve_demo_rejected_before_io():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").domestic.stock("005930").reserve_buy(quantity=1, limit_price=1)
    assert fake.calls == []


def test_reserve_overseas_stock_rejects_end_date():
    # 해외(미국) 티커의 reserve_buy 는 해외예약으로 라우팅되며 end_date 를 지원하지 않는다(전송 전 거부)
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price=1, end_date="20240605")
    assert fake.calls == []


def test_reserve_missing_sequence_fails_closed_holds_in_flight():
    store = OrderStore()
    cid = "20240101-reserved-noseq01"
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": [{}]}, tr_cont="")
    with pytest.raises(OrderError):  # 순번 없으면 재조회 불가
        _client(FakeTransport(response=resp), store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=1, client_order_id=cid)
    assert store.fingerprint_for(cid) is not None    # in-flight 유지(재전송 금지)


def test_reserve_multi_row_sequence_treated_as_missing():
    # 발주 응답 output 이 다건이면 특정 불가 -> 순번 없음으로 취급, in-flight 유지·raise
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="",
                       body={"output": [{"RSVN_ORD_SEQ": "1"}, {"RSVN_ORD_SEQ": "2"}]}, tr_cont="")
    with pytest.raises(OrderError):
        _client(FakeTransport(response=resp)).domestic.stock("005930").reserve_buy(quantity=1, limit_price=1)


def test_reserve_non_orderable_account_rejected_before_io():
    # 조회전용(퇴직연금 등) 계좌는 예약주문도 전송 전에 거부해야 한다(즉시주문과 동일 가드)
    fake = FakeTransport(response=_ACCEPTED)
    client = KISClient(app_key="k", app_secret="s", account="12345678-01",
                       environment="real", transport=fake, orderable=False)
    with pytest.raises(AccountNotOrderableError):
        client.domestic.stock("005930").reserve_buy(quantity=1, limit_price=70000)
    assert fake.calls == []


# --- 안전 불변식 -----------------------------------------------------------
def test_reserve_rejected_clears_in_flight_and_reusable():
    store = OrderStore()
    cid = "20240101-reserved-rej01"
    t = _client(FakeTransport(response=_REJECTED), store=store).domestic.stock("005930")
    with pytest.raises(OrderRejectedError):
        t.reserve_buy(quantity=1, limit_price=70000, client_order_id=cid)
    assert store.fingerprint_for(cid) is None       # 거부 -> in-flight 해제
    fake2 = FakeTransport(response=_ACCEPTED)
    report = _client(fake2, store=store).domestic.stock("005930").reserve_buy(
        quantity=1, limit_price=70000, client_order_id=cid)
    assert report.order_id == "42401"
    assert len(fake2.calls) == 1


def test_reserve_timeout_no_retry_holds_in_flight():
    store = OrderStore()
    fake = FakeTransport(raises=TransportTimeoutError("t"))
    cid = "20240101-reserved-to01"
    with pytest.raises(OrderTimeoutError):
        _client(fake, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    assert len(fake.calls) == 1                      # 재전송 없음
    assert store.fingerprint_for(cid) is not None    # in-flight 유지


def test_reserve_throttle_kiserror_becomes_timeout_and_holds_in_flight():
    # KIS 는 스로틀을 HTTP 500(KISError)로 낸다. claim 뒤에 터졌고 접수 여부 불명이므로 raw 로 올리지
    # 않고 OrderTimeoutError 로 바꿔 in-flight 를 유지한다 -- 안 그러면 호출자가 새 id 로 재예약해 이중예약.
    store = OrderStore()
    fake = FakeTransport(raises=KISError("스로틀", rt_cd="1", msg_cd="EGW00201", msg1="초당 거래건수 초과"))
    cid = "20240101-reserved-th01"
    with pytest.raises(OrderTimeoutError):
        _client(fake, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    assert len(fake.calls) == 1                      # 재전송 없음
    assert store.fingerprint_for(cid) is not None    # in-flight 유지(미접수 단정 금지)


def test_reserve_auth_error_clears_in_flight_and_raises():
    # 401(KISAuthError)은 인증 단계 거부 -- 예약이 접수되지 않았음이 확실하다. in-flight 를 해제하고
    # 그대로 올려 토큰 교정 뒤 재시도하게 한다(해제 안 하면 그 id 가 영구 InFlight 로 막힌다).
    store = OrderStore()
    fake = FakeTransport(raises=KISAuthError("인증거부", rt_cd="1", msg_cd="EGW00123", msg1="token"))
    cid = "20240101-reserved-au01"
    with pytest.raises(KISAuthError):
        _client(fake, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    assert len(fake.calls) == 1                      # 재전송 없음
    assert store.fingerprint_for(cid) is None        # in-flight 해제됨(영구 유령 방지)


def test_reserve_replay_same_id_returns_prior():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    cid = "20240101-reserved-rp01"
    t = _client(fake, store=store).domestic.stock("005930")
    r1 = t.reserve_buy(quantity=1, limit_price=70000, client_order_id=cid)
    r2 = t.reserve_buy(quantity=1, limit_price=70000, client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert len(fake.calls) == 1                      # replay -- 재전송 없음


def test_reserve_fingerprint_namespaced_from_immediate():
    # 같은 종목/수량/가격이라도 예약주문 지문은 exchange="reserved" 라 즉시주문과 다르다
    store = OrderStore()
    cid = "20240101-reserved-ns01"
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake, store=store).domestic.stock("005930").reserve_buy(quantity=1, limit_price=70000, client_order_id=cid)
    fp = store.fingerprint_for(cid)
    assert fp is not None and fp.exchange == "reserved"


# --- reconcile (예약주문조회 기반, 보수적) ---------------------------------
def test_reserve_reconcile_confirms_single_match(monkeypatch):
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-reserved-rc01"
    # 접수 응답으로 증권사 주문번호를 저장한다.
    place_t = FakeTransport(response=_ACCEPTED)
    _client(place_t, store=store).domestic.stock("005930").reserve_buy(
        quantity=1, limit_price=70000, client_order_id=cid)
    # 재조회: 예약주문조회에 지문과 맞는 미처리 예약 하나
    row = _reserved_row(qty="1", unpr="70000")
    row["rsvn_ord_rcit_dt"] = "20240603"
    recon_body = {"output": [row],
                  "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=recon_body)})
    report = _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    assert report is not None
    assert report.order_id == "42401"
    assert report.status is OrderStatus.PENDING_NEW
    assert all(c["method"] == "GET" for c in recon_t.calls)   # 읽기만


def test_reserve_refresh_does_not_rebind_identical_sibling(monkeypatch):
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-reserved-refresh-sibling"
    placed = _client(FakeTransport(response=_ACCEPTED), store=store).domestic.stock("005930").reserve_buy(
        quantity=1, limit_price=70000, client_order_id=cid)
    sibling = _reserved_row(seq="42402", qty="1", unpr="70000")
    body = {"output": [sibling], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})

    assert _client(recon_t, store=store).orders.reconcile(cid, refresh=True) is None
    assert store.report_for(cid) is placed
    assert store.report_for(cid).order_id == "42401"


def test_reserve_refresh_confirms_stored_sequence(monkeypatch):
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-reserved-refresh-same"
    _client(FakeTransport(response=_ACCEPTED), store=store).domestic.stock("005930").reserve_buy(
        quantity=1, limit_price=70000, client_order_id=cid)
    body = {"output": [_reserved_row(seq="42401", qty="1", unpr="70000")],
            "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})

    report = _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    assert report is not None and report.order_id == "42401"


def test_reserve_refresh_returns_concurrent_rebind_generation(monkeypatch):
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-reserved-refresh-cas"
    _client(FakeTransport(response=_ACCEPTED), store=store).domestic.stock("005930").reserve_buy(
        quantity=1, limit_price=70000, client_order_id=cid)

    class RebindDuringRefreshTransport(FakeTransport):
        def request(self, **kwargs):
            prior = store.report_for(cid)
            fingerprint = store.fingerprint_for(cid)
            assert prior is not None and fingerprint is not None
            store.record(replace(prior, order_id="42499"), fingerprint)
            return super().request(**kwargs)

    body = {"output": [_reserved_row(seq="42401", qty="1", unpr="70000")],
            "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = RebindDuringRefreshTransport(
        by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})

    report = _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    assert report is not None and report.order_id == "42499"
    assert store.report_for(cid) is report


def test_reserve_reconcile_no_match_stays_in_flight():
    store = OrderStore()
    cid = "20240101-reserved-rc02"
    place_t = FakeTransport(raises=TransportTimeoutError("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    # 조회 결과가 비면 미접수로 단정하지 않는다 -> None, in-flight 유지
    empty_body = {"output": [], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=empty_body)})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None    # 여전히 in-flight


def test_reserve_reconcile_multi_match_raises(monkeypatch):
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-reserved-rc03"
    place_t = FakeTransport(response=_ACCEPTED)
    _client(place_t, store=store).domestic.stock("005930").reserve_buy(
        quantity=1, limit_price=70000, client_order_id=cid)
    row1 = _reserved_row(seq="42401", qty="1", unpr="70000")
    row2 = _reserved_row(seq="42401", qty="1", unpr="70000")
    row1["rsvn_ord_rcit_dt"] = row2["rsvn_ord_rcit_dt"] = "20240603"
    body = {"output": [row1, row2],
            "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    with pytest.raises(KISError):
        _client(recon_t, store=store).orders.reconcile(cid, refresh=True)


def test_reserve_reconcile_scans_all_pages_before_confirming(monkeypatch):
    # 재조회는 조기 종료 금지: 첫 페이지가 tr_cont 소진(D)이라도 연속조회 커서(ctx_area_nk200)가
    # 남아 있으면 계속 스캔해야 한다. 그러지 않으면 다음 페이지의 두 번째 일치 예약을 놓쳐 ≥2 모호를
    # 1건으로 오판, 접수 안 됐을 수도 있는 주문을 잘못 확정한다(이중발주 위험). 즉시/해외 재조회와
    # 같은 보수적 종료(tr_cont 소진 '그리고' 커서 소진일 때만 멈춤).
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-reserved-pg01"
    place_t = FakeTransport(response=_ACCEPTED)
    _client(place_t, store=store).domestic.stock("005930").reserve_buy(
        quantity=1, limit_price=70000, client_order_id=cid)
    row1 = _reserved_row(seq="42401", qty="1", unpr="70000")
    row2 = _reserved_row(seq="42401", qty="1", unpr="70000")
    row1["rsvn_ord_rcit_dt"] = row2["rsvn_ord_rcit_dt"] = "20240603"
    page1 = RawResponse(rt_cd="0", msg_cd="M", msg1="", tr_cont="D",
                        body={"output": [row1],
                              "ctx_area_nk200": "NEXT", "ctx_area_fk200": "FK"})
    page2 = RawResponse(rt_cd="0", msg_cd="M", msg1="", tr_cont="",
                        body={"output": [row2],
                              "ctx_area_nk200": "", "ctx_area_fk200": ""})
    recon_t = FakeTransport(by_path={_INQUIRE: [page1, page2]})
    with pytest.raises(KISError):                      # 두 페이지 모두 스캔 -> 2건 -> 모호로 거부
        _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    assert len(recon_t.calls) == 2                     # 커서가 남아 둘째 페이지도 조회했다


def test_reserve_same_id_different_order_conflicts_no_wire():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    cid = "20240101-reserved-cf01"
    t = _client(fake, store=store).domestic.stock("005930")
    t.reserve_buy(quantity=1, limit_price=70000, client_order_id=cid)
    with pytest.raises(KISUsageError):               # 같은 id, 다른 수량 -> 충돌
        t.reserve_buy(quantity=2, limit_price=70000, client_order_id=cid)
    assert len(fake.calls) == 1                      # 충돌은 와이어에 닿지 않는다


def test_reserve_and_immediate_same_id_cross_lifecycle_conflicts():
    # 같은 종목/수량/가격의 즉시주문과 예약주문을 같은 client_order_id 로 내면 지문이 달라 CONFLICT,
    # 두 번째(예약)는 와이어에 닿지 않는다.
    store = OrderStore()
    cid = "20240101-reserved-xl01"
    immediate = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="",
        body={"output": {"KRX_FWDG_ORD_ORGNO": "01", "ODNO": "0001", "ORD_TMD": "1"}}))
    _client(immediate, store=store).domestic.stock("005930").buy(quantity=1, limit_price=70000, client_order_id=cid)
    reserved = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(reserved, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    assert reserved.calls == []                      # 예약 POST 없음


def test_reserve_end_date_distinguishes_fingerprint():
    # 종료일만 다른 두 예약을 같은 id 로 내면 서로 다른 주문 -> 두 번째는 CONFLICT(silent replay 아님)
    store = OrderStore()
    cid = "20240101-reserved-ed01"
    t = _client(FakeTransport(response=_ACCEPTED), store=store).domestic.stock("005930")
    t.reserve_buy(quantity=1, limit_price=70000, end_date="20240605", client_order_id=cid)
    fake2 = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake2, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, end_date="20240610", client_order_id=cid)
    assert fake2.calls == []


@pytest.mark.parametrize("changed", [
    {"pdno": "000660"}, {"sll_buy_dvsn_cd": "01"}, {"ord_dvsn_cd": "01"},
    {"ord_rsvn_qty": "2"}, {"ord_rsvn_unpr": "70001"},
])
def test_reserve_reconcile_rejects_partial_match(monkeypatch, changed):
    # 지문의 한 필드라도 다른 행은 확정하지 않는다(오귀속 방지) -> None, in-flight 유지
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-reserved-pm01"
    place_t = FakeTransport(raises=TransportTimeoutError("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    row = _reserved_row(qty="1", unpr="70000")
    row["rsvn_ord_rcit_dt"] = "20240603"
    row.update(changed)
    body = {"output": [row], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None
    from kis_trader.domestic._engine.reserved_orders import _filter_matching_reserved
    assert _filter_matching_reserved([row], store.fingerprint_for(cid), {_dt.date(2024, 6, 3)}) == []
    assert recon_t.calls == []


def test_reserve_reconcile_skips_cancelled_same_fingerprint(monkeypatch):
    # 취소된 옛 예약(취소주문일자 cncl_ord_dt 채워짐)은 같은 지문이라도 매칭하지 않는다 -- 가짜 단일
    # 매칭으로 확정하면 이후 취소가 엉뚱한(취소된) 예약을 지목한다. -> None, in-flight 유지.
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore()
    cid = "20240101-reserved-cx01"
    place_t = FakeTransport(raises=TransportTimeoutError("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    row = _reserved_row(qty="1", unpr="70000")
    row["cncl_ord_dt"] = "20240603"                  # 취소된 예약(접수가 오늘이어도)
    row["rsvn_ord_rcit_dt"] = "20240603"
    body = {"output": [row], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None    # 여전히 in-flight
    from kis_trader.domestic._engine.reserved_orders import _filter_matching_reserved
    assert _filter_matching_reserved([row], store.fingerprint_for(cid), {_dt.date(2024, 6, 3)}) == []
    assert recon_t.calls == []


def test_reserve_reconcile_skips_stale_receipt_same_fingerprint(monkeypatch):
    # 넓은 조회창에 든 옛 접수일(rsvn_ord_rcit_dt)의 같은 지문 예약은 방금 발주분이 아니므로 제외.
    # -> None, in-flight 유지(미접수로 단정하지 않는다).
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)              # claim 시계를 프리즌 날짜(6/3)로 -- 창 위치 결정화
    cid = "20240101-reserved-sr01"
    place_t = FakeTransport(raises=TransportTimeoutError("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    row = _reserved_row(qty="1", unpr="70000")
    row["rsvn_ord_rcit_dt"] = "20240101"             # 오늘(20240603)·어제가 아님 -> 옛 예약
    body = {"output": [row], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None
    from kis_trader.domestic._engine.reserved_orders import _filter_matching_reserved
    assert _filter_matching_reserved([row], store.fingerprint_for(cid), {_dt.date(2024, 6, 3)}) == []
    assert recon_t.calls == []


def test_reserve_reconcile_skips_missing_receipt_same_fingerprint(monkeypatch):
    # prior 없는 타임아웃 복구에서 접수일자 없는 같은 지문 행은 옛 예약일 수 있으므로 확정하지 않는다.
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-reserved-mr01"
    place_t = FakeTransport(raises=TransportTimeoutError("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    row = _reserved_row(qty="1", unpr="70000")
    row["rsvn_ord_rcit_dt"] = ""
    body = {"output": [row], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None
    from kis_trader.domestic._engine.reserved_orders import _filter_matching_reserved
    assert _filter_matching_reserved([row], store.fingerprint_for(cid), {_dt.date(2024, 6, 3)}) == []
    assert recon_t.calls == []


def test_reserve_reconcile_confirms_fresh_receipt(monkeypatch):
    # 접수일자가 최근(오늘)이고 취소되지 않은 예약은 종전처럼 확정된다(가드가 정상분을 막지 않음).
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-reserved-fr01"
    place_t = FakeTransport(response=_ACCEPTED)
    _client(place_t, store=store).domestic.stock("005930").reserve_buy(
        quantity=1, limit_price=70000, client_order_id=cid)
    row = _reserved_row(seq="42401", qty="1", unpr="70000")
    row["rsvn_ord_rcit_dt"] = "20240603"             # 오늘 접수
    row["cncl_ord_dt"] = ""
    body = {"output": [row], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    report = _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    assert report is not None and report.order_id == "42401"


def test_reserve_reconcile_keeps_old_unknown_pending(monkeypatch):
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)  # 오늘=6/3
    placed_at = lambda: _dt.datetime(2024, 5, 30, 10, 0, tzinfo=_dt.timezone(_dt.timedelta(hours=9)))
    store = OrderStore(now=placed_at)                # claim(발주) 시각 = 5/30
    cid = "20240101-reserved-late01"
    place_t = FakeTransport(raises=TransportTimeoutError("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    row = _reserved_row(seq="42402", qty="1", unpr="70000")
    row["rsvn_ord_rcit_dt"] = "20240530"             # 발주일(5/30) 접수 -- reconcile 호출일(6/3) 창 밖
    row["cncl_ord_dt"] = ""
    body = {"output": [row], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    report = _client(recon_t, store=store).orders.reconcile(cid)
    assert report is None and recon_t.calls == []
    assert store.is_in_flight(cid) and store.report_for(cid) is None
    from kis_trader._internal._datetime import _reconcile_recency_window
    from kis_trader.domestic._engine.reserved_orders import _filter_matching_reserved
    dates = _reconcile_recency_window(_dt.date(2024, 5, 30), _dt.date(2024, 6, 3))
    assert _filter_matching_reserved([row], store.fingerprint_for(cid), dates) == [row]


def test_reserve_reconcile_window_and_process_params(monkeypatch):
    monkeypatch.setattr("kis_trader.domestic._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore()
    cid = "20240101-reserved-win01"
    place_t = FakeTransport(response=_ACCEPTED)
    _client(place_t, store=store).domestic.stock("005930").reserve_buy(
        quantity=1, limit_price=70000, client_order_id=cid)
    body = {"output": [], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    params = recon_t.calls[0]["params"]
    # 고정된 오늘 20240603 기준 양방향 창(오늘-7 ~ 오늘+31), 처리/미처리 모두(0)
    assert params["RSVN_ORD_ORD_DT"] == "20240527"   # 20240603 - 7
    assert params["RSVN_ORD_END_DT"] == "20240704"   # 20240603 + 31
    assert params["PRCS_DVSN_CD"] == "0"             # all


def test_reserve_reconcile_ignores_blank_sequence_row():
    # 순번 없는 매칭 행은 정정·취소가 불가라 확정하지 않는다 -> None, in-flight 유지
    store = OrderStore()
    cid = "20240101-reserved-bs01"
    place_t = FakeTransport(raises=TransportTimeoutError("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=70000, client_order_id=cid)
    row = _reserved_row(seq="", qty="1", unpr="70000")
    body = {"output": [row], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_INQUIRE: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None


def test_reserve_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_ACCEPTED), account=None).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=1)


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("risk", [
    RiskLimits(max_order_quantity=1),
    RiskLimits(max_order_notional=100),
    RiskLimits(max_order_quantity=1, max_order_notional=100),
])
def test_reserved_create_risk_rejects_before_wire_or_claim(side, risk):
    fake = FakeTransport(response=_ACCEPTED)
    store = OrderStore()
    cid = "reserved-risk-reject"
    with pytest.raises(PreTradeRiskError):
        getattr(_client(fake, store=store, risk=risk).domestic.stock("005930"), "reserve_" + side)(
            quantity=1000, limit_price=70000, client_order_id=cid,
        )
    assert fake.calls == []
    assert store.snapshot_for(cid) == (None, None)
    assert not store.is_in_flight(cid)


@pytest.mark.parametrize("risk, price", [
    (RiskLimits(max_order_notional=100), None),
    (RiskLimits(price_collar_percent=10), 100),
    (RiskLimits(enforce_tick_size=True), 70001),
])
def test_reserved_create_uncheckable_or_tick_risk_rejects_without_claim(risk, price):
    fake = FakeTransport(response=_ACCEPTED)
    store = OrderStore()
    with pytest.raises(PreTradeRiskError):
        _client(fake, store=store, risk=risk).domestic.stock("005930").reserve_buy(
            quantity=1, limit_price=price, client_order_id="uncheckable",
        )
    assert fake.calls == []
    assert store.snapshot_for("uncheckable") == (None, None)


@pytest.mark.parametrize("price", [None, "100"])
def test_reserved_create_quantity_and_fixed_notional_equality_allows_normalized_wire(price):
    fake = FakeTransport(response=_ACCEPTED)
    risk = RiskLimits(max_order_quantity=2, max_order_notional=None if price is None else 200)
    report = _client(fake, risk=risk).domestic.stock("005930").reserve_buy(
        quantity=Decimal("2.0"), limit_price=price,
    )
    assert report.status is OrderStatus.PENDING_NEW
    assert len(fake.calls) == 1
    assert fake.calls[0]["body"]["ORD_QTY"] == "2.0"
    assert fake.calls[0]["body"]["ORD_UNPR"] == ("0" if price is None else "100")


def test_reserved_create_rejected_id_not_claimed_and_can_be_used_with_compliant_terms(monkeypatch):
    fake = FakeTransport(response=_ACCEPTED)
    store = OrderStore()
    claims = []
    original_claim = store.try_claim

    def claim(*args, **kwargs):
        claims.append(args)
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(store, "try_claim", claim)
    stock = _client(fake, store=store, risk=RiskLimits(max_order_quantity=1)).domestic.stock("005930")
    with pytest.raises(PreTradeRiskError):
        stock.reserve_buy(quantity=2, limit_price=100, client_order_id="reusable")
    assert claims == []
    report = stock.reserve_buy(quantity=1, limit_price=100, client_order_id="reusable")
    assert report.order_id == "42401"
    assert len(claims) == len(fake.calls) == 1


def test_reserved_completed_replay_precedes_newly_incompatible_risk():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    report = _client(fake, store=store).domestic.stock("005930").reserve_buy(
        quantity=1000, limit_price=70000, client_order_id="completed-risk",
    )
    stricter = _client(fake, store=store, risk=RiskLimits(max_order_quantity=1, price_collar_percent=1))
    assert stricter.domestic.stock("005930").reserve_buy(
        quantity=1000, limit_price=70000, client_order_id="completed-risk",
    ) is report
    assert len(fake.calls) == 1


@pytest.mark.parametrize("quantity, message", [(1000, "결과가 확인"), (1001, "다른 주문")])
def test_reserved_pending_or_conflict_precedes_new_risk(quantity, message):
    store = OrderStore()
    fake = FakeTransport(raises=TransportTimeoutError("unknown"))
    with pytest.raises(OrderTimeoutError):
        _client(fake, store=store).domestic.stock("005930").reserve_buy(
            quantity=1000, limit_price=70000, client_order_id="pending-risk",
        )
    before = store.snapshot_for("pending-risk")
    stricter = _client(fake, store=store, risk=RiskLimits(max_order_quantity=1))
    with pytest.raises(KISUsageError, match=message):
        stricter.domestic.stock("005930").reserve_buy(
            quantity=quantity, limit_price=70000, client_order_id="pending-risk",
        )
    assert store.is_in_flight("pending-risk")
    assert store.snapshot_for("pending-risk") == before
    assert len(fake.calls) == 1


def test_reserved_risk_checked_concurrent_fresh_claim_sends_once(monkeypatch):
    from kis_trader.domestic._engine import reserved_orders as engine

    fake = FakeTransport(response=_ACCEPTED)
    stock = _client(fake, risk=RiskLimits(max_order_quantity=1)).domestic.stock("005930")
    barrier = threading.Barrier(2)
    original = engine._check_reserved_risk

    def check(*args, **kwargs):
        original(*args, **kwargs)
        barrier.wait(timeout=2)

    monkeypatch.setattr(engine, "_check_reserved_risk", check)
    reports, errors = [], []

    def reserve():
        try:
            reports.append(stock.reserve_buy(quantity=1, limit_price=100, client_order_id="risk-race"))
        except KISUsageError as error:
            errors.append(error)

    workers = [threading.Thread(target=reserve, daemon=True) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
            assert not worker.is_alive()
        assert len(fake.calls) == 1
        assert reports and len(reports) + len(errors) == 2
    finally:
        barrier.abort()
        for worker in workers:
            worker.join(timeout=1)


@pytest.mark.parametrize("risk", [RiskLimits(max_order_quantity=1), RiskLimits(max_order_notional=199)])
def test_reserved_create_one_unit_above_configured_cap_rejected(risk):
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(PreTradeRiskError):
        _client(fake, risk=risk).domestic.stock("005930").reserve_buy(quantity=2, limit_price=100)
    assert fake.calls == []
