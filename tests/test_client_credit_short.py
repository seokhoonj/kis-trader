"""per-ticker 일별 시세분석 -- credit_balance_trend / short_sale_trend."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import CreditBalancePoint, KISClient, ShortSalePoint
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


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def test_credit_balance_trend_maps():
    rows = [{"deal_date": "20240102", "stck_prpr": "70000", "prdy_vrss": "500",
             "prdy_vrss_sign": "2", "prdy_ctrt": "0.72", "acml_vol": "1000",
             "whol_loan_rmnd_stcn": "50000", "whol_loan_rmnd_amt": "3500000000",
             "whol_loan_rmnd_rate": "0.5", "whol_stln_rmnd_stcn": "2000",
             "whol_stln_rmnd_amt": "140000000", "whol_stln_rmnd_rate": "0.02"}]
    fake = FakeTransport(response=_resp(rows))
    pts = _client(fake).ticker("005930").credit_balance_trend(date="20240102")
    assert isinstance(pts[0], CreditBalancePoint)
    assert pts[0].margin_loan_shares == 50000
    assert pts[0].margin_loan_amount == Decimal(3500000000)
    assert pts[0].stock_loan_shares == 2000
    assert f"{pts[0].timestamp:%Y%m%d}" == "20240102"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/daily-credit-balance"
    assert call["tr_id"] == "FHPST04760000"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240102"


def test_short_sale_trend_maps():
    rows = [{"stck_bsop_date": "20240102", "stck_clpr": "70000", "prdy_vrss": "500",
             "prdy_vrss_sign": "5", "prdy_ctrt": "0.72", "acml_vol": "10000",
             "ssts_cntg_qty": "1500", "ssts_vol_rlim": "15.0", "ssts_tr_pbmn": "105000000",
             "avrg_prc": "70050"}]
    fake = FakeTransport(response=_resp(rows))
    pts = _client(fake).ticker("005930").short_sale_trend(start="20240101", end="20240102")
    assert isinstance(pts[0], ShortSalePoint)
    assert pts[0].short_volume == 1500
    assert pts[0].short_volume_ratio == Decimal("15.0")
    assert pts[0].change == Decimal(-500)                 # sign 5 -> 하락
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/daily-short-sale"
    assert call["tr_id"] == "FHPST04830000"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240101"
    assert call["params"]["FID_INPUT_DATE_2"] == "20240102"


def test_credit_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").credit_balance_trend()


def test_short_bad_value_fails_closed():
    rows = [{"stck_bsop_date": "20240102", "stck_clpr": "70000", "prdy_vrss": "0",
             "prdy_vrss_sign": "3", "prdy_ctrt": "0", "acml_vol": "1", "ssts_cntg_qty": "n/a",
             "ssts_tr_pbmn": "0"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").short_sale_trend()


def test_loan_trend_maps():
    from kis_openapi import LoanPoint
    rows = [{"bsop_date": "20240102", "stck_prpr": "70000", "prdy_vrss": "0",
             "prdy_vrss_sign": "3", "prdy_ctrt": "0", "acml_vol": "1000",
             "new_stcn": "5000", "rdmp_stcn": "2000", "rmnd_stcn": "100000",
             "rmnd_amt": "7000000000", "prdy_rmnd_vrss": "3000"}]
    fake = FakeTransport(response=_resp(rows))
    pts = _client(fake).ticker("005930").loan_trend(start="20240101", end="20240102")
    assert isinstance(pts[0], LoanPoint)
    assert pts[0].new_shares == 5000
    assert pts[0].balance_shares == 100000
    assert pts[0].balance_amount == Decimal(7000000000)
    assert pts[0].balance_change == 3000
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/daily-loan-trans"
    assert call["tr_id"] == "HHPST074500C0"
    assert call["params"]["MKSC_SHRN_ISCD"] == "005930"
    assert call["params"]["START_DATE"] == "20240101"
