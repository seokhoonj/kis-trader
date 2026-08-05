"""채권 기본정보 -- kis.bond(code).info()."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import BondInfo, KISClient
from kis_openapi.errors import KISError
from kis_openapi.transport import RawResponse


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _client(t):
    return KISClient(app_key="k", app_secret="s", transport=t)


def test_bond_info_maps():
    out = {"ksd_bond_item_name": "국고03750-3312", "ksd_bond_item_eng_name": "KTB",
           "iso_crcy_cd": "KRW", "issu_dt": "20201210", "rdpt_dt": "20331210",
           "lstg_dt": "20201211", "ksd_rcvg_bond_srfc_inrt": "3.750",
           "ksd_rcvg_bond_dsct_rt": "0.000", "bond_expd_rdpt_rt": "100.0",
           "bond_expd_asrc_erng_rt": "3.85", "int_caltm_mcnt": "6"}
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": out}))
    info = _client(fake).bond("KR2033022D33").info()
    assert isinstance(info, BondInfo)
    assert info.name == "국고03750-3312"
    assert info.currency == "KRW"
    assert f"{info.issue_date:%Y%m%d}" == "20201210"
    assert f"{info.maturity_date:%Y%m%d}" == "20331210"
    assert info.coupon_rate == Decimal("3.750")
    assert info.yield_to_maturity == Decimal("3.85")
    assert info.interest_period_months == 6
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-bond/v1/quotations/search-bond-info"
    assert call["tr_id"] == "CTPF1114R"
    assert call["params"]["PDNO"] == "KR2033022D33"
    assert call["params"]["PRDT_TYPE_CD"] == "302"


def test_bond_info_optional_none():
    out = {"ksd_bond_item_name": "x", "ksd_bond_item_eng_name": "x", "iso_crcy_cd": "KRW",
           "issu_dt": "", "rdpt_dt": "", "lstg_dt": "", "ksd_rcvg_bond_srfc_inrt": "",
           "ksd_rcvg_bond_dsct_rt": "", "bond_expd_rdpt_rt": "", "bond_expd_asrc_erng_rt": "",
           "int_caltm_mcnt": ""}
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": out}))
    info = _client(fake).bond("KR2033022D33").info()
    assert info.issue_date is None
    assert info.coupon_rate is None
    assert info.interest_period_months is None


def test_bond_info_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).bond("KR2033022D33").info()
