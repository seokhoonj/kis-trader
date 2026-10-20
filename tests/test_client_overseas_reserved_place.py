"""미국 해외예약주문 발주 -- kis.overseas.stock("AAPL", exchange="NAS").reserve_buy/sell (order-resv TTTT3014U/3016U).

즉시주문과 별개 라이프사이클(예약번호·체결개념 없음)이지만 이중발주 방지·재시도 금지·보수적
재조회(예약주문조회 기반)를 공유한다. 네트워크 없이 가짜 전송으로 안전 불변식을 검증한다.
"""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import replace

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


def _client(transport, *, environment="real", account="12345678-01", store=None, orderable=True, risk=None):
    return KISClient(app_key="k", app_secret="s", account=account, environment=environment,
                     transport=transport, store=store, orderable=orderable, risk=risk)


def test_overseas_reserved_orders_rejects_span_over_6_days():
    # KIS 는 해외 예약조회 7일 이상을 거부한다("조회기간 7일 이상 불가", 라이브 확인 2026-10-14) --
    # 7일 이상 기간은 서버에 보내 불투명 오류를 받기 전에 명확한 KISUsageError 로 거른다.
    with pytest.raises(KISUsageError, match="7일"):
        _client(FakeTransport(response=_ACCEPTED)).account.overseas.reserved_orders(
            start="20240101", end="20240115")       # 14일 -- 한도 초과 -> 와이어 전 거부


# --- 정상 전송 -------------------------------------------------------------
def test_overseas_reserve_buy_wire():
    fake = FakeTransport(response=_ACCEPTED)
    report = _client(fake).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price="150.25")
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


def test_overseas_reserve_non_timeout_transport_error_stays_in_flight():
    # 비-200 처리(해외 예약경로): claim 뒤 비-200(KIS 스로틀=HTTP 500)은 접수 여부 불명 -> OrderTimeoutError,
    # in-flight 유지. _submit_reserved 가 이걸 안 잡으면 접수됐을 수 있는 예약을 호출자가 새 id 로
    # 재전송해 이중 예약이 난다(즉시주문 경로엔 있던 가드가 예약경로엔 빠져 있었다).
    fake = FakeTransport(by_path={_PLACE: KISError("KIS HTTP 요청에 실패했다 (status 500).")})
    store = OrderStore()
    kis = _client(fake, store=store)
    with pytest.raises(OrderTimeoutError):
        kis.overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, limit_price="150", client_order_id="rsv-1")
    assert store.is_in_flight("rsv-1")                   # in-flight 유지 -> 조용한 재전송 차단


def test_overseas_reserve_auth_error_clears_in_flight_and_allows_retry():
    # 401(인증 단계 거부)은 예약이 엔진에 닿지 못했음이 확실 -> in-flight 해제 + KISAuthError 그대로.
    # 즉시주문 경로엔 있던 이 가드가 해외 예약경로엔 빠져 있어, 토큰 교정 후 같은 id 재시도가 막혔었다.
    fake = FakeTransport(by_path={_PLACE: [KISAuthError("KIS HTTP 인증에 실패했다 (status 401)."), _ACCEPTED]})
    store = OrderStore()
    kis = _client(fake, store=store)
    with pytest.raises(KISAuthError):                    # 승격 안 함
        kis.overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, limit_price="150", client_order_id="rsv-auth")
    assert not store.is_in_flight("rsv-auth")            # in-flight 해제됨 -> 재시도 가능
    kis.overseas.stock("AAPL", exchange="NAS").reserve_buy(
        quantity=1, limit_price="150", client_order_id="rsv-auth")   # 같은 id 재전송 성공
    assert len([c for c in fake.calls if c["path"] == _PLACE]) == 2


def test_overseas_reserve_sell_uses_sell_tr():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake).overseas.stock("AAPL", exchange="NYS").reserve_sell(quantity=2, limit_price="150")
    assert fake.calls[0]["tr_id"] == "TTTT3016U"    # 미국 예약 매도
    assert fake.calls[0]["body"]["OVRS_EXCG_CD"] == "NYSE"


# --- 검증/거부 -------------------------------------------------------------
def test_overseas_reserve_unknown_exchange_rejected():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):   # 미지원 거래소코드는 와이어 전에 거부
        _client(fake).overseas.stock("AAPL", exchange="XXX").reserve_buy(quantity=1, limit_price="1")
    assert fake.calls == []


def test_overseas_reserve_requires_price():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):   # 지정가만
        _client(fake).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1)
    assert fake.calls == []


# --- 모의(paper) -- 원장상 발주는 V* TR 로 지원, 조회(재조회)만 실전전용 ----
def test_overseas_reserve_paper_place_allowed_uses_v_tr():
    fake = FakeTransport(response=_ACCEPTED)
    report = _client(fake, environment="paper").overseas.stock("AAPL", exchange="NAS").reserve_buy(
        quantity=1, limit_price="148")
    assert report.status is OrderStatus.PENDING_NEW
    assert fake.calls[0]["tr_id"] == "VTTT3014U"     # 모의 미국 예약 매수


def test_overseas_reserve_paper_sell_uses_v_tr():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake, environment="paper").overseas.stock("AAPL", exchange="NYS").reserve_sell(
        quantity=1, limit_price="148")
    assert fake.calls[0]["tr_id"] == "VTTT3016U"     # 모의 미국 예약 매도


def test_overseas_reserve_reconcile_paper_fails_closed():
    # 발주는 paper 가 되지만 예약주문조회는 실전전용 -> paper 재조회는 조회 와이어 전에 fail-closed
    # (in-flight 유지, 재전송 금지).
    store = OrderStore()
    cid = "20240101-ovsresv-pp01"
    place_t = FakeTransport(raises=TransportTimeoutError("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store, environment="paper").overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, limit_price="150", client_order_id=cid)
    recon_t = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError, match="실전"):
        _client(recon_t, store=store, environment="paper").orders.reconcile(cid)
    assert recon_t.calls == []
    assert store.fingerprint_for(cid) is not None    # in-flight 유지


def test_overseas_reserve_non_orderable_rejected():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(AccountNotOrderableError):
        _client(fake, orderable=False).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price="1")
    assert fake.calls == []


def test_overseas_reserve_missing_id_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": {}}, tr_cont="")
    with pytest.raises(OrderError):
        _client(FakeTransport(response=resp)).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price="1")


# --- 안전 불변식 -----------------------------------------------------------
def test_overseas_reserve_rejected_clears_in_flight_and_reusable():
    store = OrderStore()
    cid = "20240101-ovsresv-rej01"
    with pytest.raises(OrderRejectedError):
        _client(FakeTransport(response=_REJECTED), store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, limit_price="150", client_order_id=cid)
    assert store.fingerprint_for(cid) is None
    report = _client(FakeTransport(response=_ACCEPTED), store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
        quantity=1, limit_price="150", client_order_id=cid)
    assert report.order_id == "0031111234"


def test_overseas_reserve_timeout_no_retry():
    store = OrderStore()
    fake = FakeTransport(raises=TransportTimeoutError("t"))
    cid = "20240101-ovsresv-to01"
    with pytest.raises(OrderTimeoutError):
        _client(fake, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, limit_price="150", client_order_id=cid)
    assert len(fake.calls) == 1
    assert store.fingerprint_for(cid).exchange == "overseas-reserved"


def test_overseas_reserve_replay_same_id():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    cid = "20240101-ovsresv-rp01"
    t = _client(fake, store=store).overseas.stock("AAPL", exchange="NAS")
    r1 = t.reserve_buy(quantity=1, limit_price="150", client_order_id=cid)
    r2 = t.reserve_buy(quantity=1, limit_price="150", client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert len(fake.calls) == 1


# --- 검증 경계 + 충돌 (B2 보강) --------------------------------------------
@pytest.mark.parametrize("bad_qty", ["0", "-1", "1.5"])
def test_overseas_reserve_invalid_quantity_before_wire(bad_qty):
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=bad_qty, limit_price="150")
    assert fake.calls == []


@pytest.mark.parametrize("bad_price", ["0", "-1"])
def test_overseas_reserve_nonpositive_price_before_wire(bad_price):
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price=bad_price)
    assert fake.calls == []


def test_overseas_reserve_same_id_different_order_conflicts_no_wire():
    store = OrderStore()
    cid = "20240101-ovsresv-cf01"
    fake = FakeTransport(response=_ACCEPTED)
    t = _client(fake, store=store).overseas.stock("AAPL", exchange="NAS")
    t.reserve_buy(quantity=1, limit_price="150", client_order_id=cid)
    with pytest.raises(KISUsageError):       # 다른 수량 -> 지문 불일치 -> 충돌
        t.reserve_buy(quantity=2, limit_price="150", client_order_id=cid)
    assert len(fake.calls) == 1


def test_overseas_immediate_and_reserved_same_id_cross_lifecycle_conflict():
    store = OrderStore()
    cid = "20240101-ovsresv-xl01"
    immediate = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="", body={"output": {"ODNO": "0001", "ORD_TMD": "1"}}))
    _client(immediate, store=store).overseas.stock("AAPL", exchange="NAS").buy(
        quantity=1, limit_price="150", client_order_id=cid)
    reserved = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):       # 즉시 vs 예약(세션 다름) -> 충돌
        _client(reserved, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, limit_price="150", client_order_id=cid)
    assert reserved.calls == []


# --- reconcile (예약주문조회 기반) -----------------------------------------
def _frozen_now():
    """store claim 시계를 reconcile 날짜창(_FrozenDatetime, 2024-06-03 KST)과 같은 날로 고정 --
    예약 reconcile 의 접수일자 매칭 창이 claim 발주일에 앵커하므로 store 시각도 결정적이어야 한다."""
    return _dt.datetime(2024, 6, 3, 10, 0, tzinfo=_dt.timezone(_dt.timedelta(hours=9)))


def test_overseas_reserve_reconcile_confirms_single_match(monkeypatch):
    monkeypatch.setattr("kis_trader.overseas._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-ovsresv-rc01"
    place_t = FakeTransport(response=_ACCEPTED)
    _client(place_t, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
        quantity=1, limit_price="150", client_order_id=cid)
    body = {"output": [_list_row(qty="1", unpr="150")], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_LIST: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    report = _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    assert report is not None
    assert report.order_id == "0031111234"
    assert all(c["method"] == "GET" for c in recon_t.calls)
    # 발주일(claim=20240603)에 앵커한 ≤6일폭 창(KIS 7일-이상 금지): start=발주일-1=6/2, end=발주일+5=6/8
    assert recon_t.calls[0]["params"]["INQR_STRT_DT"] == "20240602"
    assert recon_t.calls[0]["params"]["INQR_END_DT"] == "20240608"


def test_overseas_reserve_refresh_does_not_rebind_identical_sibling(monkeypatch):
    monkeypatch.setattr("kis_trader.overseas._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-ovsresv-refresh-sibling"
    placed = _client(FakeTransport(response=_ACCEPTED), store=store).overseas.stock(
        "AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price="150", client_order_id=cid)
    body = {"output": [_list_row(odno="0031111299", qty="1", unpr="150")],
            "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_LIST: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})

    assert _client(recon_t, store=store).orders.reconcile(cid, refresh=True) is None
    assert store.report_for(cid) is placed
    assert store.report_for(cid).order_id == "0031111234"


@pytest.mark.parametrize("legacy_venue", [False, True])
def test_overseas_reserve_refresh_confirms_stored_sequence(monkeypatch, legacy_venue):
    monkeypatch.setattr("kis_trader.overseas._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-ovsresv-refresh-same"
    _client(FakeTransport(response=_ACCEPTED), store=store).overseas.stock(
        "AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price="150", client_order_id=cid)
    if legacy_venue:
        prior, fingerprint = store.snapshot_for(cid)
        store.record(prior, replace(fingerprint, overseas_exchange=""))
    body = {"output": [_list_row(odno="0031111234", qty="1", unpr="150")],
            "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_LIST: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})

    report = _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    assert report is not None and report.order_id == "0031111234"


def test_overseas_reserve_refresh_returns_concurrent_rebind_generation(monkeypatch):
    monkeypatch.setattr("kis_trader.overseas._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-ovsresv-refresh-cas"
    _client(FakeTransport(response=_ACCEPTED), store=store).overseas.stock(
        "AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price="150", client_order_id=cid)

    class RebindDuringRefreshTransport(FakeTransport):
        def request(self, **kwargs):
            prior = store.report_for(cid)
            fingerprint = store.fingerprint_for(cid)
            assert prior is not None and fingerprint is not None
            store.record(replace(prior, order_id="0031111299"), fingerprint)
            return super().request(**kwargs)

    body = {"output": [_list_row(odno="0031111234", qty="1", unpr="150")],
            "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = RebindDuringRefreshTransport(
        by_path={_LIST: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})

    report = _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    assert report is not None and report.order_id == "0031111299"
    assert store.report_for(cid) is report


def test_overseas_reserve_reconcile_ignores_canceled_mismatch_and_old_receipt(monkeypatch):
    monkeypatch.setattr("kis_trader.overseas._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)              # claim 시계를 프리즌 날짜(6/3)로 -- 창 위치 결정화
    cid = "20240101-ovsresv-rc02"
    place_t = FakeTransport(raises=TransportTimeoutError("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, limit_price="150", client_order_id=cid)
    # 취소된 예약 + 수량 다른 예약 + 접수일자가 오래된 동일지문 예약 -> 매칭 0 -> None(in-flight 유지)
    rows = [_list_row(qty="1", unpr="150", cncl="Y"),
            _list_row(qty="9", unpr="150"),
            _list_row(qty="1", unpr="150", receipt="20240401")]  # 옛 접수 -> 배제
    body = {"output": rows, "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_LIST: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None
    assert store.fingerprint_for(cid) is not None
    from kis_trader.overseas._engine.reserved_orders import _filter_matching
    assert _filter_matching(rows, store.fingerprint_for(cid), {_dt.date(2024, 6, 3)}) == []
    assert recon_t.calls == []


def test_overseas_reserve_reconcile_multi_match_raises(monkeypatch):
    monkeypatch.setattr("kis_trader.overseas._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-ovsresv-rc03"
    place_t = FakeTransport(response=_ACCEPTED)
    _client(place_t, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
        quantity=1, limit_price="150", client_order_id=cid)
    rows = [_list_row(odno="0031111234", qty="1", unpr="150"),
            _list_row(odno="0031111234", qty="1", unpr="150")]
    body = {"output": rows, "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(by_path={_LIST: RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body)})
    with pytest.raises(KISError):
        _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    assert store.fingerprint_for(cid) is not None


def test_overseas_reserve_reconcile_scans_all_pages_before_confirming(monkeypatch):
    # 재조회는 조기 종료 금지: 첫 페이지 tr_cont 소진(D)이라도 커서(ctx_area_nk200)가 남으면 계속
    # 스캔해야 둘째 페이지의 두 번째 일치를 본다. 그러지 않으면 ≥2 모호를 1건으로 오판해 접수 여부
    # 불명 주문을 잘못 확정한다(이중발주 위험). 국내 예약/즉시 재조회와 같은 보수적 종료.
    monkeypatch.setattr("kis_trader.overseas._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore(now=_frozen_now)
    cid = "20240101-ovsresv-pg01"
    place_t = FakeTransport(response=_ACCEPTED)
    _client(place_t, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
        quantity=1, limit_price="150", client_order_id=cid)
    page1 = RawResponse(rt_cd="0", msg_cd="M", msg1="", tr_cont="D",
                        body={"output": [_list_row(odno="0031111234", qty="1", unpr="150")],
                              "ctx_area_nk200": "NEXT", "ctx_area_fk200": "FK"})
    page2 = RawResponse(rt_cd="0", msg_cd="M", msg1="", tr_cont="",
                        body={"output": [_list_row(odno="0031111234", qty="1", unpr="150")],
                              "ctx_area_nk200": "", "ctx_area_fk200": ""})
    recon_t = FakeTransport(by_path={_LIST: [page1, page2]})
    with pytest.raises(KISError):                      # 두 페이지 모두 스캔 -> 2건 -> 모호로 거부
        _client(recon_t, store=store).orders.reconcile(cid, refresh=True)
    assert len(recon_t.calls) == 2                     # 커서가 남아 둘째 페이지도 조회했다


def test_overseas_reserve_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_ACCEPTED), account=None).overseas.stock(
            "AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price="1")


@pytest.mark.parametrize("exchange", ["NAS", "NYS", "AMS"])
@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("risk", [
    RiskLimits(max_order_quantity=1, max_order_notional_by_currency={"USD": 100}),
    RiskLimits(max_order_notional_by_currency={"USD": 19}),
])
def test_us_reserved_quantity_or_usd_notional_risk_rejects_without_claim(exchange, side, risk):
    fake = FakeTransport(response=_ACCEPTED)
    store = OrderStore()
    with pytest.raises(PreTradeRiskError):
        getattr(_client(fake, store=store, risk=risk).overseas.stock("AAPL", exchange=exchange),
                "reserve_" + side)(quantity=2, limit_price=10, client_order_id="us-risk-reject")
    assert fake.calls == []
    assert store.snapshot_for("us-risk-reject") == (None, None)
    assert not store.is_in_flight("us-risk-reject")


@pytest.mark.parametrize("risk", [
    RiskLimits(max_order_notional=1000000),
    RiskLimits(max_order_notional_by_currency={"EUR": 1000000}),
    RiskLimits(max_order_notional_by_currency={"USD": 100}, price_collar_percent=10),
])
def test_us_reserved_missing_currency_or_reference_rejects_without_quote(risk):
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(PreTradeRiskError):
        _client(fake, risk=risk).overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price=10)
    assert fake.calls == []


@pytest.mark.parametrize("exchange", ["NAS", "NYS", "AMS"])
def test_us_reserved_risk_equality_uses_usd_not_krw_and_preserves_wire(exchange):
    fake = FakeTransport(response=_ACCEPTED)
    risk = RiskLimits(max_order_quantity=2, max_order_notional=1, max_order_notional_by_currency={"USD": 20})
    report = _client(fake, risk=risk).overseas.stock("AAPL", exchange=exchange).reserve_buy(
        quantity="2", limit_price="10",
    )
    assert report.status is OrderStatus.PENDING_NEW
    assert len(fake.calls) == 1
    assert fake.calls[0]["tr_id"] == "TTTT3014U"
    assert fake.calls[0]["body"]["FT_ORD_QTY"] == "2"
    assert fake.calls[0]["body"]["FT_ORD_UNPR3"] == "10"


def test_us_reserved_replay_precedes_new_risk_and_rejected_id_is_reusable(monkeypatch):
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    original = _client(fake, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
        quantity=2, limit_price=10, client_order_id="us-completed",
    )
    stock = _client(fake, store=store, risk=RiskLimits(max_order_quantity=1)).overseas.stock("AAPL", exchange="NAS")
    assert stock.reserve_buy(quantity=2, limit_price=10, client_order_id="us-completed") is original
    claims = []
    original_claim = store.try_claim

    def claim(*args, **kwargs):
        claims.append(args)
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(store, "try_claim", claim)
    with pytest.raises(PreTradeRiskError):
        stock.reserve_buy(quantity=2, limit_price=10, client_order_id="us-reusable")
    assert claims == []
    assert store.snapshot_for("us-reusable") == (None, None)
    compliant = _client(fake, store=store, risk=RiskLimits(max_order_notional_by_currency={"USD": 20}))
    compliant.overseas.stock("AAPL", exchange="NAS").reserve_buy(
        quantity=2, limit_price=10, client_order_id="us-reusable",
    )
    assert len(claims) == 1
    assert len(fake.calls) == 2


@pytest.mark.parametrize("quantity, message", [(2, "결과가 확인"), (3, "다른 주문")])
def test_us_reserved_pending_and_conflict_precede_risk(quantity, message):
    store = OrderStore()
    fake = FakeTransport(raises=TransportTimeoutError("unknown"))
    with pytest.raises(OrderTimeoutError):
        _client(fake, store=store).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=2, limit_price=10, client_order_id="us-pending-risk",
        )
    before = store.snapshot_for("us-pending-risk")
    with pytest.raises(KISUsageError, match=message):
        _client(fake, store=store, risk=RiskLimits(max_order_quantity=1)).overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=quantity, limit_price=10, client_order_id="us-pending-risk",
        )
    assert store.snapshot_for("us-pending-risk") == before
    assert store.is_in_flight("us-pending-risk")
    assert len(fake.calls) == 1


@pytest.mark.parametrize("exchange,wire", [("NAS", "NASD"), ("NYS", "NYSE"), ("AMS", "AMEX")])
def test_us_reserved_identity_persists_actual_wire_venue(tmp_path, exchange, wire):
    path = tmp_path / "venue.json"
    transport = FakeTransport(response=_ACCEPTED)
    with OrderStore(path=path) as store:
        kis = KISClient(app_key="k", app_secret="s", account="12345678-01", environment="real",
                        transport=transport, store=store)
        stock = kis.overseas.stock("AAPL", exchange=exchange)
        first = stock.reserve_buy(quantity=1, limit_price=150, client_order_id="venue")
        assert stock.reserve_buy(quantity=1, limit_price=150, client_order_id="venue") == first
        assert transport.calls[0]["body"]["OVRS_EXCG_CD"] == wire
        for other in ("NAS", "NYS", "AMS"):
            if other != exchange:
                with pytest.raises(KISUsageError):
                    kis.overseas.stock("AAPL", exchange=other).reserve_buy(
                        quantity=1, limit_price=150, client_order_id="venue")
        assert len(transport.calls) == 1
        assert store.snapshot_for("venue")[1].overseas_exchange == wire
    with OrderStore(path=path) as reopened:
        assert reopened.snapshot_for("venue")[1].overseas_exchange == wire


def test_us_reserved_legacy_blank_venue_replay_conflicts():
    transport = FakeTransport(response=_ACCEPTED)
    store = OrderStore()
    kis = KISClient(app_key="k", app_secret="s", account="12345678-01", environment="real",
                    transport=transport, store=store)
    stock = kis.overseas.stock("AAPL", exchange="NAS")
    first = stock.reserve_buy(quantity=1, limit_price=150, client_order_id="legacy-venue")
    _, fingerprint = store.snapshot_for("legacy-venue")
    legacy = replace(fingerprint, overseas_exchange="")
    store.record(first, legacy)
    with pytest.raises(KISUsageError):
        stock.reserve_buy(quantity=1, limit_price=150, client_order_id="legacy-venue")
    assert store.snapshot_for("legacy-venue")[0] == first
    assert len(transport.calls) == 1
