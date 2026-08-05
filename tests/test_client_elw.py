"""ELW 핸들 -- kis.elw(code) 고유 지표.

민감도(그릭스) 추이: 일별/체결별 라우팅(TR·URL·시장구분 W), output 배열 파싱(그릭스·이론가·
전일대비 부호 복원), 시간축(영업일자 vs 체결시각), optional 그릭스(None), fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import Elw, ElwSensitivityPoint, KisClient
from kis_openapi.errors import KisError, KisUsageError
from kis_openapi.transport import RawResponse

_DAILY = "/uapi/elw/v1/quotations/sensitivity-trend-daily"
_CCNL = "/uapi/elw/v1/quotations/sensitivity-trend-ccnl"


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def _client(transport):
    return KisClient(app_key="k", app_secret="s", transport=transport)


def _daily_row(bsop="20240507", price="25", vrss="20", sign="5", ctrt="44.44",
               thpr="20.39", delta="-0.4034", gama="0.0000", theta="0.5843",
               vega="0.9954", rho="-0.3529"):
    return {"stck_bsop_date": bsop, "elw_prpr": price, "prdy_vrss": vrss,
            "prdy_vrss_sign": sign, "prdy_ctrt": ctrt, "hts_thpr": thpr,
            "delta_val": delta, "gama": gama, "theta": theta, "vega": vega, "rho": rho}


def test_elw_accessor_returns_handle():
    handle = _client(FakeTransport(response=_resp([]))).elw("58J297")
    assert isinstance(handle, Elw)
    assert handle.code == "58J297"


def test_sensitivity_trend_daily_maps_greeks_and_market():
    fake = FakeTransport(response=_resp([_daily_row()]))
    points = _client(fake).elw("58J438").sensitivity_trend("day")
    assert all(isinstance(p, ElwSensitivityPoint) for p in points)
    point = points[0]
    assert point.code == "58J438"
    assert point.price == Decimal(25)
    assert point.change == Decimal(-20)                  # sign 5(하락) -> 음수
    assert point.change_percent == Decimal("-44.44")
    assert point.theoretical_price == Decimal("20.39")
    assert point.delta == Decimal("-0.4034")               # 풋이라 델타 음수
    assert point.gamma == Decimal("0.0000")
    assert point.theta == Decimal("0.5843")
    assert point.vega == Decimal("0.9954")
    assert point.rho == Decimal("-0.3529")
    assert f"{point.timestamp:%Y%m%d}" == "20240507"       # 영업일자
    call = fake.calls[0]
    assert call["path"] == _DAILY
    assert call["tr_id"] == "FHPEW02830200"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "W"
    assert call["params"]["FID_INPUT_ISCD"] == "58J438"


def test_sensitivity_trend_default_is_daily():
    fake = FakeTransport(response=_resp([_daily_row()]))
    _client(fake).elw("58J438").sensitivity_trend()
    assert fake.calls[0]["tr_id"] == "FHPEW02830200"


def test_sensitivity_trend_ccnl_uses_execution_time():
    rows = [{"stck_cntg_hour": "101530", "elw_prpr": "25", "prdy_vrss": "20",
             "prdy_vrss_sign": "2", "prdy_ctrt": "44.44", "hts_thpr": "20.39",
             "delta_val": "0.4034", "gama": "0.0", "theta": "0.5", "vega": "0.9",
             "rho": "0.3"}]
    fake = FakeTransport(response=_resp(rows))
    points = _client(fake).elw("58J297").sensitivity_trend("trade")
    assert fake.calls[0]["path"] == _CCNL
    assert fake.calls[0]["tr_id"] == "FHPEW02830100"
    assert points[0].change == Decimal(20)               # sign 2(상승) -> 양수
    assert points[0].timestamp.hour == 10 and points[0].timestamp.minute == 15


def test_sensitivity_trend_optional_greek_none():
    fake = FakeTransport(response=_resp([_daily_row(vega="")]))
    point = _client(fake).elw("58J438").sensitivity_trend("day")[0]
    assert point.vega is None
    assert point.delta == Decimal("-0.4034")               # 나머지는 여전히 파싱


def test_sensitivity_trend_skips_empty_rows():
    fake = FakeTransport(response=_resp([_daily_row(), {"stck_bsop_date": ""}]))
    assert len(_client(fake).elw("58J438").sensitivity_trend("day")) == 1


def test_sensitivity_trend_rejects_unsupported_interval():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KisUsageError):
        _client(fake).elw("58J438").sensitivity_trend("minute")


def test_sensitivity_trend_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KisError):
        _client(fake).elw("58J438").sensitivity_trend("day")


def test_sensitivity_trend_bad_value_fails_closed():
    fake = FakeTransport(response=_resp([_daily_row(price="n/a")]))
    with pytest.raises(KisError):
        _client(fake).elw("58J438").sensitivity_trend("day")
