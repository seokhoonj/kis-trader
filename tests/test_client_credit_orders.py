"""신용주문 실행 -- kis.ticker(...).credit_buy/credit_sell (order-credit TTTC0052U/TTTC0051U).

현금주문과 같은 안전 코어(place/reconcile)를 공유하되 와이어 조립기만 신용용이다. 이중체결
방지·재시도 금지·dedup 지문(신용/현금·대출별 구분)·보수적 재조회를 네트워크 없이 가짜 전송으로
검증한다. 픽스처 응답은 원장 예시(ODNO 포함).
"""

from __future__ import annotations

import datetime as _dt
import threading

import pytest

from kis_openapi import ExecutionReport, KISClient, Order, OrderStatus, OrderStore
from kis_openapi.errors import KISUsageError, OrderRejectedError, OrderTimeoutError
from kis_openapi.transport import RawResponse, TransportTimeout

_ORDER_CREDIT = "/uapi/domestic-stock/v1/trading/order-credit"
_DAILY_CCLD = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"

_ACCEPTED = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"KRX_FWDG_ORD_ORGNO": "06010", "ODNO": "0001569138", "ORD_TMD": "131421"}},
)
_REJECTED = RawResponse(rt_cd="1", msg_cd="APBK1234", msg1="신용주문 불가", body={})


class _FrozenDatetime(_dt.datetime):
    """now() 만 고정, strptime 등은 실제 datetime 을 상속 -- 대출일자 기본값(오늘) 결정성 확보."""

    @classmethod
    def now(cls, tz=None):
        return _dt.datetime(2024, 6, 3, 10, 0, tzinfo=tz)


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


def _credit_daily_row(*, odno="0001569138", symbol="009150", side_code="02", order_division="00",
                      order_quantity="1", order_unit_price="130000", filled_quantity="1",
                      average_price="130000", loan_dt="20211103", rejected_quantity="0", canceled="N"):
    return {"odno": odno, "pdno": symbol, "sll_buy_dvsn_cd": side_code, "ord_dvsn_cd": order_division,
            "ord_qty": order_quantity, "ord_unpr": order_unit_price, "tot_ccld_qty": filled_quantity,
            "avg_prvs": average_price, "loan_dt": loan_dt, "rjct_qty": rejected_quantity,
            "cncl_yn": canceled}


# --- 정상 전송 -------------------------------------------------------------
def test_credit_buy_limit_wire():
    fake = FakeTransport(response=_ACCEPTED)
    report = _client(fake).ticker("009150").credit_buy(quantity=1, price=130000,
                                                       credit_type="26", loan_date="20211103")
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0001569138"
    assert report.status is OrderStatus.NEW
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _ORDER_CREDIT
    assert call["idempotent"] is False              # 주문은 타임아웃 재시도 금지
    assert call["tr_id"] == "TTTC0052U"             # 매수
    assert call["body"]["CRDT_TYPE"] == "26"
    assert call["body"]["LOAN_DT"] == "20211103"
    assert call["body"]["ORD_DVSN"] == "00"         # 지정가
    assert call["body"]["ORD_UNPR"] == "130000"
    assert call["body"]["SLL_TYPE"] == ""
    assert call["body"]["RSVN_ORD_YN"] == "N"
    assert call["body"]["EXCG_ID_DVSN_CD"] == "KRX"


def test_credit_buy_new_type_defaults_loan_date_to_today(monkeypatch):
    monkeypatch.setattr("kis_openapi.order.datetime", _FrozenDatetime)
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake).ticker("009150").credit_buy(quantity=1, price=130000, credit_type="21")  # 신규
    assert fake.calls[0]["body"]["LOAN_DT"] == "20240603"   # 고정된 오늘(KST)


def test_credit_sell_new_type_defaults_loan_date(monkeypatch):
    # 대주신규(22)는 sell 이지만 신규라 loan_date 생략 가능 -> 오늘로 채움(side 아니라 operation 기준)
    monkeypatch.setattr("kis_openapi.order.datetime", _FrozenDatetime)
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake).ticker("009150").credit_sell(quantity=1, price=130000, credit_type="22")
    assert fake.calls[0]["tr_id"] == "TTTC0051U"
    assert fake.calls[0]["body"]["LOAN_DT"] == "20240603"
    assert fake.calls[0]["body"]["CRDT_TYPE"] == "22"


def test_credit_sell_repay_uses_given_loan_date():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake).ticker("009150").credit_sell(quantity=1, price=130000,
                                               credit_type="25", loan_date="20211103")  # 융자상환
    assert fake.calls[0]["body"]["LOAN_DT"] == "20211103"


def test_credit_buy_market_division():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake).ticker("009150").credit_buy(quantity=1, credit_type="21", loan_date="20211103")
    assert fake.calls[0]["body"]["ORD_DVSN"] == "01"    # 시장가
    assert fake.calls[0]["body"]["ORD_UNPR"] == "0"


# --- 검증/거부 (와이어 접촉 전) --------------------------------------------
def test_credit_type_must_match_side():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):     # 21 은 매수 유형 -- 매도에 쓰면 거부
        _client(fake).ticker("009150").credit_sell(quantity=1, price=1, credit_type="21",
                                                   loan_date="20211103")
    assert fake.calls == []


def test_credit_repay_requires_loan_date():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):     # 26 은 상환(매수측) -- loan_date 필수
        _client(fake).ticker("009150").credit_buy(quantity=1, price=1, credit_type="26")
    with pytest.raises(KISUsageError):     # 25 는 상환(매도측) -- loan_date 필수
        _client(fake).ticker("009150").credit_sell(quantity=1, price=1, credit_type="25")
    assert fake.calls == []


def test_credit_bad_calendar_loan_date_rejected():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):     # 형식은 8자리지만 불가능한 날짜
        _client(fake).ticker("009150").credit_sell(quantity=1, price=1, credit_type="25",
                                                   loan_date="20261399")
    with pytest.raises(KISUsageError):     # 구분자 있는 형식
        _client(fake).ticker("009150").credit_sell(quantity=1, price=1, credit_type="25",
                                                   loan_date="2021-11-03")
    assert fake.calls == []


def test_credit_demo_rejected_before_io():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").ticker("009150").credit_buy(quantity=1, price=1,
                                                                      credit_type="21",
                                                                      loan_date="20211103")
    assert fake.calls == []


def test_credit_overseas_ticker_rejected():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(fake).ticker("AAPL", exchange="NAS").credit_buy(quantity=1, price=1,
                                                               credit_type="21",
                                                               loan_date="20211103")
    assert fake.calls == []


def test_order_credit_with_overseas_exchange_rejected_at_construction():
    # 안전: raw Order(credit_type=..., exchange=해외)는 생성 시점에 fail-closed(해외 빌더로 새지 않음).
    # (Order.credit 은 exchange 인자가 없어 항상 XKRX 이므로, 이 우회는 raw Order 직접 생성으로만 가능.)
    with pytest.raises(KISUsageError):
        Order(symbol="AAPL", side="buy", order_type="limit", quantity=1, limit_price=1,
              exchange="NAS", credit_type="21", loan_date="20211103")


def test_loan_date_without_credit_type_rejected():
    with pytest.raises(KISUsageError):
        Order(symbol="005930", side="buy", order_type="market", quantity=1, loan_date="20211103")


# --- 접수 거부: in-flight 해제 후 재사용 가능 -------------------------------
def test_credit_rejected_clears_in_flight_and_id_reusable():
    store = OrderStore()
    cid = "20240101-creditreject01"
    t = _client(FakeTransport(response=_REJECTED), store=store).ticker("009150")
    with pytest.raises(OrderRejectedError):
        t.credit_buy(quantity=1, price=1, credit_type="21", loan_date="20211103", client_order_id=cid)
    assert store.fingerprint_for(cid) is None       # 거부 -> in-flight 해제
    # 같은 id 재사용이 가능해야 한다(영구 차단 아님)
    fake2 = FakeTransport(response=_ACCEPTED)
    report = _client(fake2, store=store).ticker("009150").credit_buy(
        quantity=1, price=1, credit_type="21", loan_date="20211103", client_order_id=cid)
    assert report.order_id == "0001569138"
    assert len(fake2.calls) == 1


# --- 안전 불변식: 타임아웃 재시도 금지 + 지문 구분 -------------------------
def test_credit_timeout_no_retry():
    store = OrderStore()
    fake = FakeTransport(raises=TransportTimeout("t"))
    cid = "20240101-creditbuy0001"
    with pytest.raises(OrderTimeoutError):
        _client(fake, store=store).ticker("009150").credit_buy(
            quantity=1, price=1, credit_type="21", loan_date="20211103", client_order_id=cid)
    assert len(fake.calls) == 1                      # 재전송 없음
    assert store.fingerprint_for(cid) is not None    # in-flight 유지


def test_credit_fingerprint_distinct_from_cash():
    cash = Order.limit("009150", side="buy", quantity=1, limit_price=130000)
    credit = Order.credit("009150", side="buy", quantity=1, price=130000,
                          credit_type="26", loan_date="20211103")
    assert cash.fingerprint != credit.fingerprint
    assert cash.fingerprint.credit_type == "" and cash.fingerprint.loan_date == ""
    assert credit.fingerprint.credit_type == "26" and credit.fingerprint.loan_date == "20211103"


def test_credit_fingerprint_distinct_by_loan_date():
    # 같은 종목/수량/가격/신용유형이라도 상환 대상 대출이 다르면 다른 주문(다른 지문)
    a = Order.credit("009150", side="sell", quantity=1, price=130000, credit_type="25",
                     loan_date="20211103")
    b = Order.credit("009150", side="sell", quantity=1, price=130000, credit_type="25",
                     loan_date="20220204")
    assert a.fingerprint != b.fingerprint


def test_credit_replay_same_id_returns_prior():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    cid = "20240101-creditbuy0002"
    t = _client(fake, store=store).ticker("009150")
    r1 = t.credit_buy(quantity=1, price=130000, credit_type="26", loan_date="20211103", client_order_id=cid)
    r2 = t.credit_buy(quantity=1, price=130000, credit_type="26", loan_date="20211103", client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert len(fake.calls) == 1                      # 두 번째는 replay(재전송 없음)


def test_credit_same_id_different_credit_conflicts_no_wire():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED)
    cid = "20240101-creditbuy0003"
    t = _client(fake, store=store).ticker("009150")
    t.credit_buy(quantity=1, price=130000, credit_type="21", loan_date="20211103", client_order_id=cid)
    with pytest.raises(KISUsageError):               # 다른 신용유형 -> 지문 불일치 -> 충돌
        t.credit_buy(quantity=1, price=130000, credit_type="23", loan_date="20211103", client_order_id=cid)
    assert len(fake.calls) == 1                      # 충돌은 와이어에 닿지 않는다


# --- 신용주문 reconcile (공유 코어, loan_dt 로 현금과 구분) -----------------
def test_credit_reconcile_confirms_credit_row():
    store = OrderStore()
    cid = "20240101-creditrecon01"
    # 전송은 타임아웃(체결 불명) -> in-flight
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).ticker("009150").credit_buy(
            quantity=1, price=130000, credit_type="26", loan_date="20211103", client_order_id=cid)
    # 재조회: 일별체결조회에 부분체결된 신용 행(loan_dt 일치) 하나
    row = _credit_daily_row(filled_quantity="1", loan_dt="20211103")
    recon_t = FakeTransport(by_path={_DAILY_CCLD: RawResponse(
        rt_cd="0", msg_cd="APBK0013", msg1="조회", body={"output1": [row]})})
    report = _client(recon_t, store=store).reconcile(cid)
    assert report is not None
    assert report.order_id == "0001569138"
    assert report.status is OrderStatus.FILLED
    # reconcile 은 읽기(GET)만 -- 재전송 없음
    assert all(c["method"] == "GET" for c in recon_t.calls)


def test_credit_reconcile_ignores_cash_lookalike_row():
    # 같은 종목/수량/가격의 현금 체결(loan_dt 없음) 행은 신용주문을 확정하면 안 된다(phantom 방지)
    store = OrderStore()
    cid = "20240101-creditrecon02"
    place_t = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).ticker("009150").credit_buy(
            quantity=1, price=130000, credit_type="26", loan_date="20211103", client_order_id=cid)
    cash_row = _credit_daily_row(loan_dt="")         # 대출일자 없음 = 현금 체결
    recon_t = FakeTransport(by_path={_DAILY_CCLD: RawResponse(
        rt_cd="0", msg_cd="APBK0013", msg1="조회", body={"output1": [cash_row]})})
    report = _client(recon_t, store=store).reconcile(cid)
    assert report is None                            # 현금 행으로 신용주문을 확정하지 않는다
