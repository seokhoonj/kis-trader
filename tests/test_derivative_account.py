"""국내선물옵션(03) 계좌 조회 -- kis.account -> DomesticDerivativesAccount.balance().

선물옵션 잔고(보유내역 output1 + 계좌 요약 output2)를 네트워크 없이 FakeTransport 로 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient
from kis_trader.domestic.derivative_account import DomesticDerivativesAccount
from kis_trader.domestic.entities.derivative_account import DerivativeBalance, DerivativeDeposit
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_BALANCE_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-balance"
_DEPOSIT_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-deposit"


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


_SUMMARY = {
    "tot_dncl_amt": "50000000", "dnca_cash": "48000000", "mgna_tota": "20000000",
    "ord_psbl_cash": "30000000", "ord_psbl_tota": "31000000",
    "evlu_pfls_amt_smtl": "12345", "trad_pfls_amt_smtl": "6789",
    "futr_evlu_pfls_amt": "12345", "opt_evlu_pfls_amt": "0",
    "futr_trad_pfls_amt": "6789", "opt_trad_pfls_amt": "0",
    "prsm_dpast_amt": "51000000",
}


def _position(shtn="101W09", *, pdno="KR4101RC0000", name="코스피200 F 202509", side="매수",
              qty="3", excc="410.50", avg="408.25", pchs="122475000", evlu="123150000",
              pnl="12345", trad="6789", lqd="3"):
    return {"shtn_pdno": shtn, "pdno": pdno, "prdt_name": name, "sll_buy_dvsn_name": side,
            "cblc_qty": qty, "excc_unpr": excc, "ccld_avg_unpr1": avg, "pchs_amt": pchs,
            "evlu_amt": evlu, "evlu_pfls_amt": pnl, "trad_pfls_amt": trad, "lqd_psbl_qty": lqd}


def _balance_resp(*, rows=None, summary=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output1": rows if rows is not None else [],
            "output2": summary if summary is not None else dict(_SUMMARY),
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def _client(transport, *, environment="paper", account="12345678-03"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def test_derivative_balance_parses_and_routes():
    fake = FakeTransport(response=_balance_resp(rows=[_position()]))
    kis = _client(fake)
    assert isinstance(kis.account, DomesticDerivativesAccount)
    bal = kis.account.balance()
    assert isinstance(bal, DerivativeBalance)
    assert bal.positions[0].symbol == "101W09"          # shtn_pdno, NOT pdno
    assert bal.positions[0].isin == "KR4101RC0000"      # pdno
    assert bal.positions[0].unrealized_pnl == Decimal(12345)
    assert bal.positions[0].quantity == Decimal(3)
    assert bal.total_unrealized_pnl == Decimal(12345)  # evlu_pfls_amt_smtl
    assert bal.account_value == Decimal(51000000)      # prsm_dpast_amt
    assert bal.total_margin == Decimal(20000000)       # mgna_tota
    call = fake.calls[0]
    assert call["tr_id"] == "VTFO6118R"
    assert call["path"].endswith("inquire-balance")
    assert call["params"]["MGNA_DVSN"] == "01"
    assert call["params"]["EXCC_STAT_CD"] == "1"
    assert call["params"]["CTX_AREA_FK200"] == ""
    assert call["params"]["CTX_AREA_NK200"] == ""
    assert call["params"]["CANO"] == "12345678"


def test_derivative_balance_real_tr():
    fake = FakeTransport(response=_balance_resp(rows=[_position()]))
    _client(fake, environment="real").account.balance()
    assert fake.calls[0]["tr_id"] == "CTFO6118R"


def test_derivative_balance_paginates():
    page1 = _balance_resp(rows=[_position("101W09")], ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _balance_resp(rows=[_position("201X12", pdno="KR4201RC0000")], tr_cont="D")
    fake = FakeTransport(by_path={_BALANCE_PATH: [page1, page2]})
    bal = _client(fake).account.balance()
    assert [p.symbol for p in bal.positions] == ["101W09", "201X12"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"


def test_derivative_balance_missing_output2_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": [], "ctx_area_nk200": "", "ctx_area_fk200": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.balance()


def test_derivative_balance_skips_blank_symbol_row():
    rows = [_position("101W09"), _position("", pdno="")]
    bal = _client(FakeTransport(response=_balance_resp(rows=rows))).account.balance()
    assert [p.symbol for p in bal.positions] == ["101W09"]


_DEPOSIT = {
    "dnca_tota": "50000000", "ord_psbl_cash": "30000000", "ord_psbl_tota": "31000000",
    "brkg_mgna_cash": "18000000", "brkg_mgna_sbst": "2000000", "mtnc_rt": "418.23000000",
    "evlu_pfls_smtl": "12345", "trad_pfls_smtl": "6789",
    "futr_evlu_pfls_amt": "12345", "opt_evlu_pfls_amt": "0",
    "futr_trad_pfls": "6789", "opt_trad_pfls_amt": "0",
    "prsm_dpast_amt": "51000000", "rcva": "0",
}


def _deposit_resp(*, output=None):
    body = {"output": output if output is not None else dict(_DEPOSIT)}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="D")


def test_derivative_deposit_paper_fails_closed():
    fake = FakeTransport(response=_deposit_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.deposit()
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_derivative_deposit_parses_and_routes():
    fake = FakeTransport(response=_deposit_resp())
    dep = _client(fake, environment="real").account.deposit()
    assert isinstance(dep, DerivativeDeposit)
    assert dep.total_deposit == Decimal(50000000)              # dnca_tota
    assert dep.available_cash == Decimal(30000000)             # ord_psbl_cash
    assert dep.maintenance_ratio == Decimal("418.23000000")    # mtnc_rt
    assert dep.account_value == Decimal(51000000)              # prsm_dpast_amt
    assert dep.receivable == Decimal(0)                        # rcva
    assert dep.brokerage_margin_cash == Decimal(18000000)      # brkg_mgna_cash
    assert dep.brokerage_margin_substitute == Decimal(2000000)  # brkg_mgna_sbst
    assert dep.futures_realized_pnl == Decimal(6789)           # futr_trad_pfls (no _amt)
    call = fake.calls[0]
    assert call["tr_id"] == "CTRP6550R"
    assert call["path"].endswith("inquire-deposit")
    assert call["params"] == {"CANO": "12345678", "ACNT_PRDT_CD": "03"}


def test_derivative_deposit_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={}, tr_cont="D")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp), environment="real").account.deposit()
