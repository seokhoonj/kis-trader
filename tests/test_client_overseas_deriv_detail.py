"""해외파생 계약 명세 -- kis.overseas_futures/option(srs_cd).detail()."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, OverseasDerivativeDetail
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


def _out(**over):
    o = {"exch_cd": "CME", "crc_cd": "USD", "clas_cd": "IDX", "tick_sz": "0.25",
         "tick_val": "12.50", "ctrt_size": "50", "trst_mgn": "13200", "disp_digit": "2",
         "trd_fr_date": "20240101", "expr_date": "20251219", "trd_to_date": "20251218",
         "remn_cnt": "41", "stl_tp": "1", "stat_tp": "1"}
    o.update(over)
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": o})


def test_overseas_derivative_detail_futures():
    fake = FakeTransport(response=_out())
    d = _client(fake).overseas_futures("ESZ25").detail()
    assert isinstance(d, OverseasDerivativeDetail)
    assert d.symbol == "ESZ25"
    assert d.exchange == "CME"
    assert d.currency == "USD"
    assert d.tick_size == Decimal("0.25")
    assert d.tick_value == Decimal("12.50")
    assert d.contract_size == Decimal(50)
    assert d.price_digits == 2
    assert d.remaining_days == 41
    assert f"{d.expiry_date:%Y%m%d}" == "20251219"
    call = fake.calls[0]
    assert call["path"] == "/uapi/overseas-futureoption/v1/quotations/stock-detail"
    assert call["tr_id"] == "HHDFC55010100"
    assert call["params"]["SRS_CD"] == "ESZ25"


def test_overseas_derivative_detail_option_routes():
    fake = FakeTransport(response=_out())
    _client(fake).overseas_option("ESZ25 C5000").detail()
    assert fake.calls[0]["path"] == "/uapi/overseas-futureoption/v1/quotations/opt-detail"
    assert fake.calls[0]["tr_id"] == "HHDFO55010100"


def test_overseas_derivative_detail_optional_none():
    fake = FakeTransport(response=_out(tick_sz="", ctrt_size="", expr_date=""))
    d = _client(fake).overseas_futures("ESZ25").detail()
    assert d.tick_size is None
    assert d.contract_size is None
    assert d.expiry_date is None


def test_overseas_derivative_detail_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).overseas_futures("ESZ25").detail()
