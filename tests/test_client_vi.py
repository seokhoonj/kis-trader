"""VI 현황 -- kis.domestic.market.vi_events(). 필드는 원장 응답예시 실값 기준."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, VIEvent
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


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": rows})


def test_vi_events_maps_ledger_values():
    # 원장 응답예시 실값.
    rows = [{"hts_kor_isnm": "KODEX Fn멀티팩터", "mksc_shrn_iscd": "337120", "vi_cls_code": "N",
             "bsop_date": "20240126", "cntg_vi_hour": "174012", "vi_cncl_hour": "174212",
             "vi_kind_code": "2", "vi_prc": "12135", "vi_stnd_prc": "0", "vi_dprt": "0.00",
             "vi_dmc_stnd_prc": "13275", "vi_dmc_dprt": "-8.59", "vi_count": "2"}]
    fake = FakeTransport(response=_resp(rows))
    events = _client(fake).domestic.market.vi_events(as_of="20240126")
    assert isinstance(events[0], VIEvent)
    e = events[0]
    assert e.symbol == "337120"
    assert e.name == "KODEX Fn멀티팩터"
    assert e.trigger_price == Decimal(12135)
    assert e.daily_trigger_count == 2
    assert e.vi_class == "N"
    assert e.vi_kind == "2"
    assert f"{e.triggered_at:%Y%m%d %H%M%S}" == "20240126 174012"
    assert f"{e.released_at:%Y%m%d %H%M%S}" == "20240126 174212"
    assert e._raw["vi_dmc_dprt"] == "-8.59"              # 동적 괴리율은 _raw
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/inquire-vi-status"
    assert call["tr_id"] == "FHPST01390000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20139"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240126"


def test_vi_events_unreleased_is_none():
    rows = [{"hts_kor_isnm": "x", "mksc_shrn_iscd": "005930", "vi_cls_code": "D",
             "bsop_date": "20240126", "cntg_vi_hour": "100000", "vi_cncl_hour": "000000",
             "vi_kind_code": "1", "vi_prc": "1000", "vi_stnd_prc": "", "vi_dprt": "",
             "vi_count": "1"}]
    fake = FakeTransport(response=_resp(rows))
    e = _client(fake).domestic.market.vi_events(as_of="20240126")[0]
    assert e.released_at is None                          # 0-채움 해제시각 -> 미해제
    assert e.base_price is None
    assert e.disparity_percent is None


def test_vi_events_skips_empty_and_missing_output():
    fake = FakeTransport(response=_resp([{"mksc_shrn_iscd": ""}]))
    assert _client(fake).domestic.market.vi_events(as_of="20240126") == []
    fake2 = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake2).domestic.market.vi_events()


def test_vi_events_bad_value_fails_closed():
    rows = [{"hts_kor_isnm": "x", "mksc_shrn_iscd": "005930", "bsop_date": "20240126",
             "cntg_vi_hour": "100000", "vi_cncl_hour": "100200", "vi_prc": "n/a",
             "vi_count": "1", "vi_cls_code": "N", "vi_kind_code": "1"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).domestic.market.vi_events(as_of="20240126")
