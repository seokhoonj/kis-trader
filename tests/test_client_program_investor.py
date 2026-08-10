"""per-ticker 시세분석 -- kis.domestic.stock(code).program_trades() / .investor_estimate().

프로그램매매 흐름(TR FHPPG04650101, output)·투자자 순매수 추정(TR HHPTJ04160200, output2)의
TR·URL·시장구분·필드 매핑(순매수 수량/금액·외인/기관 추정), 시각 파싱, fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import (
    DailyProgramTradePoint,
    InvestorEstimate,
    KISClient,
    ProgramTradePoint,
)
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
    points = _client(fake).domestic.stock("005930").program_trades()
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
        _client(fake).domestic.stock("005930").program_trades()


def test_daily_program_trades_maps_and_routes():
    row = {"stck_bsop_date": "20240510", "stck_clpr": "71500", "prdy_vrss": "500",
           "prdy_vrss_sign": "2", "prdy_ctrt": "0.70", "acml_vol": "1000000",
           "acml_tr_pbmn": "71000", "whol_smtn_seln_vol": "100", "whol_smtn_shnu_vol": "130",
           "whol_smtn_ntby_qty": "30", "whol_smtn_seln_tr_pbmn": "10",
           "whol_smtn_shnu_tr_pbmn": "13", "whol_smtn_ntby_tr_pbmn": "3",
           "whol_ntby_vol_icdc": "5", "whol_ntby_tr_pbmn_icdc2": "1"}
    fake = FakeTransport(response=_resp({"output": [row]}))
    points = _client(fake).domestic.stock("005930").daily_program_trades(as_of="20240510")
    assert isinstance(points[0], DailyProgramTradePoint)
    assert points[0].net_volume == 30
    assert points[0].net_amount_change == Decimal(1)
    assert fake.calls[0] == {
        "path": "/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
        "tr_id": "FHPPG04650201",
        "params": {"FID_INPUT_ISCD": "005930", "FID_INPUT_DATE_1": "20240510"},
    }


def test_investor_estimate_maps_estimate_and_input_time():
    # bsop_hour_gb 는 시각이 아니라 입력구분 코드다: 1=09:30, 2=10:00, 3=11:20, 4=13:20, 5=14:30.
    rows = [{"bsop_hour_gb": "1", "frgn_fake_ntby_qty": "12000",
             "orgn_fake_ntby_qty": "-3000", "sum_fake_ntby_qty": "9000"},
            {"bsop_hour_gb": "5", "frgn_fake_ntby_qty": "-30000",
             "orgn_fake_ntby_qty": "121000", "sum_fake_ntby_qty": "91000"}]
    fake = FakeTransport(response=_resp({"output2": rows}))
    ests = _client(fake).domestic.stock("005930").investor_estimate()
    assert all(isinstance(e, InvestorEstimate) for e in ests)
    first = ests[0]
    assert first.foreign_net == 12000
    assert first.institutional_net == -3000            # 가집계 순매수는 pre-signed(부호복원 안 함)
    assert first.total_net == 9000
    assert (first.timestamp.hour, first.timestamp.minute) == (9, 30)   # 코드 1 -> 09:30
    assert (ests[1].timestamp.hour, ests[1].timestamp.minute) == (14, 30)  # 코드 5 -> 14:30
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/investor-trend-estimate"
    assert call["tr_id"] == "HHPTJ04160200"
    assert call["params"]["MKSC_SHRN_ISCD"] == "005930"


def test_investor_estimate_unknown_input_code_fails_closed():
    rows = [{"bsop_hour_gb": "9", "frgn_fake_ntby_qty": "0",
             "orgn_fake_ntby_qty": "0", "sum_fake_ntby_qty": "0"}]
    fake = FakeTransport(response=_resp({"output2": rows}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").investor_estimate()


def test_investor_estimate_missing_output2_fails_closed():
    fake = FakeTransport(response=_resp({"output": []}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").investor_estimate()


def test_investor_estimate_absent_on_overseas_stock():
    # investor_estimate 는 국내 전용 -- 해외 핸들엔 아예 없다(자산군 분리).
    handle = _client(FakeTransport(response=_resp({"output2": []}))).overseas.stock("AAPL", exchange="NAS")
    assert not hasattr(handle, "investor_estimate")
