"""애널리스트 투자의견 -- kis.domestic.stock(code).analyst_opinions()."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import AnalystOpinion, KISClient
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


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": rows})


def test_analyst_opinions_maps():
    rows = [{"stck_bsop_date": "20240510", "invt_opnn": "매수", "rgbf_invt_opnn": "중립",
             "hts_goal_prc": "90000", "stck_prdy_clpr": "75000", "dprt": "20.0"}]
    fake = FakeTransport(response=_resp(rows))
    ops = _client(fake).domestic.stock("005930").analyst_opinions(start="20240101", end="20240513")
    assert isinstance(ops[0], AnalystOpinion)
    assert ops[0].opinion == "매수"
    assert ops[0].previous_opinion == "중립"
    assert ops[0].target_price == Decimal(90000)
    assert ops[0].disparity_percent == Decimal("20.0")
    assert f"{ops[0].timestamp:%Y%m%d}" == "20240510"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/invest-opinion"
    assert call["tr_id"] == "FHKST663300C0"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "16633"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240101"


def test_analyst_opinions_default_window():
    fake = FakeTransport(response=_resp([]))
    _client(fake).domestic.stock("005930").analyst_opinions(end="20240131")
    call = fake.calls[0]
    assert call["params"]["FID_INPUT_DATE_1"] == "20240101"       # 30일 전
    assert call["params"]["FID_INPUT_DATE_2"] == "20240131"


def test_analyst_opinions_optional_target_none():
    rows = [{"stck_bsop_date": "20240510", "invt_opnn": "매수", "rgbf_invt_opnn": "",
             "hts_goal_prc": "", "stck_prdy_clpr": "75000", "dprt": ""}]
    fake = FakeTransport(response=_resp(rows))
    op = _client(fake).domestic.stock("005930").analyst_opinions()[0]
    assert op.target_price is None
    assert op.disparity_percent is None


def test_analyst_opinions_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").analyst_opinions()


def test_analyst_opinions_bad_value_fails_closed():
    rows = [{"stck_bsop_date": "20240510", "invt_opnn": "매수", "rgbf_invt_opnn": "중립",
             "hts_goal_prc": "n/a"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").analyst_opinions()
