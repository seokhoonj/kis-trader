"""채권 기본정보 -- kis.domestic.bond(code).profile()."""
from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_trader import BondIssuance, BondProfile, KISClient
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse


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
    info = _client(fake).domestic.bond("KR2033022D33").profile()
    assert isinstance(info, BondProfile)
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
    info = _client(fake).domestic.bond("KR2033022D33").profile()
    assert info.issue_date is None
    assert info.coupon_rate is None
    assert info.interest_period_months is None


def test_bond_info_date_fields_are_pure_date():
    # B-11: 채권 일자 속성은 datetime 이 아니라 순수 date 여야 한다.
    out = {"ksd_bond_item_name": "국고03750-3312", "ksd_bond_item_eng_name": "KTB",
           "iso_crcy_cd": "KRW", "issu_dt": "20201210", "rdpt_dt": "20331210",
           "lstg_dt": "20201211"}
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": out}))
    info = _client(fake).domestic.bond("KR2033022D33").profile()
    assert type(info.issue_date) is date
    assert info.issue_date == date(2020, 12, 10)
    assert type(info.maturity_date) is date
    assert type(info.listing_date) is date


def test_bond_info_bad_date_fails_closed():
    # 비어있지 않은 잘못된 날짜는 조용히 None 이 아니라 fail-closed.
    out = {"ksd_bond_item_name": "x", "ksd_bond_item_eng_name": "x", "iso_crcy_cd": "KRW",
           "issu_dt": "20230230"}
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": out}))
    with pytest.raises(KISError):
        _client(fake).domestic.bond("KR2033022D33").profile()


def test_bond_info_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.bond("KR2033022D33").profile()


def test_bond_info_zero_date_sentinel_is_none():
    out = {"ksd_bond_item_name": "x", "ksd_bond_item_eng_name": "x", "iso_crcy_cd": "KRW",
           "issu_dt": "00000000", "rdpt_dt": "00000000", "lstg_dt": "00000000"}
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": out}))
    info = _client(fake).domestic.bond("KR2033022D33").profile()
    assert info.issue_date is None                        # 0-채움 센티넬 -> None (크래시 아님)
    assert info.maturity_date is None
    assert info.listing_date is None


def test_bond_info_bad_value_fails_closed():
    out = {"ksd_bond_item_name": "x", "ksd_bond_item_eng_name": "x", "iso_crcy_cd": "KRW",
           "ksd_rcvg_bond_srfc_inrt": "n/a"}
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": out}))
    with pytest.raises(KISError):
        _client(fake).domestic.bond("KR2033022D33").profile()


def _issuance_output(**over):
    output = {
        "pdno": "KR6449111CB8", "prdt_name": "테스트채권", "prdt_eng_name": "Test Bond",
        "bond_clsf_kor_name": "일반사채", "papr": "10000", "issu_amt": "77839700000",
        "lstg_rmnd": "70000000000", "issu_istt_name": "테스트 발행기관",
        "int_dfrm_mcnt": "3", "srfc_inrt": "5.931", "dsct_ec_rt": "0.000",
        "expd_rdpt_rt": "100.000", "expd_asrc_erng_rt": "5.931",
        "issu_dt": "20221116", "lstg_dt": "20221116", "expd_dt": "20241116",
        "rdpt_dt": "20241116", "rgbf_int_dfrm_dt": "20240516",
        "nxtm_int_dfrm_dt": "20240816", "kis_crdt_grad_text": "AAA",
        "kbp_crdt_grad_text": "AAA", "nice_crdt_grad_text": "AAA",
        "fnp_crdt_grad_text": "", "prcm_idx_bond_yn": "N",
        "bond_tr_stop_dvsn_cd": "N", "elec_scty_yn": "Y",
    }
    output.update(over)
    return output


def test_bond_issuance_maps_detailed_terms_and_status():
    fake = FakeTransport(
        response=RawResponse(
            rt_cd="0", msg_cd="KIOK0530", msg1="정상", body={"output": _issuance_output()}
        )
    )
    issuance = _client(fake).domestic.bond("KR6449111CB8").issuance()
    assert isinstance(issuance, BondIssuance)
    assert issuance.code == "KR6449111CB8"
    assert issuance.classification == "일반사채"
    assert issuance.face_value == Decimal(10000)
    assert issuance.issue_amount == Decimal(77839700000)
    assert issuance.outstanding_amount == Decimal(70000000000)
    assert issuance.issuer_name == "테스트 발행기관"
    assert issuance.interest_payment_months == 3
    assert issuance.coupon_rate == Decimal("5.931")
    assert f"{issuance.maturity_date:%Y%m%d}" == "20241116"
    assert issuance.credit_ratings == {"KIS": "AAA", "KBP": "AAA", "NICE": "AAA"}
    assert issuance.is_inflation_linked is False
    assert issuance.is_trade_suspended is False
    assert issuance.is_electronic is True
    assert fake.calls[0] == {
        "path": "/uapi/domestic-bond/v1/quotations/issue-info",
        "tr_id": "CTPF1101R",
        "params": {"PDNO": "KR6449111CB8", "PRDT_TYPE_CD": "302"},
    }


def test_bond_issuance_optional_dates_and_sparse_ratings():
    output = _issuance_output(
        issu_dt="", lstg_dt="00000000", rgbf_int_dfrm_dt="", fnp_crdt_grad_text="AA+",
        bond_tr_stop_dvsn_cd="Y",
    )
    fake = FakeTransport(
        response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": output})
    )
    issuance = _client(fake).domestic.bond("KR6449111CB8").issuance()
    assert issuance.issue_date is None
    assert issuance.listing_date is None
    assert issuance.credit_ratings["FNP"] == "AA+"
    assert issuance.is_trade_suspended is True


def test_bond_issuance_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.bond("KR6449111CB8").issuance()


def test_bond_issuance_bad_required_value_fails_closed():
    fake = FakeTransport(
        response=RawResponse(
            rt_cd="0", msg_cd="X", msg1="ok", body={"output": _issuance_output(papr="n/a")}
        )
    )
    with pytest.raises(KISError):
        _client(fake).domestic.bond("KR6449111CB8").issuance()
