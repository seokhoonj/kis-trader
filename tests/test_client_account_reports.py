"""국내 계좌 리포트 -- realized_profit_balance (TTTC8494R) / integrated_margin (TTTC0869R).

둘 다 조회 전용·모의 미지원. 픽스처 필드는 원장 레이아웃/응답예시 기반(요약·증거금 일부는
레이아웃 기준으로 확증 전이라 값 자체가 아니라 매핑만 검증). FakeTransport 로 네트워크 없이 검증.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_openapi import IntegratedMargin, KISClient, RealizedProfitBalance
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_RLZ = "/uapi/domestic-stock/v1/trading/inquire-balance-rlz-pl"
_MGN = "/uapi/domestic-stock/v1/trading/intgr-margin"

_POS = {
    "pdno": "000080", "prdt_name": "하이트진로", "trad_dvsn_name": "현금",
    "hldg_qty": "2", "ord_psbl_qty": "2", "pchs_avg_pric": "22975.0000",
    "pchs_amt": "45950", "prpr": "22600", "evlu_amt": "45200",
    "evlu_pfls_amt": "-750", "evlu_pfls_rt": "-1.63",
    "loan_dt": "20240216", "loan_amt": "1000", "expd_dt": "20240916",
}
_SUM = {
    "dnca_tot_amt": "1000000", "nass_amt": "5000000", "tot_evlu_amt": "6000000",
    "pchs_amt_smtl_amt": "4000000", "evlu_amt_smtl_amt": "4200000",
    "evlu_pfls_smtl_amt": "200000", "asst_icdc_amt": "12345", "asst_icdc_erng_rt": "0.25",
    "rlzt_pfls": "33000", "rlzt_erng_rt": "1.10",
    "real_evlu_pfls": "199000", "real_evlu_pfls_erng_rt": "4.73",
}
_MARGIN = {
    "acmga_rt": "40", "stck_cash_ord_psbl_amt": "1000000",
    "stck_sbst_ord_psbl_amt": "500000", "rcvb_amt": "0", "lmt_amt": "9999999",
    "ovrs_stck_itgr_mgna_dvsn_name": "통합", "usd_itgr_ord_psbl_amt": "800.00",
    "hkd_itgr_ord_psbl_amt": "1000.00", "jpy_itgr_ord_psbl_amt": "50000",
    "cny_itgr_ord_psbl_amt": "3000.00", "usd_frst_bltn_exrt": "1350.5",
    "hkd_frst_bltn_exrt": "173.2", "jpy_frst_bltn_exrt": "9.12",
    "cny_frst_bltn_exrt": "188.4",
}


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


def _resp(*, output1=None, output2=None, output=None):
    body = {}
    if output1 is not None:
        body["output1"] = output1
    if output2 is not None:
        body["output2"] = output2
    if output is not None:
        body["output"] = output
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="")


# --- 실현손익 잔고 (TTTC8494R) --------------------------------------------
def test_realized_balance_parses():
    resp = _resp(output1=[_POS], output2=[_SUM])
    bal = _client(FakeTransport(response=resp)).domestic.account.realized_profit_balance()
    assert isinstance(bal, RealizedProfitBalance)
    assert len(bal.positions) == 1
    p = bal.positions[0]
    assert p.symbol == "000080"
    assert p.name == "하이트진로"
    assert p.holding_quantity == Decimal(2)
    assert p.unrealized_pnl == Decimal(-750)
    assert p.loan_date == date(2024, 2, 16)
    assert p.loan_amount == Decimal(1000)
    assert p.expiry_date == date(2024, 9, 16)
    assert bal.realized_pnl == Decimal(33000)
    assert bal.real_eval_pnl == Decimal(199000)
    assert bal.net_asset == Decimal(5000000)


def test_realized_balance_tr_and_params():
    fake = FakeTransport(response=_resp(output1=[_POS], output2=[_SUM]))
    _client(fake).domestic.account.realized_profit_balance()
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC8494R"
    assert call["path"] == _RLZ
    assert call["method"] == "GET"
    assert call["idempotent"] is True


def test_realized_balance_object_output2_ok():
    # output2 가 객체(배열 아님)로 와도 요약을 잡는다.
    resp = _resp(output1=[_POS], output2=_SUM)
    bal = _client(FakeTransport(response=resp)).domestic.account.realized_profit_balance()
    assert bal.total_deposit == Decimal(1000000)


def test_realized_balance_empty_positions_ok():
    bal = _client(FakeTransport(response=_resp(output1=[], output2=[_SUM]))).domestic.account.realized_profit_balance()
    assert bal.positions == ()
    assert bal.realized_pnl == Decimal(33000)


def test_realized_balance_bad_blank_loan_date_is_none():
    row = dict(_POS, loan_dt="", expd_dt="")
    bal = _client(FakeTransport(response=_resp(output1=[row], output2=[_SUM]))).domestic.account.realized_profit_balance()
    assert bal.positions[0].loan_date is None
    assert bal.positions[0].expiry_date is None


def test_realized_balance_non_list_output1_fails_closed():
    resp = _resp(output1={"pdno": "x"}, output2=[_SUM])
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).domestic.account.realized_profit_balance()


def test_realized_balance_demo_rejected():
    fake = FakeTransport(response=_resp(output1=[_POS], output2=[_SUM]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").domestic.account.realized_profit_balance()
    assert fake.calls == []


# --- 통합증거금 (TTTC0869R) ------------------------------------------------
def test_integrated_margin_parses():
    m = _client(FakeTransport(response=_resp(output=_MARGIN))).domestic.account.integrated_margin()
    assert isinstance(m, IntegratedMargin)
    assert m.account_margin_rate == Decimal(40)
    assert m.cash_orderable == Decimal(1000000)
    assert m.integrated_margin_type == "통합"
    assert m.usd_orderable == Decimal("800.00")
    assert m.cny_exchange_rate == Decimal("188.4")
    # headline 외 필드는 _raw 로 접근 가능
    assert m._raw["hkd_itgr_ord_psbl_amt"] == "1000.00"


def test_integrated_margin_tr_and_params():
    fake = FakeTransport(response=_resp(output=_MARGIN))
    _client(fake).domestic.account.integrated_margin(include_cma=True, won_basis=False)
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC0869R"
    assert call["path"] == _MGN
    assert call["params"]["CMA_EVLU_AMT_ICLD_YN"] == "Y"
    assert call["params"]["WCRC_FRCR_DVSN_CD"] == "01"       # 외화기준
    assert call["params"]["FWEX_CTRT_FRCR_DVSN_CD"] == "01"


def test_integrated_margin_won_basis_default():
    fake = FakeTransport(response=_resp(output=_MARGIN))
    _client(fake).domestic.account.integrated_margin()
    assert fake.calls[0]["params"]["WCRC_FRCR_DVSN_CD"] == "02"   # 원화기준
    assert fake.calls[0]["params"]["CMA_EVLU_AMT_ICLD_YN"] == "N"


def test_integrated_margin_non_object_output_fails_closed():
    with pytest.raises(KISError):
        _client(FakeTransport(response=_resp(output=[_MARGIN]))).domestic.account.integrated_margin()


def test_integrated_margin_demo_rejected():
    fake = FakeTransport(response=_resp(output=_MARGIN))
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").domestic.account.integrated_margin()
    assert fake.calls == []


def test_reports_require_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp(output=_MARGIN)), account=None).domestic.account.integrated_margin()
