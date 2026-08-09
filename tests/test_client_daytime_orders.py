"""미국주간거래 주문 -- kis.ticker(...).daytime_buy/daytime_sell + 정정취소 라우팅.

daytime-order TTTS6036U/6037U + daytime-order-rvsecncl TTTS6038U. 정규 해외주문과 같은 안전
코어(place/reconcile)를 공유하되 세션이 달라 지문·정정취소 엔드포인트가 갈린다. 네트워크 없이
가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading

import pytest

from kis_openapi import ExecutionReport, KISClient, Order, OrderStatus, OrderStore
from kis_openapi.errors import KISUsageError, OrderTimeoutError
from kis_openapi.transport import RawResponse, TransportTimeout

_DAYTIME_ORDER = "/uapi/overseas-stock/v1/trading/daytime-order"
_DAYTIME_CHANGE = "/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl"

_ACCEPTED = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0030000123", "ORD_TMD": "121052"}})


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
                               "body": body, "idempotent": idempotent})
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
def test_daytime_buy_wire():
    fake = FakeTransport(response=_ACCEPTED)
    report = _client(fake).ticker("AAPL", exchange="NAS").daytime_buy(quantity=1, price="150.25")
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0030000123"
    assert report.status is OrderStatus.NEW
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _DAYTIME_ORDER
    assert call["idempotent"] is False              # 무재시도
    assert call["tr_id"] == "TTTS6036U"             # 주간 매수
    assert call["body"]["OVRS_EXCG_CD"] == "NASD"   # NAS -> NASD
    assert call["body"]["PDNO"] == "AAPL"
    assert call["body"]["OVRS_ORD_UNPR"] == "150.25"
    assert call["body"]["ORD_DVSN"] == "00"         # 지정가
    assert call["body"]["ORD_SVR_DVSN_CD"] == "0"


def test_daytime_sell_uses_sell_tr():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake).ticker("AAPL", exchange="NYS").daytime_sell(quantity=2, price="150.00")
    assert fake.calls[0]["tr_id"] == "TTTS6037U"
    assert fake.calls[0]["body"]["OVRS_EXCG_CD"] == "NYSE"


# --- 검증/거부 -------------------------------------------------------------
def test_daytime_non_us_exchange_rejected():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):     # 홍콩(HKS)은 주간거래 불가
        _client(fake).ticker("00700", exchange="HKS").daytime_buy(quantity=1, price="1")
    assert fake.calls == []


def test_daytime_domestic_ticker_rejected():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).ticker("005930").daytime_buy(quantity=1, price="1")
    assert fake.calls == []


def test_daytime_demo_rejected_before_io():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").ticker("AAPL", exchange="NAS").daytime_buy(quantity=1, price="1")
    assert fake.calls == []


def test_daytime_order_rejects_market_price():
    # price 는 필수(지정가만) -- TypeError(키워드 필수) 로 생성 자체가 안 됨(와이어 접촉 전)
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(TypeError):
        _client(fake).ticker("AAPL", exchange="NAS").daytime_buy(quantity=1)
    assert fake.calls == []


def test_daytime_rejects_non_day_tif():
    # 주간 와이어엔 TIF 필드가 없어 조용히 day 로 나가면 지문/전송이 어긋난다 -> 생성 시점 거부
    with pytest.raises(KISUsageError):
        Order.limit("AAPL", side="buy", quantity=1, limit_price=150, exchange="NAS",
                    session="daytime", time_in_force="ioc")


# --- 정체성/안전 불변식 ----------------------------------------------------
def test_daytime_fingerprint_distinct_from_regular():
    regular = Order.limit("AAPL", side="buy", quantity=1, limit_price=150, exchange="NAS")
    daytime = Order.limit("AAPL", side="buy", quantity=1, limit_price=150, exchange="NAS",
                          session="daytime")
    assert regular.fingerprint != daytime.fingerprint
    assert regular.fingerprint.session == "regular"
    assert daytime.fingerprint.session == "daytime"


def test_daytime_timeout_no_retry():
    store = OrderStore()
    fake = FakeTransport(raises=TransportTimeout("t"))
    cid = "20240101-daytime0001"
    with pytest.raises(OrderTimeoutError):
        _client(fake, store=store).ticker("AAPL", exchange="NAS").daytime_buy(
            quantity=1, price="150", client_order_id=cid)
    assert len(fake.calls) == 1
    assert store.fingerprint_for(cid) is not None
    assert store.fingerprint_for(cid).session == "daytime"


def test_daytime_and_regular_same_id_conflicts():
    store = OrderStore()
    cid = "20240101-daytime0002"
    regular = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="",
        body={"output": {"KRX_FWDG_ORD_ORGNO": "01", "ODNO": "0001", "ORD_TMD": "1"}}))
    _client(regular, store=store).ticker("AAPL", exchange="NAS").buy(
        quantity=1, price="150", client_order_id=cid)
    daytime = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):     # 같은 id, 세션만 달라 -> 충돌
        _client(daytime, store=store).ticker("AAPL", exchange="NAS").daytime_buy(
            quantity=1, price="150", client_order_id=cid)
    assert daytime.calls == []


# --- 정정취소 라우팅(daytime 전용 엔드포인트) -------------------------------
def test_daytime_order_cancel_routes_to_daytime_endpoint():
    store = OrderStore()
    cid = "20240101-daytime0003"
    place_t = FakeTransport(response=_ACCEPTED)
    _client(place_t, store=store).ticker("AAPL", exchange="NAS").daytime_buy(
        quantity=1, price="150", client_order_id=cid)
    # 취소는 미국주간 전용 rvsecncl 로 라우팅돼야 한다
    change_t = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="", body={"output": {"ODNO": "0030000123"}}))
    _client(change_t, store=store).cancel_order(cid)
    call = change_t.calls[0]
    assert call["path"] == _DAYTIME_CHANGE
    assert call["tr_id"] == "TTTS6038U"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "02"   # 취소
    assert call["body"]["ORGN_ODNO"] == "0030000123"


def test_daytime_order_replace_routes_to_daytime_endpoint():
    store = OrderStore()
    cid = "20240101-daytime0004"
    place_t = FakeTransport(response=_ACCEPTED)
    _client(place_t, store=store).ticker("AAPL", exchange="NAS").daytime_buy(
        quantity=2, price="150", client_order_id=cid)
    change_t = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="", body={"output": {"ODNO": "0030000123"}}))
    _client(change_t, store=store).replace_order(cid, price="151")
    call = change_t.calls[0]
    assert call["path"] == _DAYTIME_CHANGE
    assert call["tr_id"] == "TTTS6038U"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "01"   # 정정
    assert call["body"]["OVRS_ORD_UNPR"] == "151"


def test_daytime_replay_same_id_returns_prior():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    cid = "20240101-daytime-rp01"
    t = _client(fake, store=store).ticker("AAPL", exchange="NAS")
    r1 = t.daytime_buy(quantity=1, price="150", client_order_id=cid)
    r2 = t.daytime_buy(quantity=1, price="150", client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert len(fake.calls) == 1                      # replay -- 재전송 없음


def test_daytime_reconcile_does_not_confirm_from_regular_ccnl():
    # 주간 체결은 정규 체결내역에 없으므로, 정규 ccnl 의 (동일 종목/수량/가격) 행으로 주간 주문을
    # 확정하면 안 된다 -> None(in-flight 유지), 수동 확인.
    store = OrderStore()
    cid = "20240101-daytime-rc01"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).ticker("AAPL", exchange="NAS").daytime_buy(
            quantity=1, price="150", client_order_id=cid)
    # reconcile: 정규 ccnl 에 지문과 같은 행이 있어도 주간 주문은 확정하지 않는다
    ccnl_row = {"pdno": "AAPL", "sll_buy_dvsn_cd": "02", "ft_ord_qty": "1", "ft_ord_unpr3": "150",
                "ft_ccld_qty": "1", "ft_ccld_unpr3": "150", "odno": "0009999999"}
    recon_body = {"output": [ccnl_row], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    recon_t = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="M", msg1="", body=recon_body))
    assert _client(recon_t, store=store).reconcile(cid) is None   # 자동 확정 안 함
    assert store.fingerprint_for(cid) is not None                 # in-flight 유지
    assert recon_t.calls == []                                    # ccnl 조회조차 하지 않는다


def test_daytime_session_persists_across_store_reopen(tmp_path):
    # 세션은 취소 라우팅을 좌우하는 안전 필드 -- store 를 닫고 다시 열어도 daytime 이 보존돼야 하고,
    # 재개 후 취소가 여전히 미국주간 전용 엔드포인트로 가야 한다.
    path = tmp_path / "orders.json"
    cid = "20240101-daytime-persist01"
    store1 = OrderStore(path=path)
    _client(FakeTransport(response=_ACCEPTED), store=store1).ticker("AAPL", exchange="NAS").daytime_buy(
        quantity=1, price="150", client_order_id=cid)
    store1.close()
    store2 = OrderStore(path=path)
    assert store2.fingerprint_for(cid).session == "daytime"       # v3 라운드트립 보존
    change_t = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="", body={"output": {"ODNO": "0030000123"}}))
    _client(change_t, store=store2).cancel_order(cid)
    assert change_t.calls[0]["path"] == _DAYTIME_CHANGE
    assert change_t.calls[0]["tr_id"] == "TTTS6038U"
    store2.close()


def test_daytime_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_ACCEPTED), account=None).ticker(
            "AAPL", exchange="NAS").daytime_buy(quantity=1, price="1")
