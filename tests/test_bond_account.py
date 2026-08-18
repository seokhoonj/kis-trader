"""장내채권 계좌 조회 -- kis.account.domestic.bonds (실전전용).

잔고/매수가능/미체결/체결을 네트워크 없이 FakeTransport 로 검증한다. 모든 조회는 모의투자
미지원이라 environment="paper" 면 와이어를 타기 전에 KISUsageError 로 fail-closed 한다.
"""

from __future__ import annotations

import threading
from datetime import date, time
from decimal import Decimal

import pytest

from kis_trader import KISClient
from kis_trader.domestic.bond_account import DomesticBondAccount
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_BALANCE_PATH = "/uapi/domestic-bond/v1/trading/inquire-balance"
_BUYABLE_PATH = "/uapi/domestic-bond/v1/trading/inquire-psbl-order"
_OPEN_ORDERS_PATH = "/uapi/domestic-bond/v1/trading/inquire-psbl-rvsecncl"
_FILLS_PATH = "/uapi/domestic-bond/v1/trading/inquire-daily-ccld"


class FakeTransport:
    def __init__(self, *, response=None, by_path=None, raises=None):
        self.response = response
        self.by_path = by_path or {}
        self.raises = raises
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent, "tr_cont": tr_cont})
        if path in self.by_path:
            outcome = self.by_path[path]
            if isinstance(outcome, list):
                outcome = outcome.pop(0)
        elif self.raises is not None:
            outcome = self.raises
        else:
            outcome = self.response
        if isinstance(outcome, BaseException):
            raise outcome
        assert outcome is not None, "FakeTransport 에 응답을 줘야 한다"
        return outcome


_ERROR = RawResponse(rt_cd="1", msg_cd="EGW00215", msg1="초당 거래건수 초과", body={})


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def _bonds(transport, *, environment="real") -> DomesticBondAccount:
    return _client(transport, environment=environment).account.domestic.bonds


# --- balance (CTSC8407R) ---------------------------------------------------
def _position(pdno="KR2033022D33", *, name="국민주택1종20-05", buy_dt="20240215", buy_sqno="1",
              cblc="1000", agrx="1000", sprx="0", exdt="20340215", buy_erng="3.45",
              buy_unpr="9850", buy_amt="9850000", ord_psbl="1000"):
    return {"pdno": pdno, "prdt_name": name, "buy_dt": buy_dt, "buy_sqno": buy_sqno,
            "cblc_qty": cblc, "agrx_qty": agrx, "sprx_qty": sprx, "exdt": exdt,
            "buy_erng_rt": buy_erng, "buy_unpr": buy_unpr, "buy_amt": buy_amt,
            "ord_psbl_qty": ord_psbl}


def _balance_resp(*, rows=None, ctx_nk="", ctx_fk="", tr_cont=""):
    body = {"output": rows if rows is not None else [],
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_balance_parses_and_routes():
    fake = FakeTransport(response=_balance_resp(rows=[_position()]))
    positions = _bonds(fake).balance()
    assert len(positions) == 1
    pos = positions[0]
    assert pos.symbol == "KR2033022D33"
    assert pos.name == "국민주택1종20-05"
    assert pos.buy_date == date(2024, 2, 15)
    assert pos.buy_sequence == "1"
    assert pos.quantity == Decimal(1000)
    assert pos.comprehensive_tax_quantity == Decimal(1000)
    assert pos.separate_tax_quantity == Decimal(0)
    assert pos.maturity_date == date(2034, 2, 15)
    assert pos.buy_yield == Decimal("3.45")
    assert pos.buy_price == Decimal(9850)
    assert pos.buy_amount == Decimal(9850000)
    assert pos.orderable_quantity == Decimal(1000)
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == _BALANCE_PATH
    assert call["tr_id"] == "CTSC8407R"
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "01"
    assert call["params"]["INQR_CNDT"] == "00"


def test_balance_skips_blank_pdno():
    fake = FakeTransport(response=_balance_resp(rows=[_position(), {"pdno": "  "}]))
    assert len(_bonds(fake).balance()) == 1


def test_balance_paper_fails_closed():
    fake = FakeTransport(response=_balance_resp(rows=[_position()]))
    with pytest.raises(KISUsageError):
        _bonds(fake, environment="paper").balance()
    assert fake.calls == []


def test_balance_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={})
    with pytest.raises(KISError):
        _bonds(FakeTransport(response=resp)).balance()


def test_balance_non_list_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": {"pdno": "x"}})
    with pytest.raises(KISError):
        _bonds(FakeTransport(response=resp)).balance()


def test_balance_error_response_raises():
    with pytest.raises(KISError):
        _bonds(FakeTransport(response=_ERROR)).balance()


def test_balance_paginates_two_pages():
    page1 = _balance_resp(rows=[_position(buy_sqno="1")], ctx_nk="NK", ctx_fk="FK", tr_cont="F")
    page2 = _balance_resp(rows=[_position(buy_sqno="2")], tr_cont="D")
    fake = FakeTransport(by_path={_BALANCE_PATH: [page1, page2]})
    positions = _bonds(fake).balance()
    assert [p.buy_sequence for p in positions] == ["1", "2"]
    assert len(fake.calls) == 2
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NK"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"


# --- buyable (TTTC8910R) ---------------------------------------------------
_BUYABLE_OUTPUT = {
    "ord_psbl_cash": "50000000", "ord_psbl_sbst": "0", "ruse_psbl_amt": "0",
    "buy_psbl_amt": "49500000", "buy_psbl_qty": "5000", "cma_evlu_amt": "0",
}


def _buyable_resp(output=None):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output": output if output is not None else dict(_BUYABLE_OUTPUT)})


def test_buyable_parses_and_routes():
    fake = FakeTransport(response=_buyable_resp())
    result = _bonds(fake).buyable("KR2033022D33", price=9850)
    assert result.symbol == "KR2033022D33"
    assert result.orderable_cash == Decimal(50000000)
    assert result.orderable_substitute == Decimal(0)
    assert result.reusable_amount == Decimal(0)
    assert result.buyable_amount == Decimal(49500000)
    assert result.buyable_quantity == Decimal(5000)
    assert result.cma_value == Decimal(0)
    call = fake.calls[0]
    assert call["path"] == _BUYABLE_PATH
    assert call["tr_id"] == "TTTC8910R"
    assert call["params"]["PDNO"] == "KR2033022D33"
    assert call["params"]["BOND_ORD_UNPR"] == "9850"
    assert call["params"]["SAMT_MKET_PTCI_YN"] == "N"


def test_buyable_market_price_blank_when_none():
    fake = FakeTransport(response=_buyable_resp())
    _bonds(fake).buyable("KR2033022D33")
    assert fake.calls[0]["params"]["BOND_ORD_UNPR"] == ""


def test_buyable_summary_as_length_one_list():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output": [dict(_BUYABLE_OUTPUT)]})
    assert _bonds(FakeTransport(response=resp)).buyable("KR2033022D33").orderable_cash == Decimal(50000000)


def test_buyable_paper_fails_closed():
    fake = FakeTransport(response=_buyable_resp())
    with pytest.raises(KISUsageError):
        _bonds(fake, environment="paper").buyable("KR2033022D33", price=9850)
    assert fake.calls == []


def test_buyable_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={})
    with pytest.raises(KISError):
        _bonds(FakeTransport(response=resp)).buyable("KR2033022D33")


def test_buyable_rejects_non_positive_price():
    fake = FakeTransport(response=_buyable_resp())
    with pytest.raises(KISUsageError):
        _bonds(fake).buyable("KR2033022D33", price=0)
    assert fake.calls == []


# --- open_orders (CTSC8035R) -----------------------------------------------
def _open_order(odno="0000000123", *, pdno="KR2033022D33", name="국민주택1종", rvse="정정",
                ord_qty="1000", unpr="9850", ord_tmd="131438", ccld_qty="0", ccld_amt="0",
                psbl="1000", orgn="0000000100", sll_buy="02", ord_dvsn="00"):
    return {"odno": odno, "pdno": pdno, "prdt_abrv_name": name, "rvse_cncl_dvsn_name": rvse,
            "ord_qty": ord_qty, "bond_ord_unpr": unpr, "ord_tmd": ord_tmd,
            "tot_ccld_qty": ccld_qty, "tot_ccld_amt": ccld_amt, "ord_psbl_qty": psbl,
            "orgn_odno": orgn, "sll_buy_dvsn_cd": sll_buy, "ord_dvsn_cd": ord_dvsn}


def _open_orders_resp(*, rows=None, ctx_nk="", ctx_fk="", tr_cont=""):
    body = {"output": rows if rows is not None else [],
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_open_orders_parses_and_routes():
    fake = FakeTransport(response=_open_orders_resp(rows=[_open_order()]))
    orders = _bonds(fake).open_orders("20240215")
    assert len(orders) == 1
    order = orders[0]
    assert order.order_id == "0000000123"
    assert order.symbol == "KR2033022D33"
    assert order.name == "국민주택1종"
    assert order.revise_cancel_type == "정정"
    assert order.order_quantity == Decimal(1000)
    assert order.order_price == Decimal(9850)
    assert order.order_time == time(13, 14, 38)
    assert order.filled_quantity == Decimal(0)
    assert order.filled_amount == Decimal(0)
    assert order.cancelable_quantity == Decimal(1000)
    assert order.original_order_id == "0000000100"
    assert order.side == "buy"
    assert order.order_division == "00"
    call = fake.calls[0]
    assert call["path"] == _OPEN_ORDERS_PATH
    assert call["tr_id"] == "CTSC8035R"
    assert call["params"]["ORD_DT"] == "20240215"
    assert call["params"]["ODNO"] == ""


def test_open_orders_skips_blank_odno():
    fake = FakeTransport(response=_open_orders_resp(rows=[_open_order(), {"odno": "  "}]))
    assert len(_bonds(fake).open_orders("20240215")) == 1


def test_open_orders_rejects_bad_date():
    fake = FakeTransport(response=_open_orders_resp(rows=[_open_order()]))
    with pytest.raises(KISUsageError):
        _bonds(fake).open_orders("2024-02-15")
    assert fake.calls == []


def test_open_orders_paper_fails_closed():
    fake = FakeTransport(response=_open_orders_resp(rows=[_open_order()]))
    with pytest.raises(KISUsageError):
        _bonds(fake, environment="paper").open_orders("20240215")
    assert fake.calls == []


def test_open_orders_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={})
    with pytest.raises(KISError):
        _bonds(FakeTransport(response=resp)).open_orders("20240215")


def test_open_orders_paginates_two_pages():
    page1 = _open_orders_resp(rows=[_open_order(odno="1")], ctx_nk="NK", ctx_fk="FK", tr_cont="M")
    page2 = _open_orders_resp(rows=[_open_order(odno="2")], tr_cont="D")
    fake = FakeTransport(by_path={_OPEN_ORDERS_PATH: [page1, page2]})
    orders = _bonds(fake).open_orders("20240215")
    assert [o.order_id for o in orders] == ["1", "2"]
    assert len(fake.calls) == 2
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NK"
    assert fake.calls[1]["tr_cont"] == "N"


# --- fills (CTSC8013R -- output1/output2 SWAPPED at runtime) ----------------
_FILLS_TOTALS = {
    "tot_ord_qty": "3000", "tot_ccld_qty_smtl": "2000",
    "tot_bond_ccld_avg_unpr": "9855", "tot_ccld_amt_smtl": "19710000",
}


def _fill(odno="0000000123", *, ord_dt="20240215", orgn="0000000100", ord_dvsn_name="지정가",
          sll_buy="02", shtn="KR2033022D33", name="국민주택1종", ord_qty="2000", unpr="9855",
          ord_tmd="131438", ccld_qty="2000", avg="9855", ccld_amt="19710000", nccs="0",
          brno="12345"):
    return {"ord_dt": ord_dt, "odno": odno, "orgn_odno": orgn, "ord_dvsn_name": ord_dvsn_name,
            "sll_buy_dvsn_cd": sll_buy, "shtn_pdno": shtn, "prdt_abrv_name": name,
            "ord_qty": ord_qty, "bond_ord_unpr": unpr, "ord_tmd": ord_tmd,
            "tot_ccld_qty": ccld_qty, "bond_avg_unpr": avg, "tot_ccld_amt": ccld_amt,
            "nccs_qty": nccs, "ord_gno_brno": brno}


def _fills_resp(*, rows=None, totals=None, ctx_nk="", ctx_fk="", tr_cont=""):
    # LIVE: output1 = fill ROWS, output2 = TOTALS (ledger has these swapped).
    body = {"output1": rows if rows is not None else [],
            "output2": totals if totals is not None else dict(_FILLS_TOTALS),
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_fills_parses_rows_and_totals():
    fake = FakeTransport(response=_fills_resp(rows=[_fill()]))
    history = _bonds(fake).fills("20240201", "20240229")
    assert history.total_order_quantity == Decimal(3000)
    assert history.total_filled_quantity == Decimal(2000)
    assert history.avg_price == Decimal(9855)
    assert history.total_filled_amount == Decimal(19710000)
    assert len(history.fills) == 1
    fill = history.fills[0]
    assert fill.order_date == date(2024, 2, 15)
    assert fill.order_id == "0000000123"
    assert fill.original_order_id == "0000000100"
    assert fill.order_type == "지정가"
    assert fill.side == "buy"
    assert fill.symbol == "KR2033022D33"
    assert fill.name == "국민주택1종"
    assert fill.order_quantity == Decimal(2000)
    assert fill.order_price == Decimal(9855)
    assert fill.order_time == time(13, 14, 38)
    assert fill.filled_quantity == Decimal(2000)
    assert fill.avg_price == Decimal(9855)
    assert fill.filled_amount == Decimal(19710000)
    assert fill.unfilled_quantity == Decimal(0)
    assert fill.branch_number == "12345"


def test_fills_routes_with_params():
    fake = FakeTransport(response=_fills_resp(rows=[_fill()]))
    _bonds(fake).fills("20240201", "20240229", side="buy", symbol="KR2033022D33",
                       unfilled_only=True)
    call = fake.calls[0]
    assert call["path"] == _FILLS_PATH
    assert call["tr_id"] == "CTSC8013R"
    assert call["params"]["INQR_STRT_DT"] == "20240201"
    assert call["params"]["INQR_END_DT"] == "20240229"
    assert call["params"]["SLL_BUY_DVSN_CD"] == "02"
    assert call["params"]["PDNO"] == "KR2033022D33"
    assert call["params"]["NCCS_YN"] == "Y"
    assert call["params"]["SORT_SQN_DVSN"] == "00"


def test_fills_side_all_and_sell_codes():
    fake_all = FakeTransport(response=_fills_resp(rows=[_fill()]))
    _bonds(fake_all).fills("20240201", "20240229")
    assert fake_all.calls[0]["params"]["SLL_BUY_DVSN_CD"] == "00"
    assert fake_all.calls[0]["params"]["PDNO"] == ""
    assert fake_all.calls[0]["params"]["NCCS_YN"] == "N"
    fake_sell = FakeTransport(response=_fills_resp(rows=[_fill()]))
    _bonds(fake_sell).fills("20240201", "20240229", side="sell")
    assert fake_sell.calls[0]["params"]["SLL_BUY_DVSN_CD"] == "01"


def test_fills_skips_blank_odno():
    fake = FakeTransport(response=_fills_resp(rows=[_fill(), {"odno": "  "}]))
    assert len(_bonds(fake).fills("20240201", "20240229").fills) == 1


def test_fills_rejects_bad_side():
    fake = FakeTransport(response=_fills_resp(rows=[_fill()]))
    with pytest.raises(KISUsageError):
        _bonds(fake).fills("20240201", "20240229", side="both")
    assert fake.calls == []


def test_fills_rejects_bad_date():
    fake = FakeTransport(response=_fills_resp(rows=[_fill()]))
    with pytest.raises(KISUsageError):
        _bonds(fake).fills("2024", "20240229")
    assert fake.calls == []


def test_fills_paper_fails_closed():
    fake = FakeTransport(response=_fills_resp(rows=[_fill()]))
    with pytest.raises(KISUsageError):
        _bonds(fake, environment="paper").fills("20240201", "20240229")
    assert fake.calls == []


def test_fills_missing_output1_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output2": dict(_FILLS_TOTALS)})
    with pytest.raises(KISError):
        _bonds(FakeTransport(response=resp)).fills("20240201", "20240229")


def test_fills_missing_totals_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": []})
    with pytest.raises(KISError):
        _bonds(FakeTransport(response=resp)).fills("20240201", "20240229")


def test_fills_paginates_two_pages():
    page1 = _fills_resp(rows=[_fill(odno="1")], ctx_nk="NK", ctx_fk="FK", tr_cont="F")
    page2 = _fills_resp(rows=[_fill(odno="2")], tr_cont="D")
    fake = FakeTransport(by_path={_FILLS_PATH: [page1, page2]})
    history = _bonds(fake).fills("20240201", "20240229")
    assert [f.order_id for f in history.fills] == ["1", "2"]
    assert len(fake.calls) == 2
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NK"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"
