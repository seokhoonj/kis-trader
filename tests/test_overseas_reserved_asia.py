"""해외 아시아(홍콩/중국/일본/베트남) 예약주문 -- 발주/취소/조회/재조회 (TTTS3013U/TTTS3014R).

원장(KIS 공식 전체문서, 시트 `해외주식 예약주문접수/조회/접수취소`) 기준: 아시아 예약은
발주·취소가 TR 하나(TTTS3013U, RVSE_CNCL_DVSN_CD 00/02)를 공유하고, 상품유형코드(PRDT_TYPE_CD)는
거래소에서 파생된다 -- 515 일본 / 551 상해A / 552 심천A / 507 하노이 / 508 호치민, 홍콩만 통화별
(501 HKD / 543 CNY / 558 USD). 조회(TTTS3014R)는 실전전용. 픽스처는 원장 응답예시 실값을 쓴다.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from kis_trader import KISClient, OrderStatus, OrderStore
from kis_trader.errors import (
    KISError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
)
from kis_trader.overseas._engine import reserved_orders as ro
from kis_trader.transport import RawResponse, TransportTimeout

_PLACE = "/uapi/overseas-stock/v1/trading/order-resv"
_LIST = "/uapi/overseas-stock/v1/trading/order-resv-list"


# --- PRDT_TYPE_CD 파생 ------------------------------------------------------
def test_asia_prdt_type_cd_by_exchange():
    # 홍콩 외 아시아는 통화 선택이 없어 currency=None -- 거래소코드만으로 상품유형이 정해진다.
    assert ro._asia_prdt_type_cd("TSE", None) == "515"   # 일본
    assert ro._asia_prdt_type_cd("SHS", None) == "551"   # 중국 상해A
    assert ro._asia_prdt_type_cd("SZS", None) == "552"   # 중국 심천A
    assert ro._asia_prdt_type_cd("HNX", None) == "507"   # 베트남 하노이
    assert ro._asia_prdt_type_cd("HSX", None) == "508"   # 베트남 호치민


def test_asia_prdt_type_cd_hong_kong_currency():
    assert ro._asia_prdt_type_cd("HKS", "HKD") == "501"
    assert ro._asia_prdt_type_cd("HKS", "CNY") == "543"
    assert ro._asia_prdt_type_cd("HKS", "USD") == "558"


def test_asia_prdt_type_cd_hong_kong_rejects_unknown_currency():
    with pytest.raises(KISUsageError, match="HKD/CNY/USD"):
        ro._asia_prdt_type_cd("HKS", "JPY")


def test_asia_prdt_type_cd_hong_kong_none_defaults_hkd():
    # 홍콩은 currency 미지정(None)이면 HKD(501) 로 본다.
    assert ro._asia_prdt_type_cd("HKS", None) == "501"


def test_asia_prdt_type_cd_unknown_exchange_rejected():
    with pytest.raises(KISUsageError, match="거래소"):
        ro._asia_prdt_type_cd("NAS", None)   # 미국은 아시아 파생 대상이 아니다


# --- 픽스처 ----------------------------------------------------------------
class _Fake:
    """가짜 전송 -- 고정 응답(response) 또는 예외(raises)를 돌려주고 호출을 기록한다."""

    def __init__(self, response=None, *, raises=None):
        self.response = response
        self.raises = raises
        self.calls: list[dict] = []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                           "params": params, "body": body, "idempotent": idempotent})
        if self.raises is not None:
            raise self.raises
        assert self.response is not None
        return self.response


class _FrozenDatetime(_dt.datetime):
    """now() 만 고정 -- reconcile 날짜창·접수일자 판별 결정성 확보."""

    @classmethod
    def now(cls, tz=None):
        return _dt.datetime(2024, 6, 3, 10, 0, tzinfo=tz)


def _place_resp():
    # 원장 응답예시(TTTS3013U) 실값 -- output = 예약번호(OVRS_RSVN_ODNO) + 접수일자(RSVN_ORD_RCIT_DT).
    return RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="정상",
                       body={"output": {"OVRS_RSVN_ODNO": "0030138295",
                                        "RSVN_ORD_RCIT_DT": "20260818"}})


def _place(fake, store, **overrides):
    kwargs = {"symbol": "00700", "side": "buy", "quantity": 100, "limit_price": 350.0,
              "exchange": "HKS", "currency": None, "client_order_id": "c1",
              "cano": "12345678", "product_code": "01", "environment": "real"}
    kwargs.update(overrides)
    return ro.place_overseas_reserved_order(fake, store, **kwargs)


def _client(transport, store, *, environment="real"):
    return KISClient(app_key="k", app_secret="s", account="12345678-01",
                     environment=environment, transport=transport, store=store)


def _list_row(**overrides):
    # 아시아 예약주문조회(TTTS3014R) 행 -- 미국(TTTT3039R)과 같은 소문자 키 스키마.
    row = {"ovrs_rsvn_odno": "0030138295", "pdno": "00700", "sll_buy_dvsn_cd": "02",
           "ft_ord_qty": "100", "ft_ord_unpr3": "350", "ft_ccld_qty": "0", "cncl_yn": "N",
           "ovrs_excg_cd": "SEHK", "prdt_name": "텐센트", "ovrs_rsvn_ord_stat_cd_name": "접수",
           "rsvn_ord_rcit_dt": "20240603", "ord_dt": "", "odno": "", "nprc_rson_text": ""}
    row.update(overrides)
    return row


def _list_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output": rows, "ctx_area_nk200": "", "ctx_area_fk200": ""})


# --- 발주 (TTTS3013U) -------------------------------------------------------
def test_asia_place_builds_ttts3013u_body_and_stores_receipt_date():
    fake = _Fake(_place_resp())
    store = OrderStore()
    rep = _place(fake, store)
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _PLACE
    assert call["idempotent"] is False
    assert call["tr_id"] == "TTTS3013U"
    assert call["body"]["SLL_BUY_DVSN_CD"] == "02"        # 매수
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "00"      # 발주
    assert call["body"]["OVRS_EXCG_CD"] == "SEHK"
    assert call["body"]["PRDT_TYPE_CD"] == "501"          # 홍콩 / HKD
    assert call["body"]["FT_ORD_QTY"] == "100"
    assert call["body"]["ORD_SVR_DVSN_CD"] == "0"
    assert rep.order_id == "0030138295"
    assert rep.receipt_date == "20260818"
    assert rep.status is OrderStatus.PENDING_NEW
    fp = store.fingerprint_for("c1")
    assert fp.exchange == "overseas-reserved-asia"
    assert fp.overseas_exchange == "HKS"


def test_asia_place_sell_uses_sell_code():
    fake = _Fake(_place_resp())
    _place(fake, OrderStore(), side="sell")
    assert fake.calls[0]["body"]["SLL_BUY_DVSN_CD"] == "01"


def test_asia_place_paper_uses_v_tr():
    fake = _Fake(_place_resp())
    rep = _place(fake, OrderStore(), environment="paper")
    assert fake.calls[0]["tr_id"] == "VTTS3013U"
    assert rep.status is OrderStatus.PENDING_NEW


def test_asia_place_hong_kong_currency_override():
    fake = _Fake(_place_resp())
    _place(fake, OrderStore(), currency="CNY")
    assert fake.calls[0]["body"]["PRDT_TYPE_CD"] == "543"  # 홍콩 CNY


@pytest.mark.parametrize(("exchange", "wire_exchange", "prdt_type_cd"), [
    ("TSE", "TKSE", "515"),
    ("SHS", "SHAA", "551"),
    ("SZS", "SZAA", "552"),
    ("HNX", "HASE", "507"),
    ("HSX", "VNSE", "508"),
])
def test_asia_place_exchange_wire(exchange, wire_exchange, prdt_type_cd):
    fake = _Fake(_place_resp())
    _place(fake, OrderStore(), exchange=exchange, symbol="7203")
    assert fake.calls[0]["body"]["OVRS_EXCG_CD"] == wire_exchange
    assert fake.calls[0]["body"]["PRDT_TYPE_CD"] == prdt_type_cd


def test_asia_place_missing_reservation_id_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {}})
    with pytest.raises(OrderError):
        _place(_Fake(resp), OrderStore())


def test_asia_place_timeout_no_retry():
    store = OrderStore()
    fake = _Fake(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _place(fake, store)
    assert len(fake.calls) == 1                      # 재전송 없음
    assert store.fingerprint_for("c1").exchange == "overseas-reserved-asia"  # in-flight 유지


def test_asia_place_rejected_clears_in_flight():
    store = OrderStore()
    rejected = RawResponse(rt_cd="1", msg_cd="APBK9999", msg1="예약 불가", body={})
    with pytest.raises(OrderRejectedError):
        _place(_Fake(rejected), store)
    assert store.fingerprint_for("c1") is None       # id 재사용 가능


def test_asia_place_replay_same_id_no_second_wire():
    store = OrderStore()
    fake = _Fake(_place_resp())
    r1 = _place(fake, store)
    r2 = _place(fake, store)
    assert r1.order_id == r2.order_id
    assert len(fake.calls) == 1


# --- 취소 (TTTS3013U, RVSE_CNCL_DVSN_CD=02, store 경유) ---------------------
def test_asia_cancel_resends_full_order_with_rvse_cncl_02():
    store = OrderStore()
    _place(_Fake(_place_resp()), store)
    cancel = _Fake(_place_resp())
    rep = ro.cancel_asia_reserved_order(cancel, store, "c1",
                                        cano="12345678", product_code="01", environment="real")
    call = cancel.calls[0]
    body = call["body"]
    assert call["path"] == _PLACE                    # 전용 취소 엔드포인트가 없다
    assert call["tr_id"] == "TTTS3013U"
    assert body["RVSE_CNCL_DVSN_CD"] == "02"
    assert body["OVRS_RSVN_ODNO"] == "0030138295"
    assert body["RSVN_ORD_RCIT_DT"] == "20260818"
    assert body["OVRS_EXCG_CD"] == "SEHK"
    assert body["PRDT_TYPE_CD"] == "501"
    assert body["PDNO"] == "00700"
    assert body["FT_ORD_QTY"] == "100"
    assert body["SLL_BUY_DVSN_CD"] == "02"
    assert rep.status is OrderStatus.PENDING_CANCEL
    assert rep.receipt_date == "20260818"


def test_asia_cancel_non_hk_rederives_exchange_prdt():
    # 비-홍콩 예약(상해 SHS)을 발주->취소 end-to-end. 지문에 "HKD"(byte-identity 기본)가 저장돼도
    # 취소 재도출은 거래소코드로 상품유형(551)을 되찾는다(통화 무시).
    store = OrderStore()
    _place(_Fake(_place_resp()), store, exchange="SHS", symbol="600000", client_order_id="cn1")
    cancel = _Fake(_place_resp())
    ro.cancel_asia_reserved_order(cancel, store, "cn1",
                                  cano="12345678", product_code="01", environment="real")
    body = cancel.calls[0]["body"]
    assert body["OVRS_EXCG_CD"] == "SHAA"
    assert body["PRDT_TYPE_CD"] == "551"       # 통화 아닌 거래소에서 재도출


def test_asia_prdt_type_cd_non_hk_ignores_persisted_hkd():
    # 지문이 실제로 싣고 오는 값 -- non-HK 거래소 + "HKD"(정규화 기본) -> 거래소 상품유형(통화 무시).
    assert ro._asia_prdt_type_cd("SHS", "HKD") == "551"


def test_hk_reserve_none_and_hkd_dedupe_identically():
    # 홍콩 미지정(None)과 명시 "HKD" 는 같은 상품유형(501) -> 지문 byte-identical -> 같은
    # client_order_id 재발주가 지문불일치 없이 dedup(두 번째는 와이어 안 나감).
    store = OrderStore()
    fake = _Fake(_place_resp())
    kis = _client(fake, store)
    r1 = kis.overseas.stock("00700", exchange="HKS").reserve_buy(
        quantity=100, limit_price=350.0, client_order_id="hk-dup")
    r2 = kis.overseas.stock("00700", exchange="HKS").reserve_buy(
        quantity=100, limit_price=350.0, currency="HKD", client_order_id="hk-dup")
    assert len(fake.calls) == 1                # 두 번째는 dedup
    assert r1.order_id == r2.order_id


def test_asia_cancel_paper_uses_v_tr():
    store = OrderStore()
    _place(_Fake(_place_resp()), store, environment="paper")
    cancel = _Fake(_place_resp())
    ro.cancel_asia_reserved_order(cancel, store, "c1",
                                  cano="12345678", product_code="01", environment="paper")
    assert cancel.calls[0]["tr_id"] == "VTTS3013U"


def test_asia_cancel_unknown_id_rejected_before_wire():
    fake = _Fake(_place_resp())
    with pytest.raises(KISUsageError):
        ro.cancel_asia_reserved_order(fake, OrderStore(), "nope",
                                      cano="12345678", product_code="01", environment="real")
    assert fake.calls == []


def test_asia_cancel_without_receipt_date_fails_closed():
    # 접수일자 없는 접수 응답 -> 리포트 receipt_date=None -> 취소는 재조회를 유도하며 fail-closed.
    store = OrderStore()
    no_receipt = RawResponse(rt_cd="0", msg_cd="A", msg1="",
                             body={"output": {"OVRS_RSVN_ODNO": "0030138295"}})
    _place(_Fake(no_receipt), store)
    fake = _Fake(_place_resp())
    with pytest.raises(KISError, match="reconcile"):
        ro.cancel_asia_reserved_order(fake, store, "c1",
                                      cano="12345678", product_code="01", environment="real")
    assert fake.calls == []


@pytest.mark.parametrize("body", [
    {},                                  # output 부재
    {"output": {}},                      # OVRS_RSVN_ODNO 부재
    {"output": {"OVRS_RSVN_ODNO": ""}},  # 빈 취소주문번호
])
def test_asia_cancel_missing_number_fails_closed(body):
    # rt_cd=0 이어도 취소주문번호(OVRS_RSVN_ODNO)가 아예 없으면 부분/오응답으로 보고 fail-closed --
    # PENDING_CANCEL 리포트 대신 KISError.
    store = OrderStore()
    _place(_Fake(_place_resp()), store)
    bad = _Fake(RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="정상", body=body))
    with pytest.raises(KISError, match="취소주문번호"):
        ro.cancel_asia_reserved_order(bad, store, "c1",
                                      cano="12345678", product_code="01", environment="real")


def test_asia_cancel_new_number_is_success():
    # 아시아 취소는 발주 TR 로 나가 취소주문이 원 예약번호와 다른 새 번호를 받는다(라이브: 원 684 -> 응답
    # 685). 원번호와 다르다고 오거부하지 않고, rt_cd=0 + 취소주문번호 존재면 PENDING_CANCEL 로 확정한다.
    store = OrderStore()
    place_odno = _place(_Fake(_place_resp()), store).order_id
    cancel_odno = str(int(place_odno) + 1)           # 취소주문은 다른 번호
    cancel = _Fake(RawResponse(rt_cd="0", msg_cd="40470000", msg1="모의투자 예약주문 완료",
                               body={"output": {"OVRS_RSVN_ODNO": cancel_odno}}))
    rep = ro.cancel_asia_reserved_order(cancel, store, "c1",
                                        cano="12345678", product_code="01", environment="real")
    assert rep.status is OrderStatus.PENDING_CANCEL
    assert rep.order_id == place_odno                # 리포트는 원 예약번호를 유지(취소주문 번호 아님)


@pytest.mark.parametrize(("currency", "prdt_type_cd"), [
    ("HKD", "501"), ("CNY", "543"), ("USD", "558"),
])
def test_asia_cancel_preserves_hong_kong_currency(currency, prdt_type_cd):
    # CNY/USD 로 발주한 홍콩 예약의 취소가 발주와 같은 PRDT_TYPE_CD 로 재현된다(통화가 지문에 영속).
    store = OrderStore()
    _place(_Fake(_place_resp()), store, currency=currency)
    cancel = _Fake(_place_resp())
    ro.cancel_asia_reserved_order(cancel, store, "c1",
                                  cano="12345678", product_code="01", environment="real")
    assert cancel.calls[0]["body"]["PRDT_TYPE_CD"] == prdt_type_cd


def test_asia_cancel_rejected_raises():
    store = OrderStore()
    _place(_Fake(_place_resp()), store)
    rejected = RawResponse(rt_cd="1", msg_cd="APBK9999", msg1="취소 불가", body={})
    with pytest.raises(OrderRejectedError):
        ro.cancel_asia_reserved_order(_Fake(rejected), store, "c1",
                                      cano="12345678", product_code="01", environment="real")


def test_asia_cancel_timeout_no_retry():
    store = OrderStore()
    _place(_Fake(_place_resp()), store)
    fake = _Fake(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        ro.cancel_asia_reserved_order(fake, store, "c1",
                                      cano="12345678", product_code="01", environment="real")
    assert len(fake.calls) == 1                      # 재전송 없음


def test_asia_cancel_routed_via_orders_cancel():
    # kis.orders.cancel(client_order_id) 이 지문 네임스페이스(overseas-reserved-asia)로 아시아 취소
    # 엔진에 라우팅된다(안전코어 경유 -- 스펙의 공개 취소 경로).
    store = OrderStore()
    _place(_Fake(_place_resp()), store)
    cancel = _Fake(_place_resp())
    rep = _client(cancel, store).orders.cancel("c1")
    assert cancel.calls[0]["tr_id"] == "TTTS3013U"
    assert cancel.calls[0]["body"]["RVSE_CNCL_DVSN_CD"] == "02"
    assert rep.status is OrderStatus.PENDING_CANCEL


def test_asia_modify_rejected_via_orders_modify():
    store = OrderStore()
    _place(_Fake(_place_resp()), store)
    fake = _Fake(_place_resp())
    with pytest.raises(KISUsageError, match="정정"):
        _client(fake, store).orders.modify("c1", limit_price=360.0)
    assert fake.calls == []


def test_asia_partial_cancel_rejected_before_wire():
    store = OrderStore()
    _place(_Fake(_place_resp()), store)
    fake = _Fake(_place_resp())
    with pytest.raises(KISUsageError, match="전량"):
        _client(fake, store).orders.cancel("c1", quantity=50)
    assert fake.calls == []


# --- 조회/재조회 (TTTS3014R, 실전전용) --------------------------------------
def test_asia_list_sends_ttts3014r():
    fake = _Fake(_list_resp([]))
    ro.fetch_reserved_orders(fake, cano="12345678", product_code="01", environment="real",
                             start="20260801", end="20260818", market="ASIA")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTS3014R"
    assert call["path"] == _LIST
    assert call["params"]["PRDT_TYPE_CD"] == ""      # 공백 = 아시아 전체
    assert call["params"]["OVRS_EXCG_CD"] == ""


def test_asia_list_unknown_market_rejected_before_wire():
    fake = _Fake(_list_resp([]))
    with pytest.raises(KISUsageError):
        ro.fetch_reserved_orders(fake, cano="12345678", product_code="01", environment="real",
                                 start="20260801", end="20260818", market="EU")
    assert fake.calls == []


def test_asia_reconcile_paper_fails_closed():
    store = OrderStore()
    _place(_Fake(_place_resp()), store)
    fake = _Fake(_place_resp())
    with pytest.raises(KISUsageError, match="실전"):
        ro.reconcile_asia_reserved_order(fake, store, "c1",
                                         cano="12345678", product_code="01", environment="paper")
    assert fake.calls == []                          # 조회 와이어에 닿지 않는다


def test_asia_reconcile_confirms_single_match_with_receipt_date(monkeypatch):
    monkeypatch.setattr("kis_trader.overseas._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore()
    place_t = _Fake(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _place(place_t, store)
    recon_t = _Fake(_list_resp([_list_row()]))
    rep = _client(recon_t, store).orders.reconcile("c1")   # 지문으로 아시아 경로 라우팅
    assert rep is not None
    assert recon_t.calls[0]["tr_id"] == "TTTS3014R"
    assert rep.order_id == "0030138295"
    assert rep.receipt_date == "20240603"            # 재조회 확정도 접수일자를 실어 취소 가능
    assert rep.status is OrderStatus.PENDING_NEW


def test_asia_reconcile_zero_match_returns_none_keeps_in_flight(monkeypatch):
    monkeypatch.setattr("kis_trader.overseas._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore()
    with pytest.raises(OrderTimeoutError):
        _place(_Fake(raises=TransportTimeout("t")), store)
    recon_t = _Fake(_list_resp([_list_row(ft_ord_qty="9")]))   # 수량 불일치 -> 매칭 0
    assert _client(recon_t, store).orders.reconcile("c1") is None
    assert store.fingerprint_for("c1") is not None


def test_asia_reconcile_multi_match_raises(monkeypatch):
    monkeypatch.setattr("kis_trader.overseas._engine.reserved_orders.datetime", _FrozenDatetime)
    store = OrderStore()
    with pytest.raises(OrderTimeoutError):
        _place(_Fake(raises=TransportTimeout("t")), store)
    rows = [_list_row(), _list_row(ovrs_rsvn_odno="0030138299")]
    recon_t = _Fake(_list_resp(rows))
    with pytest.raises(KISError):                    # 2건 이상 -> 모호, 자동 확정 금지
        _client(recon_t, store).orders.reconcile("c1")
    assert store.fingerprint_for("c1") is not None


# --- 공개 표면 (핸들/네임스페이스 end-to-end) --------------------------------
def test_handle_routes_hk_symbol_to_asia_reserve():
    # 홍콩 종목 핸들의 reserve_buy 가 아시아 엔진(TTTS3013U)으로 라우팅되고, 리포트에 예약번호와
    # 접수일자가 실린다. exchange 명시로 마스터 없이 결정적이다.
    fake = _Fake(_place_resp())
    store = OrderStore()
    kis = _client(fake, store)
    rep = kis.overseas.stock("00700", exchange="HKS").reserve_buy(quantity=100, limit_price=350.0)
    call = fake.calls[0]
    assert call["path"] == _PLACE
    assert call["tr_id"] == "TTTS3013U"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "00"
    assert call["body"]["OVRS_EXCG_CD"] == "SEHK"
    assert call["body"]["PRDT_TYPE_CD"] == "501"      # currency 기본 HKD
    assert rep.order_id == "0030138295"
    assert rep.receipt_date == "20260818"
    assert rep.status is OrderStatus.PENDING_NEW
    assert store.fingerprint_for(rep.client_order_id).exchange == "overseas-reserved-asia"


def test_handle_reserve_forwards_hong_kong_currency():
    fake = _Fake(_place_resp())
    kis = _client(fake, OrderStore())
    kis.overseas.stock("00700", exchange="HKS").reserve_sell(
        quantity=100, limit_price=350.0, currency="CNY")
    assert fake.calls[0]["body"]["PRDT_TYPE_CD"] == "543"   # 홍콩 CNY
    assert fake.calls[0]["body"]["SLL_BUY_DVSN_CD"] == "01"  # 매도


@pytest.mark.parametrize(("exchange", "currency", "wire_exchange", "prdt_type_cd"), [
    ("HKS", "HKD", "SEHK", "501"),
    ("HKS", "CNY", "SEHK", "543"),
    ("HKS", "USD", "SEHK", "558"),
    ("SHS", None, "SHAA", "551"),   # 홍콩 외 아시아는 통화 선택이 없다 -> currency=None
    ("SZS", None, "SZAA", "552"),
    ("TSE", None, "TKSE", "515"),
    ("HNX", None, "HASE", "507"),
    ("HSX", None, "VNSE", "508"),
])
def test_public_asia_place_wire_matrix(exchange, currency, wire_exchange, prdt_type_cd):
    # 공개 표면(reserve_buy)의 거래소x통화 -> 와이어(OVRS_EXCG_CD/PRDT_TYPE_CD) 매트릭스 고정 --
    # 원장 코드표(515 일본/551 상해A/552 심천A/507 하노이/508 호치민, 홍콩 501/543/558) 전수.
    fake = _Fake(_place_resp())
    kis = _client(fake, OrderStore())
    kis.overseas.stock("00700", exchange=exchange).reserve_buy(
        quantity=100, limit_price=350.0, currency=currency)
    assert fake.calls[0]["body"]["OVRS_EXCG_CD"] == wire_exchange
    assert fake.calls[0]["body"]["PRDT_TYPE_CD"] == prdt_type_cd


def test_public_asia_place_non_hk_currency_rejected_before_wire():
    # 통화 지정은 홍콩(HKS) 전용 -- 다른 아시아 거래소에 비-HKD 를 주면 와이어 전에 fail-closed.
    fake = _Fake(_place_resp())
    kis = _client(fake, OrderStore())
    with pytest.raises(KISUsageError, match="홍콩"):
        kis.overseas.stock("600000", exchange="SHS").reserve_buy(
            quantity=100, limit_price=10.0, currency="CNY")
    assert fake.calls == []


def test_us_reserve_rejects_currency_before_wire():
    # 미국은 통화 선택이 없다 -- currency 를 주면(HKD/USD 무엇이든) 와이어 전에 fail-closed.
    fake = _Fake(_place_resp())
    kis = _client(fake, OrderStore())
    with pytest.raises(KISUsageError, match="홍콩"):
        kis.overseas.stock("AAPL", exchange="NAS").reserve_buy(
            quantity=1, limit_price=148.0, currency="USD")
    assert fake.calls == []


def test_handle_us_symbol_still_routes_us_reserve():
    # 미국 종목은 기존 미국 예약 경로(TTTT3014U) 그대로다 -- currency 는 홍콩 전용이라 안 실린다.
    us_resp = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="정상",
                          body={"output": {"ODNO": "0030135009"}})
    fake = _Fake(us_resp)
    kis = _client(fake, OrderStore())
    rep = kis.overseas.stock("AAPL", exchange="NAS").reserve_buy(quantity=1, limit_price=148.0)
    assert fake.calls[0]["tr_id"] == "TTTT3014U"
    assert "RVSE_CNCL_DVSN_CD" not in fake.calls[0]["body"]
    assert rep.order_id == "0030135009"
    assert rep.receipt_date is None


def test_handle_report_cancels_via_orders_cancel():
    # 핸들 발주 리포트의 client_order_id 로 kis.orders.cancel 하면 아시아 취소 와이어
    # (TTTS3013U, RVSE_CNCL_DVSN_CD=02)가 나간다 -- 공개 표면의 취소 계약.
    fake = _Fake(_place_resp())
    store = OrderStore()
    kis = _client(fake, store)
    rep = kis.overseas.stock("00700", exchange="HKS").reserve_buy(quantity=100, limit_price=350.0)
    cancelled = kis.orders.cancel(rep.client_order_id)
    cancel_call = fake.calls[1]
    assert cancel_call["tr_id"] == "TTTS3013U"
    assert cancel_call["body"]["RVSE_CNCL_DVSN_CD"] == "02"
    assert cancel_call["body"]["OVRS_RSVN_ODNO"] == "0030138295"
    assert cancel_call["body"]["RSVN_ORD_RCIT_DT"] == "20260818"
    assert cancelled.status is OrderStatus.PENDING_CANCEL


def test_account_reserved_orders_unions_us_and_asia():
    # kis.account.overseas.reserved_orders 는 미국(TTTT3039R)과 아시아(TTTS3014R)를 모두 조회해 합친다.
    fake = _Fake(_list_resp([_list_row()]))
    kis = _client(fake, OrderStore())
    rows = kis.account.overseas.reserved_orders(start="20260801", end="20260818")
    assert [call["tr_id"] for call in fake.calls] == ["TTTT3039R", "TTTS3014R"]
    assert len(rows) == 2                            # 시장별 1건씩 합쳐진다


def test_account_reserved_orders_paper_fails_closed():
    fake = _Fake(_list_resp([]))
    kis = _client(fake, OrderStore(), environment="paper")
    with pytest.raises(KISUsageError, match="실전"):
        kis.account.overseas.reserved_orders(start="20260801", end="20260818")
    assert fake.calls == []                          # 조회 와이어에 닿지 않는다
