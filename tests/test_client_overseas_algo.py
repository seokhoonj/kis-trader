"""해외 알고(지정가/TWAP/VWAP) 주문 -- kis.overseas.account.algo_orders / overseas_algo_executions.

algo-ordno TTTS6058R (주문번호 목록) + inquire-algo-ccnl TTTS6059R (체결내역). 네트워크 없이
FakeTransport 로 검증한다. 픽스처는 원장 응답예시/레이아웃 필드 기반.
"""

from __future__ import annotations

import threading
from datetime import time
from decimal import Decimal

import pytest

from kis_openapi import KISClient, OverseasAlgoExecution, OverseasAlgoOrder
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_ORDNO = "/uapi/overseas-stock/v1/trading/algo-ordno"
_CCNL = "/uapi/overseas-stock/v1/trading/inquire-algo-ccnl"

_ORD_ROW = {
    "odno": "0030000123", "trad_dvsn_name": "TWAP지정가매수", "pdno": "AAPL", "item_name": "애플",
    "ft_ord_qty": "10", "ft_ord_unpr3": "150.25", "ft_ccld_qty": "3",
    "splt_buy_attr_name": "정규장 종료", "ord_gno_brno": "06010",
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
    orders = _client(FakeTransport(response=_resp([_ORD_ROW]))).overseas.account.algo_orders()
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
    _client(fake).overseas.account.algo_orders()
    call = fake.calls[0]
    assert call["tr_id"] == "TTTS6058R"
    assert call["method"] == "GET"
    assert call["path"] == _ORDNO
    assert call["idempotent"] is True


def test_algo_orders_demo_rejected():
    fake = FakeTransport(response=_resp([_ORD_ROW]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").overseas.account.algo_orders()
    assert fake.calls == []


def test_algo_orders_empty_ok():
    assert _client(FakeTransport(response=_resp([]))).overseas.account.algo_orders() == []


def test_algo_orders_non_list_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": {"odno": "x"}}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).overseas.account.algo_orders()


# --- 체결내역 (TTTS6059R) --------------------------------------------------
def test_algo_executions_parses():
    execs = _client(FakeTransport(response=_resp([_CCNL_ROW]))).overseas.account.algo_executions(
        "0030000123", order_date="20250523", branch_number="06010")
    assert len(execs) == 1
    e = execs[0]
    assert isinstance(e, OverseasAlgoExecution)
    assert e.sequence == "1"
    assert e.executed_at == time(15, 30, 12)
    assert e.symbol == "AAPL"
    assert e.quantity == Decimal(3)
    assert e.price == Decimal("150.30")
    assert e.amount == Decimal("450.90")


def test_algo_executions_tr_and_params():
    fake = FakeTransport(response=_resp([_CCNL_ROW]))
    _client(fake).overseas.account.algo_executions("0030000123", order_date="20250523", branch_number="06010")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTS6059R"
    assert call["path"] == _CCNL
    assert call["params"]["ODNO"] == "0030000123"
    assert call["params"]["ORD_DT"] == "20250523"
    assert call["params"]["ORD_GNO_BRNO"] == "06010"


def test_algo_executions_demo_rejected():
    fake = FakeTransport(response=_resp([_CCNL_ROW]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").overseas.account.algo_executions("1", order_date="20250523")
    assert fake.calls == []


def test_algo_executions_empty_ok():
    assert _client(FakeTransport(response=_resp([]))).overseas.account.algo_executions(
        "1", order_date="20250523") == []


def test_algo_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp([])), account=None).overseas.account.algo_orders()
