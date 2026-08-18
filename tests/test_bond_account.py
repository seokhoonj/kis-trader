"""장내채권 계좌 조회 -- kis.account.domestic.bonds (실전전용).

잔고/매수가능/미체결/체결을 네트워크 없이 FakeTransport 로 검증한다. 모든 조회는 모의투자
미지원이라 environment="paper" 면 와이어를 타기 전에 KISUsageError 로 fail-closed 한다.
"""

from __future__ import annotations

import threading
from datetime import date
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
