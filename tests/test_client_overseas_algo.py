"""해외 알고(지정가/TWAP/VWAP) 주문 -- kis.account.overseas.algo_orders / overseas_algo_executions.

algo-ordno TTTS6058R (주문번호 목록) + inquire-algo-ccnl TTTS6059R (체결내역). 네트워크 없이
FakeTransport 로 검증한다. 픽스처는 원장 응답예시/레이아웃 필드 기반.
"""

from __future__ import annotations

import threading
from datetime import time
from decimal import Decimal

import pytest

from kis_trader import KISClient, OverseasAlgoExecution, OverseasAlgoOrder
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_ORDNO = "/uapi/overseas-stock/v1/trading/algo-ordno"
_CCNL = "/uapi/overseas-stock/v1/trading/inquire-algo-ccnl"

# algo 패밀리(TTTS6058R/6059R)는 응답 키가 대문자다(형제 _CCNL_ROW 와 같은 규약).
_ORD_ROW = {
    "ODNO": "0030000123", "TRAD_DVSN_NAME": "TWAP지정가매수", "PDNO": "AAPL", "ITEM_NAME": "애플",
    "FT_ORD_QTY": "10", "FT_ORD_UNPR3": "150.25", "FT_CCLD_QTY": "3",
    "SPLT_BUY_ATTR_NAME": "정규장 종료", "ORD_GNO_BRNO": "06010",
}
_CCNL_ROW = {
    "CCLD_SEQ": "1", "CCLD_BTWN": "153012", "PDNO": "AAPL", "ITEM_NAME": "애플",
    "FT_CCLD_QTY": "3", "FT_CCLD_UNPR3": "150.30", "FT_CCLD_AMT3": "450.90",
}


def _resp(rows, *, nk="", fk="", tr_cont=""):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output": rows, "ctx_area_nk200": nk, "ctx_area_fk200": fk},
                       tr_cont=tr_cont)


class FakeTransport:
    def __init__(self, *, response=None):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent})
        assert self.response is not None
        return self.response


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


# --- 주문번호 목록 (TTTS6058R) ---------------------------------------------
def test_algo_orders_parses():
    orders = _client(FakeTransport(response=_resp([_ORD_ROW]))).account.overseas.algo_orders()
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, OverseasAlgoOrder)
    assert o.order_id == "0030000123"
    assert o.symbol == "AAPL"
    assert o.quantity == Decimal(10)
    assert o.order_price == Decimal("150.25")
    assert o.filled_quantity == Decimal(3)
    assert o.split_attribute == "정규장 종료"
    assert o.branch_number == "06010"


def test_algo_orders_tr_and_params():
    fake = FakeTransport(response=_resp([_ORD_ROW]))
    _client(fake).account.overseas.algo_orders()
    call = fake.calls[0]
    assert call["tr_id"] == "TTTS6058R"
    assert call["method"] == "GET"
    assert call["path"] == _ORDNO
    assert call["idempotent"] is True
    # 필수 거래일자(TRAD_DT) -- 생략 시 오늘(YYYYMMDD). 안 보내면 조회가 비거나 거부된다.
    assert call["params"]["TRAD_DT"] and len(call["params"]["TRAD_DT"]) == 8


def test_algo_orders_trade_date_forwarded():
    fake = FakeTransport(response=_resp([_ORD_ROW]))
    _client(fake).account.overseas.algo_orders(trade_date="20240605")
    assert fake.calls[0]["params"]["TRAD_DT"] == "20240605"


def test_algo_orders_bad_trade_date_rejected():
    # trade_date 는 YYYYMMDD 형식만 -- 형식 오류는 와이어 전에 fail-closed(형제 date 파라미터와 일관).
    fake = FakeTransport(response=_resp([_ORD_ROW]))
    with pytest.raises(KISUsageError):
        _client(fake).account.overseas.algo_orders(trade_date="2024-06-05")
    assert fake.calls == []


def test_algo_orders_demo_rejected():
    fake = FakeTransport(response=_resp([_ORD_ROW]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.overseas.algo_orders()
    assert fake.calls == []


def test_algo_orders_empty_ok():
    assert _client(FakeTransport(response=_resp([]))).account.overseas.algo_orders() == []


def test_algo_orders_non_list_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": {"ODNO": "x"}}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.overseas.algo_orders()


# --- 체결내역 (TTTS6059R) --------------------------------------------------
def test_algo_executions_parses():
    execs = _client(FakeTransport(response=_resp([_CCNL_ROW]))).account.overseas.algo_executions(
        "0030000123", order_date="20250523", branch_number="06010")
    assert len(execs) == 1
    e = execs[0]
    assert isinstance(e, OverseasAlgoExecution)
    assert e.sequence == "1"
    assert e.executed_at == time(15, 30, 12)
    assert e.symbol == "AAPL"
    assert e.quantity == Decimal(3)
    assert e.price == Decimal("150.30")
    assert e.executed_amount == Decimal("450.90")


def test_algo_executions_tr_and_params():
    fake = FakeTransport(response=_resp([_CCNL_ROW]))
    _client(fake).account.overseas.algo_executions("0030000123", order_date="20250523", branch_number="06010")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTS6059R"
    assert call["path"] == _CCNL
    assert call["params"]["ODNO"] == "0030000123"
    assert call["params"]["ORD_DT"] == "20250523"
    assert call["params"]["ORD_GNO_BRNO"] == "06010"


def test_algo_executions_demo_rejected():
    fake = FakeTransport(response=_resp([_CCNL_ROW]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.overseas.algo_executions("1", order_date="20250523")
    assert fake.calls == []


def test_algo_executions_empty_ok():
    assert _client(FakeTransport(response=_resp([]))).account.overseas.algo_executions(
        "1", order_date="20250523") == []


def test_algo_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp([])), account=None).account.overseas.algo_orders()
