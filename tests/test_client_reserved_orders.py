"""예약주문 조회 -- kis.reserved_orders(start=, end=) (CTSC0004R).

예약주문 목록(다음 영업일 동시호가 예약)을 네트워크 없이 검증한다. 픽스처는 원장 응답예시
(order-resv-ccnl) 실값을 쓴다.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_openapi import KISClient, ReservedOrder
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_PATH = "/uapi/domestic-stock/v1/trading/order-resv-ccnl"

# 원장 응답예시(order-resv-ccnl) 실값.
_ROW = {
    "rsvn_ord_seq": "42401", "rsvn_ord_ord_dt": "20220523", "rsvn_ord_rcit_dt": "20220520",
    "pdno": "005940", "ord_dvsn_cd": "01", "ord_rsvn_qty": "1", "tot_ccld_qty": "0",
    "cncl_ord_dt": "", "ord_tmd": "", "ctac_tlno": "0", "rjct_rson2": "", "odno": "",
    "rsvn_ord_rcit_tmd": "165318", "kor_item_shtn_name": "NH투자증권", "sll_buy_dvsn_cd": "02",
    "ord_rsvn_unpr": "6000", "tot_ccld_amt": "0", "loan_dt": "", "cncl_rcit_tmd": "",
    "prcs_rslt": "미처리", "ord_dvsn_name": "현금매수", "tmnl_mdia_kind_cd": "31",
    "rsvn_end_dt": "20220523",
}


def _resp(rows=None, *, nk="", fk="", tr_cont=""):
    body = {"output": rows if rows is not None else [_ROW],
            "ctx_area_nk200": nk, "ctx_area_fk200": fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


class FakeTransport:
    def __init__(self, *, response=None, pages=None):
        self.response = response
        self.pages = pages
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent, "tr_cont": tr_cont})
        outcome = self.pages.pop(0) if self.pages else self.response
        assert outcome is not None
        return outcome


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def test_reserved_orders_parses_ledger_row():
    orders = _client(FakeTransport(response=_resp())).reserved_orders(start="20220501", end="20220523")
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, ReservedOrder)
    assert o.sequence == "42401"
    assert o.order_date == date(2022, 5, 23)
    assert o.received_date == date(2022, 5, 20)
    assert o.symbol == "005940"
    assert o.name == "NH투자증권"
    assert o.side == "buy"                          # 02 -> buy
    assert o.order_type_name == "현금매수"
    assert o.reserved_quantity == Decimal(1)
    assert o.filled_quantity == Decimal(0)
    assert o.reserved_price == Decimal(6000)
    assert o.status == "미처리"
    assert o.executed_order_id == ""                # 미집행
    assert o.reservation_end_date == date(2022, 5, 23)


def test_reserved_orders_tr_method_and_params():
    fake = FakeTransport(response=_resp())
    _client(fake).reserved_orders(start="20220501", end="20220523", process="unprocessed")
    call = fake.calls[0]
    assert call["tr_id"] == "CTSC0004R"
    assert call["method"] == "GET"
    assert call["path"] == _PATH
    assert call["idempotent"] is True
    assert call["params"]["RSVN_ORD_ORD_DT"] == "20220501"
    assert call["params"]["RSVN_ORD_END_DT"] == "20220523"
    assert call["params"]["PRCS_DVSN_CD"] == "2"    # unprocessed
    assert call["params"]["CNCL_YN"] == "Y"
    assert call["params"]["TMNL_MDIA_KIND_CD"] == "00"


def test_reserved_orders_default_process_all():
    fake = FakeTransport(response=_resp())
    _client(fake).reserved_orders(start="1", end="2")
    assert fake.calls[0]["params"]["PRCS_DVSN_CD"] == "0"


def test_reserved_orders_unknown_process_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake).reserved_orders(start="1", end="2", process="weird")
    assert fake.calls == []


def test_reserved_orders_demo_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").reserved_orders(start="1", end="2")
    assert fake.calls == []


def test_reserved_orders_paginates_and_merges():
    page1 = _resp([_ROW], nk="NEXT", fk="FK", tr_cont="M")
    page2 = _resp([dict(_ROW, rsvn_ord_seq="42405")], tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    orders = _client(fake).reserved_orders(start="1", end="2")
    assert [o.sequence for o in orders] == ["42401", "42405"]
    assert fake.calls[1]["tr_cont"] == "N"
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"


def test_reserved_orders_empty_is_ok():
    assert _client(FakeTransport(response=_resp([]))).reserved_orders(start="1", end="2") == []


def test_reserved_orders_skips_padding_row():
    orders = _client(FakeTransport(response=_resp([dict(_ROW, rsvn_ord_seq=""), _ROW]))).reserved_orders(
        start="1", end="2"
    )
    assert len(orders) == 1


def test_reserved_orders_non_list_output_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": {"rsvn_ord_seq": "1"}}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).reserved_orders(start="1", end="2")


def test_reserved_orders_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="E", msg1="실패", body={"output": []}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).reserved_orders(start="1", end="2")


def test_reserved_orders_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).reserved_orders(start="1", end="2")
