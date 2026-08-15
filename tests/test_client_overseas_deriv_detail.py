"""해외파생 계약 명세 -- kis.overseas.futures/option(srs_cd).detail()."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, OverseasDerivativeDetail
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


def _out(**over):
    o = {"exch_cd": "CME", "crc_cd": "USD", "clas_cd": "IDX", "tick_sz": "0.25",
         "tick_val": "12.50", "ctrt_size": "50", "trst_mgn": "13200", "disp_digit": "2",
         "trd_fr_date": "20240101", "expr_date": "20251219", "trd_to_date": "20251218",
         "remn_cnt": "41", "stl_tp": "1", "stat_tp": "1"}
    o.update(over)
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": o})


def test_overseas_derivative_detail_futures():
    fake = FakeTransport(response=_out())
    d = _client(fake).overseas.futures("ESZ25").detail()
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
    _client(fake).overseas.option("ESZ25 C5000").detail()
    assert fake.calls[0]["path"] == "/uapi/overseas-futureoption/v1/quotations/opt-detail"
    assert fake.calls[0]["tr_id"] == "HHDFO55010100"


def test_overseas_derivative_detail_optional_none():
    fake = FakeTransport(response=_out(tick_sz="", ctrt_size="", expr_date=""))
    d = _client(fake).overseas.futures("ESZ25").detail()
    assert d.tick_size is None
    assert d.contract_size is None
    assert d.expiry_date is None


def test_overseas_derivative_detail_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).overseas.futures("ESZ25").detail()


def _detail_row():
    return _out().body["output1"]


def test_overseas_futures_details_maps_batch_by_request_order():
    fake = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="MCA00000", msg1="정상",
        body={"output2": [_detail_row(), _detail_row()]},
    ))
    details = _client(fake).overseas.futures_details(["6AM24", "10YK24"])
    assert [detail.symbol for detail in details] == ["6AM24", "10YK24"]
    assert all(isinstance(detail, OverseasDerivativeDetail) for detail in details)
    assert details[0].exchange == "CME"
    assert fake.calls[0] == {
        "path": "/uapi/overseas-futureoption/v1/quotations/search-contract-detail",
        "tr_id": "HHDFC55200000",
        "params": {"QRY_CNT": "2", "SRS_CD_01": "6AM24", "SRS_CD_02": "10YK24"},
    }


def test_overseas_option_details_routes_option_batch():
    fake = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": [_detail_row()]}
    ))
    details = _client(fake).overseas.option_details(["OESU24 C5600"])
    assert details[0].symbol == "OESU24 C5600"
    assert fake.calls[0]["path"].endswith("/search-opt-detail")
    assert fake.calls[0]["tr_id"] == "HHDFO55200000"


@pytest.mark.parametrize(
    ("method", "symbols"),
    [
        ("futures_details", []),
        ("futures_details", ["x"] * 33),
        ("option_details", ["x"] * 31),
        ("option_details", [""]),
    ],
)
def test_overseas_derivative_details_rejects_invalid_requests(method, symbols):
    fake = FakeTransport(response=None)
    with pytest.raises(KISUsageError):
        getattr(_client(fake).overseas, method)(symbols)
    assert fake.calls == []


def test_overseas_derivative_details_rejects_demo_before_transport():
    fake = FakeTransport(response=None)
    client = KISClient(app_key="k", app_secret="s", transport=fake, profile="paper")
    with pytest.raises(KISUsageError):
        client.overseas.futures_details(["6AM24"])
    assert fake.calls == []


def test_overseas_derivative_details_requires_matching_response_count():
    fake = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="X", msg1="ok", body={"output2": [_detail_row()]}
    ))
    with pytest.raises(KISError):
        _client(fake).overseas.futures_details(["6AM24", "10YK24"])


def test_overseas_derivative_details_requires_output2():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).overseas.futures_details(["6AM24"])
