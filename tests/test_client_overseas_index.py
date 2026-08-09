"""해외 지수/환율/국채/금선물 기간봉 핸들 검증."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import Bar, KISClient, OverseasIndex
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_BARS_PATH = "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"


def _row(date, open_, high, low, close, volume):
    return {
        "stck_bsop_date": date, "ovrs_nmix_oprc": open_, "ovrs_nmix_hgpr": high,
        "ovrs_nmix_lwpr": low, "ovrs_nmix_prpr": close, "acml_vol": volume,
    }


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": rows})


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "params": params})
        return self.response


def _client(response):
    fake = FakeTransport(response)
    return KISClient(app_key="k", app_secret="s", transport=fake), fake


def test_overseas_index_bars_routes_and_parses_ascending():
    client, fake = _client(_resp([
        _row("20220613", "3060.1", "3090.2", "3050.3", "3080.4", "500"),
        _row("20220401", "3010.1", "3040.2", "3000.3", "3030.4", "400"),
    ]))
    handle = client.overseas.index(".DJI")
    bars = handle.bars("1d", start="20220401", end="20220613")
    assert isinstance(handle, OverseasIndex)
    assert all(isinstance(bar, Bar) for bar in bars)
    assert [f"{bar.timestamp:%Y%m%d}" for bar in bars] == ["20220401", "20220613"]
    assert bars[0].symbol == ".DJI"
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume) == (
        Decimal("3010.1"), Decimal("3040.2"), Decimal("3000.3"), Decimal("3030.4"), 400,
    )
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == _BARS_PATH
    assert call["tr_id"] == "FHKST03030100"
    assert call["params"] == {
        "FID_COND_MRKT_DIV_CODE": "N", "FID_INPUT_ISCD": ".DJI",
        "FID_PERIOD_DIV_CODE": "D", "FID_INPUT_DATE_1": "20220401",
        "FID_INPUT_DATE_2": "20220613",
    }


def test_overseas_index_kind_and_period_mappings():
    client, fake = _client(_resp([_row("20240105", "1", "1", "1", "1", "1")]))
    client.overseas.index("USD", kind="fx").bars(start="20240105")
    assert fake.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"] == "X"
    for interval, period in (("1wk", "W"), ("1mo", "M")):
        client, fake = _client(_resp([_row("20240105", "1", "1", "1", "1", "1")]))
        client.overseas.index(".DJI").bars(interval, start="20240105")
        assert fake.calls[0]["params"]["FID_PERIOD_DIV_CODE"] == period


def test_overseas_index_rejects_unknown_kind():
    client, _ = _client(_resp([]))
    with pytest.raises(KISUsageError):
        client.overseas.index(".DJI", kind="commodity")


@pytest.mark.parametrize("kwargs, error", [
    ({}, KISUsageError),
    ({"start": "20240102", "end": "20240101"}, KISUsageError),
    ({"start": "20240101", "max_bars": 0}, KISUsageError),
])
def test_overseas_index_bars_guards(kwargs, error):
    client, _ = _client(_resp([]))
    with pytest.raises(error):
        client.overseas.index(".DJI").bars(**kwargs)


@pytest.mark.parametrize("output2", [None, {}, "bad"])
def test_overseas_index_bars_missing_or_non_list_output_fails_closed(output2):
    body = {} if output2 is None else {"output2": output2}
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body=body)
    client, _ = _client(response)
    with pytest.raises(KISError):
        client.overseas.index(".DJI").bars(start="20240101")


def test_overseas_index_bars_skips_blank_rows_and_rejects_bad_numeric():
    client, _ = _client(_resp([
        _row("", "1", "1", "1", "1", "1"),
        _row("20240101", "1", "1", "1", "", "1"),
    ]))
    assert client.overseas.index(".DJI").bars(start="20240101") == []

    client, _ = _client(_resp([_row("20240101", "bad", "1", "1", "1", "1")]))
    with pytest.raises(KISError):
        client.overseas.index(".DJI").bars(start="20240101")


def _minute_row(day, time, open_, high, low, close, volume):
    return {
        "stck_bsop_date": day,
        "stck_cntg_hour": time,
        "optn_oprc": open_,
        "optn_hgpr": high,
        "optn_lwpr": low,
        "optn_prpr": close,
        "cntg_vol": volume,
    }


def test_overseas_index_minute_bars_maps_filters_and_limits():
    response = RawResponse(
        rt_cd="0",
        msg_cd="MCA00000",
        msg1="정상",
        body={
            "output1": {"stck_shrn_iscd": "SPX"},
            "output2": [
                _minute_row("20240223", "101000", "5000", "5002", "4999", "5001", "30"),
                _minute_row("20240223", "100900", "4998", "5001", "4997", "5000", "25"),
                _minute_row("20240222", "160000", "4990", "4999", "4988", "4998", "50"),
            ],
        },
    )
    client, fake = _client(response)
    bars = client.overseas.index("SPX").bars(
        "1m", start="20240223", end="2024-02-23", max_bars=1
    )

    assert len(bars) == 1
    assert f"{bars[0].timestamp:%Y%m%d%H%M%S}" == "20240223101000"
    assert bars[0].close == Decimal(5001)
    assert bars[0].volume == 30
    assert fake.calls[0] == {
        "method": "GET",
        "path": "/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice",
        "tr_id": "FHKST03030200",
        "params": {
            "FID_COND_MRKT_DIV_CODE": "N",
            "FID_INPUT_ISCD": "SPX",
            "FID_HOUR_CLS_CODE": "0",
            "FID_PW_DATA_INCU_YN": "Y",
        },
    }


def test_overseas_index_minute_bars_rejects_unsupported_kind_before_transport():
    client, fake = _client(_resp([]))
    with pytest.raises(KISUsageError):
        client.overseas.index("US10Y", kind="bond").bars("1m")
    assert fake.calls == []


@pytest.mark.parametrize("body", [{}, {"output1": {}}, {"output1": {}, "output2": {}}])
def test_overseas_index_minute_bars_requires_both_blocks(body):
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body=body)
    client, _ = _client(response)
    with pytest.raises(KISError):
        client.overseas.index("SPX").bars("1m")
