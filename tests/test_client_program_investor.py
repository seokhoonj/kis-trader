"""per-ticker 시세분석 -- kis.ticker(code).program_trades() / .investor_estimate().

프로그램매매 흐름(TR FHPPG04650101, output)·투자자 순매수 추정(TR HHPTJ04160200, output2)의
TR·URL·시장구분·필드 매핑(순매수 수량/금액·외인/기관 추정), 시각 파싱, fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import InvestorEstimate, KISClient, ProgramTradePoint
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


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def _resp(body):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


def test_program_trades_maps_flow():
    rows = [{"bsop_hour": "100000", "stck_prpr": "70000", "prdy_vrss": "500",
             "prdy_vrss_sign": "2", "prdy_ctrt": "0.72", "acml_vol": "1000000",
             "whol_smtn_seln_vol": "30000", "whol_smtn_shnu_vol": "50000",
             "whol_smtn_ntby_qty": "20000", "whol_smtn_ntby_tr_pbmn": "1400000000"}]
    fake = FakeTransport(response=_resp({"output": rows}))
    points = _client(fake).ticker("005930").program_trades()
    assert all(isinstance(p, ProgramTradePoint) for p in points)
    p = points[0]
    assert p.symbol == "005930"
    assert p.price == Decimal(70000)
    assert p.change == Decimal(500)                       # sign 2 -> 상승
    assert p.buy_volume == 50000
    assert p.sell_volume == 30000
    assert p.net_volume == 20000
    assert p.net_amount == Decimal(1400000000)
    assert p.timestamp.hour == 10
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/program-trade-by-stock"
    assert call["tr_id"] == "FHPPG04650101"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "J"
    assert call["params"]["FID_INPUT_ISCD"] == "005930"


def test_program_trades_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").program_trades()


def test_investor_estimate_maps_estimate():
    rows = [{"bsop_hour_gb": "0930", "frgn_fake_ntby_qty": "12000",
             "orgn_fake_ntby_qty": "-3000", "sum_fake_ntby_qty": "9000"}]
    fake = FakeTransport(response=_resp({"output2": rows}))
    ests = _client(fake).ticker("005930").investor_estimate()
    assert all(isinstance(e, InvestorEstimate) for e in ests)
    e = ests[0]
    assert e.foreign_net == 12000
    assert e.institutional_net == -3000
    assert e.total_net == 9000
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/investor-trend-estimate"
    assert call["tr_id"] == "HHPTJ04160200"
    assert call["params"]["MKSC_SHRN_ISCD"] == "005930"


def test_investor_estimate_missing_output2_fails_closed():
    fake = FakeTransport(response=_resp({"output": []}))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").investor_estimate()


def test_investor_estimate_rejected_for_overseas_ticker():
    fake = FakeTransport(response=_resp({"output2": []}))
    with pytest.raises(Exception):  # noqa: B017 -- 해외 티커는 국내 전용 verb 거부
        _client(fake).ticker("AAPL", exchange="NAS").investor_estimate()
