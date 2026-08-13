"""시간대별 프로그램매매 -- kis.domestic.market.program_flow(). 필드는 원장 응답예시 실값."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, ProgramFlowPoint
from kis_trader.errors import KISError, KISUsageError
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


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": rows})


def test_program_flow_maps_amounts():
    # 값은 smtn(대금). smtm 은 _rate 필드에만.
    rows = [{"bsop_hour": "170000", "arbt_smtn_ntby_tr_pbmn": "276905",
             "arbt_smtm_ntby_tr_pbmn_rate": "2.53", "nabt_smtn_ntby_tr_pbmn": "859384",
             "whol_smtn_ntby_tr_pbmn": "1136289"}]
    fake = FakeTransport(response=_resp(rows))
    pts = _client(fake).domestic.market.program_flow(market="KOSPI")
    assert isinstance(pts[0], ProgramFlowPoint)
    p = pts[0]
    assert p.arbitrage_net_amount == Decimal(276905)     # smtn (NOT the _rate field)
    assert p.nonarbitrage_net_amount == Decimal(859384)
    assert p.total_net_amount == Decimal(1136289)
    assert p.timestamp.hour == 17
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/comp-program-trade-today"
    assert call["tr_id"] == "FHPPG04600101"
    assert call["params"]["FID_MRKT_CLS_CODE"] == "K"


def test_program_flow_kosdaq_and_bad_market():
    fake = FakeTransport(response=_resp([]))
    _client(fake).domestic.market.program_flow(market="KOSDAQ")
    assert fake.calls[0]["params"]["FID_MRKT_CLS_CODE"] == "Q"
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.program_flow(market="US")


def test_program_flow_missing_output_and_bad_value():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.market.program_flow()
    fake2 = FakeTransport(response=_resp([{"bsop_hour": "170000",
        "arbt_smtn_ntby_tr_pbmn": "n/a", "nabt_smtn_ntby_tr_pbmn": "1",
        "whol_smtn_ntby_tr_pbmn": "1"}]))
    with pytest.raises(KISError):
        _client(fake2).domestic.market.program_flow()
