"""투자계좌 자산현황 -- kis.domestic.account.assets() (CTRP6548R).

계좌 전반 자산 요약(output2)을 네트워크 없이 검증한다. 픽스처는 원장 응답예시
(inquire-account-balance)의 실값을 쓴다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import AccountAssets, KISClient
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_PATH = "/uapi/domestic-stock/v1/trading/inquire-account-balance"

# 원장 응답예시(inquire-account-balance) output2 실값.
_OUTPUT2 = {
    "pchs_amt_smtl": "161155333", "nass_tot_amt": "185550504", "loan_amt_smtl": "0",
    "evlu_pfls_amt_smtl": "24395171", "evlu_amt_smtl": "185550504",
    "tot_asst_amt": "1651869889547", "tot_lnda_tot_ulst_lnda": "0", "cma_auto_loan_amt": "0",
    "tot_mgln_amt": "0", "stln_evlu_amt": "0", "crdt_fncg_amt": "0", "ocl_apl_loan_amt": "0",
    "pldg_stup_amt": "0", "frcr_evlu_tota": "1651434483743", "tot_dncl_amt": "249855300",
    "cma_evlu_amt": "0", "dncl_amt": "249855300", "tot_sbst_amt": "0", "thdt_rcvb_amt": "0",
    "ovrs_stck_evlu_amt1": "185144504.000000", "ovrs_bond_evlu_amt": "0.000000",
}
_OUTPUT1 = [{"pchs_amt": "161155333", "evlu_amt": "185550504", "whol_weit_rt": "100.00000000"}]


def _resp(output2=None, output1=None):
    body = {"output1": _OUTPUT1 if output1 is None else output1,
            "output2": _OUTPUT2 if output2 is None else output2}
    return RawResponse(rt_cd="0", msg_cd="KIOK0530", msg1="조회", body=body, tr_cont="")


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


def test_account_assets_parses_summary():
    assets = _client(FakeTransport(response=_resp())).domestic.account.assets()
    assert isinstance(assets, AccountAssets)
    assert assets.total_asset_amount == Decimal(1651869889547)
    assert assets.net_asset_total == Decimal(185550504)
    assert assets.purchase_amount_total == Decimal(161155333)
    assert assets.evaluation_pnl_total == Decimal(24395171)
    assert assets.deposit == Decimal(249855300)
    assert assets.foreign_evaluation_total == Decimal(1651434483743)
    assert assets.overseas_stock_evaluation == Decimal("185144504.000000")
    assert assets.today_receivable == Decimal(0)
    # 자산군별 내역(output1)은 _raw 로 접근
    assert assets._raw["output1"][0]["whol_weit_rt"] == "100.00000000"


def test_account_assets_tr_method_params():
    fake = FakeTransport(response=_resp())
    _client(fake).domestic.account.assets()
    call = fake.calls[0]
    assert call["tr_id"] == "CTRP6548R"
    assert call["method"] == "GET"
    assert call["path"] == _PATH
    assert call["idempotent"] is True
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["INQR_DVSN_1"] == ""


def test_account_assets_demo_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").domestic.account.assets()
    assert fake.calls == []


def test_account_assets_missing_summary_fails_closed():
    body = {"output1": _OUTPUT1, "output2": []}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).domestic.account.assets()


def test_account_assets_missing_field_fails_closed():
    thin = dict(_OUTPUT2)
    del thin["tot_asst_amt"]
    with pytest.raises(KISError):
        _client(FakeTransport(response=_resp(output2=thin))).domestic.account.assets()


def test_account_assets_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="ERR", msg1="실패", body={"output2": {}}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).domestic.account.assets()


def test_account_assets_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).domestic.account.assets()
