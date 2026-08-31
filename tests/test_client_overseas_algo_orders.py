"""미국주식 알고리즘(TWAP/VWAP) 분할주문 발주 -- 즉시(buy/sell) + 예약(reserve_buy/sell).

원장(공지 2025-05-23): 즉시주문(TTTT1002U/1006U)은 ORD_DVSN 35/36 + START_TIME/END_TIME +
ALGO_ORD_TMD_DVSN_CD(00 직접입력 / 02 정규장 종료), 예약주문(TTTT3014U/3016U)은 ORD_DVSN 35/36 +
ALGO_ORD_TMD_DVSN_CD=02(정규장 종료 고정, 시간창 없음). 미국(NAS/NYS/AMS) 실전 전용.

네트워크 없이 가짜 전송으로 와이어·fail-closed·dedup 불변식을 검증한다. 비-algo 경로는 회귀 0
(ORD_DVSN=00, algo 필드 없음)을 함께 고정한다.
"""

from __future__ import annotations

import threading

import pytest

from kis_trader import ExecutionReport, KISClient, OrderStore
from kis_trader.errors import KISUsageError
from kis_trader.order import (
    ImmediateOrderFingerprint,
    Order,
    ReservedOrderFingerprint,
    encode_fingerprint,
    validate_hhmmss,
)
from kis_trader.transport import RawResponse

_ORDER = "/uapi/overseas-stock/v1/trading/order"
_RESV = "/uapi/overseas-stock/v1/trading/order-resv"

_IMMEDIATE_ACK = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                             body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000123456",
                                              "ORD_TMD": "093015"}})
_RESV_ACK = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="예약 접수",
                        body={"output": {"ODNO": "0031111234"}})


class FakeTransport:
    def __init__(self, *, response=None):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body,
                               "params": params, "idempotent": idempotent})
        return self.response


def _client(transport, *, environment="real", account="12345678-01", store=None):
    return KISClient(app_key="k", app_secret="s", account=account, environment=environment,
                     transport=transport, store=store)


def _stock(transport, *, symbol="AAPL", exchange="NAS", **kw):
    return _client(transport, **kw).overseas.stock(symbol, exchange=exchange)


# --- 즉시 algo 와이어 --------------------------------------------------------
def test_immediate_twap_with_window_wire():
    fake = FakeTransport(response=_IMMEDIATE_ACK)
    report = _stock(fake).buy(quantity=10, limit_price="150.25", algo="twap",
                              algo_window=("093000", "160000"))
    assert isinstance(report, ExecutionReport)
    body = fake.calls[0]["body"]
    assert fake.calls[0]["tr_id"] == "TTTT1002U"        # 미국 매수
    assert body["ORD_DVSN"] == "35"                     # TWAP
    assert body["ALGO_ORD_TMD_DVSN_CD"] == "00"         # 직접입력
    assert body["START_TIME"] == "093000"
    assert body["END_TIME"] == "160000"
    assert body["OVRS_ORD_UNPR"] == "150.25"


def test_immediate_vwap_no_window_uses_close_mode():
    fake = FakeTransport(response=_IMMEDIATE_ACK)
    _stock(fake).sell(quantity=5, limit_price="150", algo="vwap")
    body = fake.calls[0]["body"]
    assert fake.calls[0]["tr_id"] == "TTTT1006U"        # 미국 매도
    assert body["ORD_DVSN"] == "36"                     # VWAP
    assert body["ALGO_ORD_TMD_DVSN_CD"] == "02"         # 정규장 종료
    assert "START_TIME" not in body and "END_TIME" not in body


def test_immediate_non_algo_wire_is_byte_identical():
    # 회귀 0: algo 안 주면 ORD_DVSN=00, algo 필드 전부 없음(종전과 동일).
    fake = FakeTransport(response=_IMMEDIATE_ACK)
    _stock(fake).buy(quantity=1, limit_price="150")
    body = fake.calls[0]["body"]
    assert body["ORD_DVSN"] == "00"
    assert not any(k in body for k in ("ALGO_ORD_TMD_DVSN_CD", "START_TIME", "END_TIME"))


# --- 즉시 algo fail-closed ---------------------------------------------------
def test_immediate_algo_non_us_exchange_rejected_before_wire():
    fake = FakeTransport(response=_IMMEDIATE_ACK)
    with pytest.raises(KISUsageError, match="미국"):
        _stock(fake, exchange="HKS").buy(quantity=1, limit_price="150", algo="twap")
    assert fake.calls == []


def test_immediate_algo_paper_rejected_before_wire():
    # algo 는 실전 전용 -- 모의는 claim/와이어 전에 조기 거부(client_order_id 미소비).
    store = OrderStore()
    fake = FakeTransport(response=_IMMEDIATE_ACK)
    cid = "20240101-algo-pp01"
    with pytest.raises(KISUsageError, match="모의"):
        _stock(fake, environment="paper", store=store).buy(
            quantity=1, limit_price="150", algo="twap", client_order_id=cid)
    assert fake.calls == []
    assert store.fingerprint_for(cid) is None           # id 미소비


def test_immediate_algo_requires_limit_price():
    fake = FakeTransport(response=_IMMEDIATE_ACK)
    with pytest.raises(KISUsageError, match="지정가"):
        _stock(fake).buy(quantity=1, algo="twap")
    assert fake.calls == []


@pytest.mark.parametrize("window", [("160000", "093000"), ("160000", "160000")])
def test_immediate_algo_window_start_must_precede_end(window):
    fake = FakeTransport(response=_IMMEDIATE_ACK)
    with pytest.raises(KISUsageError):
        _stock(fake).buy(quantity=1, limit_price="150", algo="twap", algo_window=window)
    assert fake.calls == []


@pytest.mark.parametrize("window", [("9300", "160000"), ("093000", "256000"), ("abcdef", "160000")])
def test_immediate_algo_window_must_be_valid_hhmmss(window):
    fake = FakeTransport(response=_IMMEDIATE_ACK)
    with pytest.raises(KISUsageError):
        _stock(fake).buy(quantity=1, limit_price="150", algo="twap", algo_window=window)
    assert fake.calls == []


def test_immediate_window_without_algo_rejected():
    fake = FakeTransport(response=_IMMEDIATE_ACK)
    with pytest.raises(KISUsageError, match="algo"):
        _stock(fake).buy(quantity=1, limit_price="150", algo_window=("093000", "160000"))
    assert fake.calls == []


# --- 즉시 algo dedup ---------------------------------------------------------
def test_immediate_algo_vs_non_algo_same_id_conflicts():
    store = OrderStore()
    cid = "20240101-algo-cf01"
    t = _stock(FakeTransport(response=_IMMEDIATE_ACK), store=store)
    t.buy(quantity=1, limit_price="150", algo="twap", client_order_id=cid)
    with pytest.raises(KISUsageError):                  # 일반주문은 다른 주문 -> 충돌
        _stock(FakeTransport(response=_IMMEDIATE_ACK), store=store).buy(
            quantity=1, limit_price="150", client_order_id=cid)


def test_immediate_twap_vs_vwap_same_id_conflicts():
    store = OrderStore()
    cid = "20240101-algo-cf02"
    _stock(FakeTransport(response=_IMMEDIATE_ACK), store=store).buy(
        quantity=1, limit_price="150", algo="twap", client_order_id=cid)
    with pytest.raises(KISUsageError):
        _stock(FakeTransport(response=_IMMEDIATE_ACK), store=store).buy(
            quantity=1, limit_price="150", algo="vwap", client_order_id=cid)


def test_immediate_different_window_same_id_conflicts():
    store = OrderStore()
    cid = "20240101-algo-cf03"
    _stock(FakeTransport(response=_IMMEDIATE_ACK), store=store).buy(
        quantity=1, limit_price="150", algo="twap", algo_window=("093000", "160000"),
        client_order_id=cid)
    with pytest.raises(KISUsageError):
        _stock(FakeTransport(response=_IMMEDIATE_ACK), store=store).buy(
            quantity=1, limit_price="150", algo="twap", algo_window=("100000", "160000"),
            client_order_id=cid)


def test_immediate_algo_replay_same_order_is_idempotent():
    store = OrderStore()
    cid = "20240101-algo-rp01"
    t = _stock(FakeTransport(response=_IMMEDIATE_ACK), store=store)
    r1 = t.buy(quantity=1, limit_price="150", algo="twap", algo_window=("093000", "160000"),
               client_order_id=cid)
    fake2 = FakeTransport(response=_IMMEDIATE_ACK)
    r2 = _stock(fake2, store=store).buy(quantity=1, limit_price="150", algo="twap",
                                        algo_window=("093000", "160000"), client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert fake2.calls == []                            # 재전송 안 함


# --- 예약 algo 와이어 --------------------------------------------------------
@pytest.mark.parametrize("strategy,ord_dvsn", [("twap", "35"), ("vwap", "36")])
def test_reserved_algo_wire_close_mode_only(strategy, ord_dvsn):
    # 예약 algo 는 정규장 종료 집행 고정(시간창 없음). twap/vwap 각각 ORD_DVSN 35/36 매핑을 고정한다.
    fake = FakeTransport(response=_RESV_ACK)
    report = _stock(fake).reserve_buy(quantity=1, limit_price="150", algo=strategy)
    assert report.order_id == "0031111234"
    body = fake.calls[0]["body"]
    assert fake.calls[0]["path"] == _RESV
    assert fake.calls[0]["tr_id"] == "TTTT3014U"
    assert body["ORD_DVSN"] == ord_dvsn
    assert body["ALGO_ORD_TMD_DVSN_CD"] == "02"         # 정규장 종료 고정
    assert "START_TIME" not in body and "END_TIME" not in body


def test_reserved_non_algo_wire_is_byte_identical():
    fake = FakeTransport(response=_RESV_ACK)
    _stock(fake).reserve_buy(quantity=1, limit_price="150")
    body = fake.calls[0]["body"]
    assert body["ORD_DVSN"] == "00"
    assert "ALGO_ORD_TMD_DVSN_CD" not in body


def test_reserved_algo_asia_rejected_before_wire():
    fake = FakeTransport(response=_RESV_ACK)
    with pytest.raises(KISUsageError, match="미국"):
        _stock(fake, exchange="HKS").reserve_buy(quantity=1, limit_price="150", algo="vwap")
    assert fake.calls == []


def test_reserved_algo_paper_rejected_before_wire():
    fake = FakeTransport(response=_RESV_ACK)
    with pytest.raises(KISUsageError, match="모의"):
        _stock(fake, environment="paper").reserve_buy(quantity=1, limit_price="150", algo="twap")
    assert fake.calls == []


def test_reserved_algo_vs_non_algo_same_id_conflicts():
    store = OrderStore()
    cid = "20240101-ralgo-cf01"
    _stock(FakeTransport(response=_RESV_ACK), store=store).reserve_buy(
        quantity=1, limit_price="150", algo="twap", client_order_id=cid)
    with pytest.raises(KISUsageError):
        _stock(FakeTransport(response=_RESV_ACK), store=store).reserve_buy(
            quantity=1, limit_price="150", client_order_id=cid)


# --- Order 레벨 검증(DATA 경계) ---------------------------------------------
def test_order_algo_fingerprint_carries_strategy_and_window():
    order = Order.limit("AAPL", side="buy", quantity=1, limit_price=150, exchange="NAS",
                        algo_strategy="twap", algo_start="093000", algo_end="160000")
    fp = order.fingerprint
    assert isinstance(fp, ImmediateOrderFingerprint)
    assert (fp.algo_strategy, fp.algo_start, fp.algo_end) == ("twap", "093000", "160000")


def test_order_algo_rejects_non_us_exchange():
    with pytest.raises(KISUsageError, match="미국"):
        Order.limit("AAPL", side="buy", quantity=1, limit_price=150, exchange="HKS",
                    algo_strategy="twap")


def test_order_algo_rejects_overnight_session():
    with pytest.raises(KISUsageError, match="정규"):
        Order.limit("AAPL", side="buy", quantity=1, limit_price=150, exchange="NAS",
                    session="overnight", algo_strategy="vwap")


def test_reserved_fingerprint_encoding_has_strategy_and_empty_window_slots():
    # 예약 지문은 시간창이 없어 전략만 슬롯 16 에 실리고 시작/종료(17/18)는 항상 "" -- 공개 코덱으로 검증.
    fp = ReservedOrderFingerprint(
        symbol="AAPL", side="buy", order_type="limit", quantity="1", limit_price="150",
        end_date="", exchange="overseas-reserved", algo_strategy="twap")
    row = encode_fingerprint(fp)
    assert row[16] == "twap" and row[17] == "" and row[18] == ""


@pytest.mark.parametrize("value", ["235959", "000000"])
def test_validate_hhmmss_accepts_boundary_times(value):
    validate_hhmmss(value, "algo_start")   # 유효 시각은 통과(예외 없음)


@pytest.mark.parametrize("value", ["240000", "236000", "005960", "0930", "0930000", "٠٩٣٠٠٠"])
def test_validate_hhmmss_rejects_out_of_range_or_malformed(value):
    # 24시/60분/60초, 5·7자리, 비-ASCII(아랍-인도 숫자)는 전부 거부.
    with pytest.raises(KISUsageError):
        validate_hhmmss(value, "algo_start")
