"""ETF/ETN 고유 정보 -- kis.ticker(code).nav().

ETF 는 종목처럼 거래되므로 시세/주문은 일반 verb 로 하고, NAV/괴리율/추적오차만 별도. etfetn 세그먼트
경로, NAV 전일대비 부호 복원, fail-closed 파싱을 가짜 전송으로 검증.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import EtfNav, KisClient
from kis_openapi.errors import KisError, KisUsageError
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


_ETF_COMPONENTS = "/uapi/etfetn/v1/quotations/inquire-component-stock-price"


def _component_row(symbol="005930", name="삼성전자", price="72700", change="400", sign="2",
                   pct="0.55", weight="28.9", valuation="1210000000"):
    return {"stck_shrn_iscd": symbol, "hts_kor_isnm": name, "stck_prpr": price,
            "prdy_vrss": change, "prdy_vrss_sign": sign, "prdy_ctrt": pct,
            "etf_cnfg_issu_rlim": weight, "etf_vltn_amt": valuation}


def _components_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {"stck_prpr": "37195"}, "output2": rows})


def test_components_maps_fields_and_params():
    from kis_openapi import EtfComponent
    fake = FakeTransport(response=_components_resp([_component_row(),
                                                    _component_row(symbol="000660", name="SK하이닉스")]))
    comps = _client(fake).ticker("069500").components()
    assert [c.symbol for c in comps] == ["005930", "000660"]
    first = comps[0]
    assert isinstance(first, EtfComponent)
    assert first.name == "삼성전자"
    assert first.price == Decimal(72700)
    assert first.weight == Decimal("28.9")
    assert first.valuation == Decimal(1210000000)
    call = fake.calls[0]
    assert call["path"] == _ETF_COMPONENTS
    assert call["tr_id"] == "FHKST121600C0"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "11216"
    assert call["params"]["FID_INPUT_ISCD"] == "069500"


def test_components_negative_change_sign_restored():
    fake = FakeTransport(response=_components_resp([_component_row(change="300", sign="5")]))
    comps = _client(fake).ticker("069500").components()
    assert comps[0].change == Decimal(-300)                      # 하락 -> 음수


def test_components_missing_output2_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output1": {"stck_prpr": "1"}}))
    with pytest.raises(KisError):
        _client(fake).ticker("069500").components()


_ETF_NAV_HISTORY = "/uapi/etfetn/v1/quotations/nav-comparison-daily-trend"


def _nav_hist_row(date_text, close, nav, nav_change, sign, nav_pct, premium):
    return {"stck_bsop_date": date_text, "stck_clpr": close, "nav": nav,
            "nav_prdy_vrss": nav_change, "nav_prdy_vrss_sign": sign, "nav_prdy_ctrt": nav_pct,
            "dprt": premium}


def _nav_hist_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def test_nav_history_maps_fields_sorted_and_params():
    from datetime import date as _date

    from kis_openapi import EtfNavHistoryPoint
    fake = FakeTransport(response=_nav_hist_resp([
        _nav_hist_row("20240104", "36090", "36110", "95", "2", "0.26", "-0.06"),
        _nav_hist_row("20240103", "35980", "36015", "40", "2", "0.11", "-0.10"),
    ]))
    points = _client(fake).ticker("069500").nav_history(start="20240103", end="20240104")
    assert [p.date for p in points] == [_date(2024, 1, 3), _date(2024, 1, 4)]   # 오름차순
    assert all(isinstance(p, EtfNavHistoryPoint) for p in points)
    assert points[-1].close == Decimal(36090)
    assert points[-1].nav == Decimal(36110)
    assert points[-1].premium == Decimal("-0.06")
    call = fake.calls[0]
    assert call["path"] == _ETF_NAV_HISTORY
    assert call["tr_id"] == "FHPST02440200"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240103"
    assert call["params"]["FID_INPUT_DATE_2"] == "20240104"


def test_nav_history_negative_nav_change_and_date_objects():
    from datetime import date as _date
    fake = FakeTransport(response=_nav_hist_resp([
        _nav_hist_row("20240104", "36090", "36110", "95", "5", "0.26", "-0.06")]))
    points = _client(fake).ticker("069500").nav_history(
        start=_date(2024, 1, 1), end=_date(2024, 1, 4)
    )
    assert points[0].nav_change == Decimal(-95)                  # 하락 -> 음수
    assert fake.calls[0]["params"]["FID_INPUT_DATE_1"] == "20240101"


def test_nav_history_start_after_end_raises():
    fake = FakeTransport(response=_nav_hist_resp([]))
    with pytest.raises(KisUsageError):
        _client(fake).ticker("069500").nav_history(start="20240104", end="20240103")


def test_nav_history_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KisError):
        _client(fake).ticker("069500").nav_history(start="20240101", end="20240104")
