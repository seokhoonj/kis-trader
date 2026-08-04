"""ETF/ETN 고유 정보 -- kis.ticker(code).nav().

ETF 는 종목처럼 거래되므로 시세/주문은 일반 verb 로 하고, NAV/괴리율/추적오차만 별도. etfetn 세그먼트
경로, NAV 전일대비 부호 복원, fail-closed 파싱을 가짜 전송으로 검증.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import EtfNav, KisClient
from kis_openapi.errors import KisError
from kis_openapi.transport import RawResponse

_ETF_NAV = "/uapi/etfetn/v1/quotations/inquire-price"


def _output(*, nav="36110.50", nav_change="95.20", nav_sign="2", nav_pct="0.26",
            prev_nav="36015.30", premium="-0.06", trc_err="0.03", net_assets="4200000000000"):
    return {"stck_prpr": "36090", "prdy_vrss_sign": "2", "prdy_vrss": "110", "prdy_ctrt": "0.31",
            "acml_vol": "1200000", "nav": nav, "nav_prdy_vrss": nav_change,
            "nav_prdy_vrss_sign": nav_sign, "nav_prdy_ctrt": nav_pct, "prdy_last_nav": prev_nav,
            "dprt": premium, "trc_errt": trc_err, "etf_ntas_ttam": net_assets}


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _resp(output):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": output})


def _client(transport):
    return KisClient(app_key="k", app_secret="s", transport=transport)


def test_nav_maps_fields_and_params():
    fake = FakeTransport(response=_resp(_output()))
    nav = _client(fake).ticker("069500").nav()
    assert isinstance(nav, EtfNav)
    assert nav.symbol == "069500"
    assert nav.nav == Decimal("36110.50")
    assert nav.nav_change == Decimal("95.20")
    assert nav.nav_change_percent == Decimal("0.26")
    assert nav.previous_nav == Decimal("36015.30")
    assert nav.premium == Decimal("-0.06")
    assert nav.tracking_error == Decimal("0.03")
    assert nav.net_assets == Decimal(4200000000000)
    call = fake.calls[0]
    assert call["path"] == _ETF_NAV
    assert call["tr_id"] == "FHPST02400000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "J"
    assert call["params"]["FID_INPUT_ISCD"] == "069500"


def test_nav_negative_change_sign_restored():
    fake = FakeTransport(response=_resp(_output(nav_change="80.00", nav_sign="5", nav_pct="0.22")))
    nav = _client(fake).ticker("069500").nav()
    assert nav.nav_change == Decimal("-80.00")                   # 하락 -> 음수
    assert nav.nav_change_percent == Decimal("-0.22")


def test_nav_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KisError):
        _client(fake).ticker("069500").nav()


def test_nav_error_response_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="1", msg_cd="X", msg1="실패", body={}))
    with pytest.raises(KisError):
        _client(fake).ticker("069500").nav()


def test_nav_bad_value_fails_closed():
    fake = FakeTransport(response=_resp(_output(nav="n/a")))
    with pytest.raises(KisError):
        _client(fake).ticker("069500").nav()
