"""퇴직연금 잔고/체결기준잔고/주문내역 -- kis.pension.balance/present_balance/orders.

TTTC2208R / TTTC2202R / TTTC2210R. 보유종목(Position 재사용)+요약, 미체결 주문을 네트워크
없이 검증한다. 픽스처는 원장 응답예시 실값을 쓴다.
"""

from __future__ import annotations

import threading
from datetime import time
from decimal import Decimal

import pytest

from kis_trader import (
    KISClient,
    PensionBalance,
    PensionOrder,
    PensionPresentBalance,
    Position,
)
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-balance"
_PRESENT_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-present-balance"
_ORDERS_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-daily-ccld"

# --- 잔고(TTTC2208R) 원장 예시 ---
_BAL_ROW = {
    "cblc_dvsn_name": "사용자", "prdt_name": "ACE 미국S&P500", "pdno": "360200",
    "item_dvsn_name": "현금", "thdt_buyqty": "5", "thdt_sll_qty": "0", "hldg_qty": "5",
    "ord_psbl_qty": "5", "pchs_avg_pric": "13235.0000", "pchs_amt": "66175",
    "prpr": "13235", "evlu_amt": "66175", "evlu_pfls_amt": "0", "evlu_erng_rt": "0.00000000",
}
_BAL_SUMMARY = {
    "dnca_tot_amt": "100000", "nxdy_excc_amt": "100000", "prvs_rcdl_excc_amt": "33825",
    "thdt_buy_amt": "66175", "thdt_sll_amt": "0", "thdt_tlex_amt": "0",
    "scts_evlu_amt": "66175", "tot_evlu_amt": "100000",
}

# --- 체결기준잔고(TTTC2202R) 원장 예시 ---
_PRE_ROW = {
    "cblc_dvsn": "01", "cblc_dvsn_name": "사용자", "pdno": "069500", "prdt_name": "KODEX 200",
    "hldg_qty": "6", "slpsb_qty": "6", "pchs_avg_pric": "35670.0000", "evlu_pfls_amt": "-3330",
    "evlu_pfls_rt": "-1.56", "prpr": "35115", "evlu_amt": "210690", "pchs_amt": "214020",
    "cblc_weit": "53.06651890",
}
_PRE_SUMMARY = {
    "pchs_amt_smtl_amt": "464760", "evlu_amt_smtl_amt": "397030", "evlu_pfls_smtl_amt": "-67730",
    "trad_pfls_smtl": "0", "thdt_tot_pfls_amt": "-67730", "pftrt": "-14.57311300",
}

# --- 주문내역(TTTC2210R) -- 원장 예시는 빈 배열이라 필드는 표준 주문키로 구성 ---
_ORDER_ROW = {
    "ord_gno_brno": "06010", "sll_buy_dvsn_cd": "02", "trad_dvsn_name": "현금",
    "odno": "0001569139", "pdno": "360200", "prdt_name": "ACE 미국S&P500",
    "ord_unpr": "13235", "ord_qty": "5", "tot_ccld_qty": "2", "nccs_qty": "3",
    "ord_dvsn_cd": "00", "ord_dvsn_name": "지정가", "orgn_odno": "", "ord_tmd": "131438",
    "pchs_avg_pric": "0",
}


def _resp2(rows, summary, *, summary_list=False, nk="", fk="", tr_cont=""):
    out2 = [summary] if summary_list else summary
    body = {"output1": rows, "output2": out2, "ctx_area_nk100": nk, "ctx_area_fk100": fk}
    return RawResponse(rt_cd="0", msg_cd="KIOK0510", msg1="조회", body=body, tr_cont=tr_cont)


def _resp_orders(rows, *, nk="", fk="", tr_cont=""):
    body = {"output": rows, "ctx_area_nk100": nk, "ctx_area_fk100": fk}
    return RawResponse(rt_cd="0", msg_cd="KIOK0490", msg1="조회", body=body, tr_cont=tr_cont)


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


def _client(transport, *, profile="main", account="12345678-29"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     profile=profile, transport=transport)


# --- 잔고 ------------------------------------------------------------------
def test_pension_balance_parses_positions_and_summary():
    bal = _client(FakeTransport(response=_resp2([_BAL_ROW], _BAL_SUMMARY))).pension.balance()
    assert isinstance(bal, PensionBalance)
    assert bal.total_deposit == Decimal(100000)
    assert bal.total_evaluation == Decimal(100000)
    assert bal.today_buy_amount == Decimal(66175)
    assert len(bal.positions) == 1
    p = bal.positions[0]
    assert isinstance(p, Position)
    assert p.symbol == "360200"
    assert p.quantity == Decimal(5)
    assert p.sellable_quantity == Decimal(5)         # ord_psbl_qty
    assert p.average_purchase_price == Decimal("13235.0000")
    assert p.currency == "KRW"


def test_pension_balance_tr_and_params():
    fake = FakeTransport(response=_resp2([_BAL_ROW], _BAL_SUMMARY))
    _client(fake).pension.balance()
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC2208R"
    assert call["path"] == _BALANCE_PATH
    assert call["params"]["INQR_DVSN"] == "00"


def test_pension_balance_non_list_output_fails_closed():
    # 성공 응답인데 output1 이 배열이 아니면 빈 결과로 오인하지 않고 fail-closed.
    malformed = RawResponse(rt_cd="0", msg_cd="KIOK0510", msg1="조회",
                            body={"output1": {"bad": "object"}, "output2": _BAL_SUMMARY})
    with pytest.raises(KISError):
        _client(FakeTransport(response=malformed)).pension.balance()


def test_pension_balance_page_cap_fails_closed(monkeypatch):
    # 연속조회가 끝나지 않으면 상한에서 부분 결과로 자르지 않고 fail-closed(부분 잔고 오인 방지).
    from kis_trader.pension import _engine
    monkeypatch.setattr(_engine, "_MAX_PAGES", 2)
    endless = _resp2([_BAL_ROW], _BAL_SUMMARY, nk="MORE", tr_cont="M")  # 항상 다음 페이지 있음
    fake = FakeTransport(pages=[endless, endless, endless])
    with pytest.raises(KISError, match="페이지 상한"):
        _client(fake).pension.balance()


def test_pension_balance_demo_rejected_before_io():
    fake = FakeTransport(response=_resp2([_BAL_ROW], _BAL_SUMMARY))
    with pytest.raises(KISUsageError):
        _client(fake, profile="paper").pension.balance()
    assert fake.calls == []


def test_pension_balance_missing_summary_fails_closed():
    body = {"output1": [_BAL_ROW], "output2": None}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).pension.balance()


# --- 체결기준잔고 ----------------------------------------------------------
def test_pension_present_balance_parses():
    pre = _client(FakeTransport(response=_resp2([_PRE_ROW], _PRE_SUMMARY, summary_list=True))).pension.present_balance()
    assert isinstance(pre, PensionPresentBalance)
    assert pre.total_purchase_amount == Decimal(464760)
    assert pre.total_evaluation_pnl == Decimal(-67730)
    assert pre.return_percent == Decimal("-14.57311300")
    p = pre.positions[0]
    assert p.symbol == "069500"
    assert p.sellable_quantity == Decimal(6)         # slpsb_qty
    assert p.unrealized_pnl == Decimal(-3330)
    assert p.unrealized_pnl_percent == Decimal("-1.56")   # evlu_pfls_rt


def test_pension_present_balance_tr():
    fake = FakeTransport(response=_resp2([_PRE_ROW], _PRE_SUMMARY, summary_list=True))
    _client(fake).pension.present_balance()
    assert fake.calls[0]["tr_id"] == "TTTC2202R"
    assert fake.calls[0]["path"] == _PRESENT_PATH


# --- 주문내역 --------------------------------------------------------------
def test_pension_orders_parses():
    orders = _client(FakeTransport(response=_resp_orders([_ORDER_ROW]))).pension.orders()
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, PensionOrder)
    assert o.order_id == "0001569139"
    assert o.side == "buy"
    assert o.quantity == Decimal(5)
    assert o.filled_quantity == Decimal(2)
    assert o.unfilled_quantity == Decimal(3)
    assert o.order_price == Decimal(13235)
    assert o.order_time == time(13, 14, 38)


def test_pension_orders_only_unfilled_param():
    fake = FakeTransport(response=_resp_orders([]))
    _client(fake).pension.orders(only_unfilled=True)
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC2210R"
    assert call["path"] == _ORDERS_PATH
    assert call["params"]["CCLD_NCCS_DVSN"] == "02"


def test_pension_orders_default_all():
    fake = FakeTransport(response=_resp_orders([]))
    _client(fake).pension.orders()
    assert fake.calls[0]["params"]["CCLD_NCCS_DVSN"] == "%%"


def test_pension_orders_empty_ok():
    assert _client(FakeTransport(response=_resp_orders([]))).pension.orders() == []


def test_pension_orders_paginates():
    page1 = _resp_orders([_ORDER_ROW], nk="NEXT", tr_cont="M")
    page2 = _resp_orders([dict(_ORDER_ROW, odno="0001569140")], tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    orders = _client(fake).pension.orders()
    assert [o.order_id for o in orders] == ["0001569139", "0001569140"]
    assert fake.calls[1]["tr_cont"] == "N"


def test_pension_orders_demo_rejected_before_io():
    fake = FakeTransport(response=_resp_orders([]))
    with pytest.raises(KISUsageError):
        _client(fake, profile="paper").pension.orders()
    assert fake.calls == []


def test_pension_orders_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp_orders([])), account=None).pension.orders()
