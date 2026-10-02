"""미체결(정정·취소 가능) 주문 조회 -- kis.account.domestic.open_orders() (TTTC0084R).

브로커 측 미체결 주문 목록을 네트워크 없이 FakeTransport 로 검증한다. 픽스처는 원장
응답예시(inquire-psbl-rvsecncl) 실값을 사용한다.
"""

from __future__ import annotations

import threading
from datetime import time
from decimal import Decimal

import pytest

from kis_trader import KISClient, OpenOrder
from kis_trader.domestic._engine import account as account_module
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"


class FakeTransport:
    def __init__(self, *, response=None, pages=None, raises=None):
        self.response = response
        self.pages = pages
        self.raises = raises
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent, "tr_cont": tr_cont})
        if self.raises is not None:
            raise self.raises
        outcome = self.pages.pop(0) if self.pages else self.response
        assert outcome is not None, "FakeTransport 에 응답을 줘야 한다"
        return outcome


# 원장 응답예시(inquire-psbl-rvsecncl) 실값 2건.
_ROW_AMEND = {
    "ord_gno_brno": "06010", "odno": "0001569139", "orgn_odno": "0001569136",
    "ord_dvsn_name": "지정가", "pdno": "009150", "prdt_name": "SamsungElecMech",
    "rvse_cncl_dvsn_name": "BUY AMEND*", "ord_qty": "1", "ord_unpr": "140000",
    "ord_tmd": "131438", "tot_ccld_qty": "0", "tot_ccld_amt": "0", "psbl_qty": "1",
    "sll_buy_dvsn_cd": "02", "ord_dvsn_cd": "00", "mgco_aptm_odno": "",
}
_ROW_PLAIN = {
    "ord_gno_brno": "06010", "odno": "0001569138", "orgn_odno": "",
    "ord_dvsn_name": "지정가", "pdno": "009150", "prdt_name": "SamsungElecMech",
    "rvse_cncl_dvsn_name": "", "ord_qty": "1", "ord_unpr": "200000",
    "ord_tmd": "131421", "tot_ccld_qty": "0", "tot_ccld_amt": "0", "psbl_qty": "1",
    "sll_buy_dvsn_cd": "02", "ord_dvsn_cd": "00", "mgco_aptm_odno": "",
}


def _resp(*, rows=None, ctx_nk="", ctx_fk="", tr_cont=""):
    body = {"output": rows if rows is not None else [],
            "ctx_area_nk100": ctx_nk, "ctx_area_fk100": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def test_open_orders_parses_ledger_rows():
    orders = _client(FakeTransport(response=_resp(rows=[_ROW_AMEND, _ROW_PLAIN]))).account.domestic.open_orders()
    assert len(orders) == 2
    first = orders[0]
    assert isinstance(first, OpenOrder)
    assert first.symbol == "009150"
    assert first.name == "SamsungElecMech"
    assert first.order_id == "0001569139"
    assert first.original_order_id == "0001569136"   # 정정 주문이라 원주문번호 있음
    assert first.branch_number == "06010"
    assert first.side == "buy"                        # 02 -> buy
    assert first.order_type == "지정가"
    assert first.order_quantity == Decimal(1)
    assert first.filled_quantity == Decimal(0)
    assert first.unfilled_quantity == Decimal(1)
    assert first.cancelable_quantity == Decimal(1)
    assert first.order_price == Decimal(140000)
    assert first.order_time == time(13, 14, 38)
    # 정정 주문이 아닌 건은 원주문번호가 빈 문자열
    assert orders[1].original_order_id == ""
    assert orders[1].order_price == Decimal(200000)


def test_open_orders_tr_method_and_params():
    fake = FakeTransport(response=_resp(rows=[_ROW_PLAIN]))
    _client(fake).account.domestic.open_orders()
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC0084R"
    assert call["method"] == "GET"
    assert call["idempotent"] is True
    assert call["path"] == _PATH
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "01"
    assert call["params"]["INQR_DVSN_1"] == "0"
    assert call["params"]["INQR_DVSN_2"] == "0"


def test_open_orders_side_sell_maps():
    row = dict(_ROW_PLAIN, sll_buy_dvsn_cd="01")
    orders = _client(FakeTransport(response=_resp(rows=[row]))).account.domestic.open_orders()
    assert orders[0].side == "sell"


def test_open_orders_demo_rejected_before_io():
    fake = FakeTransport(response=_resp(rows=[_ROW_PLAIN]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.domestic.open_orders()
    assert fake.calls == []   # 와이어 접촉 전 거부


def test_open_orders_empty_is_ok():
    orders = _client(FakeTransport(response=_resp(rows=[]))).account.domestic.open_orders()
    assert orders == []


def test_open_orders_paginates_and_merges():
    page1 = _resp(rows=[_ROW_AMEND], ctx_nk="NEXT", ctx_fk="FK", tr_cont="M")
    page2 = _resp(rows=[_ROW_PLAIN], tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    orders = _client(fake).account.domestic.open_orders()
    assert len(orders) == 2
    assert fake.calls[1]["tr_cont"] == "N"
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK100"] == "FK"


def test_open_orders_non_list_output_fails_closed():
    body = {"output": {"odno": "x"}, "ctx_area_nk100": "", "ctx_area_fk100": ""}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.domestic.open_orders()


def test_open_orders_skips_padding_row():
    padding = dict(_ROW_PLAIN, odno="")
    orders = _client(FakeTransport(response=_resp(rows=[padding, _ROW_PLAIN]))).account.domestic.open_orders()
    assert len(orders) == 1


def test_open_orders_blank_time_is_none():
    row = dict(_ROW_PLAIN, ord_tmd="")
    orders = _client(FakeTransport(response=_resp(rows=[row]))).account.domestic.open_orders()
    assert orders[0].order_time is None


def test_open_orders_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="ERR", msg1="실패",
                       body={"output": []}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.domestic.open_orders()


def test_open_orders_pagination_cap_fails_closed(monkeypatch):
    monkeypatch.setattr(account_module, "_MAX_OPEN_ORDER_PAGES", 2)
    # 진짜로 다음 페이지가 계속 있는 상황 = 매 페이지 연속키가 진전한다(같은 키 반복은 이제 종료로 본다).
    fake = FakeTransport(pages=[_resp(rows=[_ROW_PLAIN], ctx_nk=f"N{i}", tr_cont="M") for i in range(3)])
    with pytest.raises(KISError):
        _client(fake).account.domestic.open_orders()


def test_open_orders_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).account.domestic.open_orders()
