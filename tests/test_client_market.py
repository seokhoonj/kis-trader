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


def _flow_row(**over):
    row = {"stck_bsop_date": "20240510", "bstp_nmix_prpr": "2700.50",
           "bstp_nmix_prdy_vrss": "15.0", "prdy_vrss_sign": "2", "bstp_nmix_prdy_ctrt": "0.56",
           "frgn_ntby_qty": "1200000", "prsn_ntby_qty": "-500000", "orgn_ntby_qty": "-700000",
           "scrt_ntby_qty": "100"}
    row.update(over)
    return row


def test_market_investor_flows_maps_signed_and_anchor_params():
    fake = FakeTransport(response=_resp([_flow_row()]))
    flows = _client(fake).market.investor_flows(market="KOSPI", as_of="20240510")
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
    # 앵커 엔드포인트: DATE_1 == DATE_2 == as_of (원장: DATE_2 는 DATE_1 과 동일날짜).
    assert call["params"]["FID_INPUT_DATE_1"] == "20240510"
    assert call["params"]["FID_INPUT_DATE_2"] == "20240510"


def test_market_investor_flows_index_down_sign():
    fake = FakeTransport(response=_resp([_flow_row(prdy_vrss_sign="5", bstp_nmix_prdy_ctrt="0.56")]))
    f = _client(fake).market.investor_flows(as_of="20240510")[0]
    assert f.index_change == Decimal("-15.0")            # sign 5 -> 하락
    assert f.index_change_percent == Decimal("-0.56")


def test_market_investor_flows_kosdaq_code():
    fake = FakeTransport(response=_resp([]))
    _client(fake).market.investor_flows(market="KOSDAQ", as_of="20240131")
    assert fake.calls[0]["params"]["FID_INPUT_ISCD"] == "1001"
    assert fake.calls[0]["params"]["FID_INPUT_ISCD_1"] == "KSQ"


def test_market_investor_flows_rejects_bad_market():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).market.investor_flows(market="NYSE")


def test_market_investor_flows_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).market.investor_flows()


def test_market_investor_flows_bad_value_fails_closed():
    fake = FakeTransport(response=_resp([_flow_row(frgn_ntby_qty="n/a")]))
    with pytest.raises(KISError):
        _client(fake).market.investor_flows(as_of="20240510")


def _prog_row(**over):
    # 값 필드는 모두 smtn. smtm 은 _rate(비율) 필드에만 붙는 오탈자다(원장 확인).
    row = {"stck_bsop_date": "20240510", "arbt_smtn_ntby_qty": "12000",
           "arbt_smtn_ntby_tr_pbmn": "84000000", "nabt_smtn_ntby_qty": "-5000",
           "nabt_smtn_ntby_tr_pbmn": "-35000000", "arbt_smtm_ntby_qty_rate": "0.4"}
    row.update(over)
    return row


def test_program_trade_summary_maps_smtn_fields():
    from kis_openapi import ProgramTradeSummary
    fake = FakeTransport(response=_resp([_prog_row()]))
    s = _client(fake).market.program_trades(market="KOSPI", start="20240101", end="20240513")[0]
    assert isinstance(s, ProgramTradeSummary)
    assert s.arbitrage_net_volume == 12000               # arbt_smtn_ntby_qty (NOT the _rate field)
    assert s.arbitrage_net_amount == Decimal(84000000)
    assert s.nonarb_net_volume == -5000                  # pre-signed 순매도
    assert s.total_net_volume == 12000 - 5000            # 차익 + 비차익
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily"
    assert call["tr_id"] == "FHPPG04600001"
    assert call["params"]["FID_MRKT_CLS_CODE"] == "K"


def test_program_trade_summary_kosdaq_and_bad_market():
    fake = FakeTransport(response=_resp([]))
    _client(fake).market.program_trades(market="KOSDAQ", start="20240101", end="20240131")
    assert fake.calls[0]["params"]["FID_MRKT_CLS_CODE"] == "Q"
    with pytest.raises(KISUsageError):
        _client(fake).market.program_trades(market="US")


def test_program_trade_summary_bad_value_fails_closed():
    fake = FakeTransport(response=_resp([_prog_row(arbt_smtn_ntby_qty="n/a")]))
    with pytest.raises(KISError):
        _client(fake).market.program_trades()


def test_program_trade_summary_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).market.program_trades()
