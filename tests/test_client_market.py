"""시장 전체 분석 -- kis.market.investor_flows()."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, MarketInvestorFlow
from kis_openapi.errors import KISError, KISUsageError
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
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": rows})


def test_market_investor_flows_maps_and_params():
    rows = [{"stck_bsop_date": "20240510", "bstp_nmix_prpr": "2700.50",
             "bstp_nmix_prdy_vrss": "15.0", "prdy_vrss_sign": "2", "bstp_nmix_prdy_ctrt": "0.56",
             "frgn_ntby_qty": "1200000", "prsn_ntby_qty": "-500000", "orgn_ntby_qty": "-700000",
             "scrt_ntby_qty": "100"}]
    fake = FakeTransport(response=_resp(rows))
    flows = _client(fake).market.investor_flows(market="KOSPI", start="20240101", end="20240513")
    assert isinstance(flows[0], MarketInvestorFlow)
    f = flows[0]
    assert f.market == "KOSPI"
    assert f.index_value == Decimal("2700.50")
    assert f.index_change == Decimal("15.0")             # sign 2 -> 상승
    assert f.foreign_net == 1200000
    assert f.individual_net == -500000                   # pre-signed
    assert f.institutional_net == -700000
    assert f._raw["scrt_ntby_qty"] == "100"              # 세부 주체는 _raw
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market"
    assert call["tr_id"] == "FHPTJ04040000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "U"
    assert call["params"]["FID_INPUT_ISCD"] == "0001"    # KOSPI
    assert call["params"]["FID_INPUT_ISCD_1"] == "KSP"


def test_market_investor_flows_kosdaq_code():
    fake = FakeTransport(response=_resp([]))
    _client(fake).market.investor_flows(market="KOSDAQ", start="20240101", end="20240131")
    assert fake.calls[0]["params"]["FID_INPUT_ISCD"] == "1001"
    assert fake.calls[0]["params"]["FID_INPUT_ISCD_1"] == "KSQ"


def test_market_investor_flows_default_window():
    fake = FakeTransport(response=_resp([]))
    _client(fake).market.investor_flows(end="20240131")
    call = fake.calls[0]
    assert call["params"]["FID_INPUT_DATE_1"] == "20240101"       # 30일 전
    assert call["params"]["FID_INPUT_DATE_2"] == "20240131"


def test_market_investor_flows_rejects_bad_market():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).market.investor_flows(market="NYSE")


def test_market_investor_flows_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).market.investor_flows()
