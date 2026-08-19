"""해외선물옵션(08) 계좌 조회 -- kis.account -> OverseasDerivativesAccount.

예수금현황/미결제(보유)/주문가능을 네트워크 없이 FakeTransport 로 검증한다. 모든 조회는 실전
전용이라 모의(paper)면 와이어 이전에 fail-closed 한다. 금액·수량은 조회 통화의 Decimal(원화 아님).
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient
from kis_trader.errors import KISError, KISUsageError
from kis_trader.overseas.derivative_account import OverseasDerivativesAccount
from kis_trader.overseas.entities.derivative_account import OverseasDerivativeDeposit
from kis_trader.transport import RawResponse

_DEPOSIT_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-deposit"


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


def _client(transport, *, environment="real", account="12345678-08"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


_DEPOSIT = {
    "crcy_cd": "USD",
    "fm_dnca_rmnd": "100000.50", "fm_tot_asst_evlu_amt": "125000.75",
    "fm_fuop_evlu_pfls_amt": "1500.25", "fm_lqd_pfls_amt": "300.00",
    "fm_brkg_mgn_amt": "20000.00", "fm_mntn_mgn_amt": "18000.00",
    "fm_add_mgn_amt": "0", "fm_risk_rt": "45.30",
    "fm_ord_psbl_amt": "80000.00", "fm_drwg_psbl_amt": "75000.00",
    "fm_rcvb_amt": "0", "fm_nxdy_dncl_amt": "100000.50",
    "fm_opt_evlu_amt": "500.00", "fm_fee": "12.50",
}


def _deposit_resp(*, output=None):
    body = {"output": output if output is not None else dict(_DEPOSIT)}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="D")


def test_deposit_paper_fails_closed():
    fake = FakeTransport(response=_deposit_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.deposit()
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_deposit_parses_and_routes():
    fake = FakeTransport(response=_deposit_resp())
    kis = _client(fake)
    assert isinstance(kis.account, OverseasDerivativesAccount)
    dep = kis.account.deposit(currency="USD", date="20240216")
    assert isinstance(dep, OverseasDerivativeDeposit)
    assert dep.currency == "USD"                                    # crcy_cd
    assert dep.cash_balance == Decimal("100000.50")                # fm_dnca_rmnd
    assert dep.total_asset == Decimal("125000.75")                 # fm_tot_asst_evlu_amt
    assert dep.unrealized_pnl == Decimal("1500.25")                # fm_fuop_evlu_pfls_amt
    assert dep.realized_pnl == Decimal("300.00")                   # fm_lqd_pfls_amt
    assert dep.brokerage_margin == Decimal("20000.00")            # fm_brkg_mgn_amt
    assert dep.risk_rate == Decimal("45.30")                      # fm_risk_rt
    assert dep.orderable_amount == Decimal("80000.00")           # fm_ord_psbl_amt
    assert dep.fee == Decimal("12.50")                           # fm_fee
    call = fake.calls[0]
    assert call["tr_id"] == "OTFM1411R"
    assert call["path"].endswith("inquire-deposit")
    assert call["params"]["CRCY_CD"] == "USD"
    assert call["params"]["INQR_DT"] == "20240216"
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "08"


def test_deposit_default_currency_and_date():
    fake = FakeTransport(response=_deposit_resp())
    _client(fake).account.deposit()
    call = fake.calls[0]
    assert call["params"]["CRCY_CD"] == "USD"                       # 기본 통화
    assert len(call["params"]["INQR_DT"]) == 8                      # 오늘(YYYYMMDD)
    assert call["params"]["INQR_DT"].isdigit()


def test_deposit_bad_date_fails_closed():
    fake = FakeTransport(response=_deposit_resp())
    with pytest.raises(KISUsageError):
        _client(fake).account.deposit(date="2024-02-16")
    assert fake.calls == []  # 날짜 검증도 와이어 이전


def test_deposit_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={}, tr_cont="D")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.deposit()


def test_deposit_blank_amounts_read_as_zero():
    output = dict(_DEPOSIT, fm_add_mgn_amt="", fm_rcvb_amt="  ")
    dep = _client(FakeTransport(response=_deposit_resp(output=output))).account.deposit()
    assert dep.additional_margin == Decimal(0)
    assert dep.receivable == Decimal(0)


def test_overseas_derivative_deposit_entity_importable():
    from kis_trader import OverseasDerivativeDeposit as Exported

    assert Exported is not None
