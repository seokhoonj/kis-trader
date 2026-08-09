"""해외 선물/옵션 상품군별 장운영시간 조회의 라우팅·연속조회·파싱·실패 경계를 검증한다."""

from __future__ import annotations

import threading
from datetime import time

import pytest

from kis_openapi import KISClient, OverseasDerivativeMarketHours
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_PATH = "/uapi/overseas-futureoption/v1/quotations/market-time"


class FakeTransport:
    def __init__(self, *, responses):
        self.responses = iter(responses)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append(
                {"method": method, "path": path, "tr_id": tr_id, "params": params}
            )
        return next(self.responses)


def _client(transport, *, environment="real"):
    return KISClient(
        app_key="k", app_secret="s", environment=environment, transport=transport
    )


def _row(**over):
    row = {
        "fm_pdgr_cd": "ES",
        "fm_pdgr_name": "E-MINI S&P 500",
        "fm_excg_cd": "CME",
        "fm_excg_name": "CHICAGO MERCANTILE EXCHANGE",
        "fuop_dvsn_name": "선물",
        "fm_clas_cd": "003",
        "fm_clas_name": "지수",
        "am_mkmn_strt_tmd": "093000",
        "am_mkmn_end_tmd": "160000",
        "pm_mkmn_strt_tmd": "180000",
        "pm_mkmn_end_tmd": "235959",
        "mkmn_nxdy_strt_tmd": "000000",
        "mkmn_nxdy_end_tmd": "050000",
        "base_mket_strt_tmd": "093000",
        "base_mket_end_tmd": "161500",
    }
    row.update(over)
    return row


def _response(rows, **extra):
    body = {"output": rows, **extra}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


def test_market_hours_single_page_routes_defaults_and_parses():
    fake = FakeTransport(responses=[_response([_row(pm_mkmn_end_tmd="")])])
    hours = _client(fake).overseas.derivatives_market_hours()

    assert len(hours) == 1
    item = hours[0]
    assert isinstance(item, OverseasDerivativeMarketHours)
    assert item.product_group_code == "ES"
    assert item.product_group_name == "E-MINI S&P 500"
    assert item.exchange_code == "CME"
    assert item.exchange_name == "CHICAGO MERCANTILE EXCHANGE"
    assert item.kind == "선물"
    assert item.class_code == "003"
    assert item.class_name == "지수"
    assert item.am_open == time(9, 30)
    assert item.pm_close is None
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == _PATH
    assert call["tr_id"] == "OTFM2229R"
    assert call["params"] == {
        "FM_PDGR_CD": "",
        "FM_CLAS_CD": "",
        "FM_EXCG_CD": "",
        "OPT_YN": "%",
        "CTX_AREA_NK200": "",
        "CTX_AREA_FK200": "",
    }


def test_market_hours_forwards_non_default_filters():
    fake = FakeTransport(responses=[_response([])])
    _client(fake).overseas.derivatives_market_hours(
        product_group="ES", asset_class="003", exchange="CME", kind="N"
    )
    params = fake.calls[0]["params"]
    assert params["FM_PDGR_CD"] == "ES"
    assert params["FM_CLAS_CD"] == "003"
    assert params["FM_EXCG_CD"] == "CME"
    assert params["OPT_YN"] == "N"


def test_market_hours_walks_ctx_area_pages_without_tr_cont():
    fake = FakeTransport(
        responses=[
            _response([_row()], ctx_area_nk200="NEXT", ctx_area_fk200="FIRST"),
            _response([_row(fm_pdgr_cd="NQ", fm_pdgr_name="E-MINI NASDAQ-100")]),
        ]
    )
    hours = _client(fake).overseas.derivatives_market_hours()
    assert [item.product_group_code for item in hours] == ["ES", "NQ"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FIRST"


def test_market_hours_rejects_demo_environment():
    fake = FakeTransport(responses=[])
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").overseas.derivatives_market_hours()
    assert fake.calls == []


@pytest.mark.parametrize("output", [None, {}, "not-an-array"])
def test_market_hours_missing_or_non_list_output_fails_closed(output):
    body = {} if output is None else {"output": output}
    response = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)
    fake = FakeTransport(responses=[response])
    with pytest.raises(KISError):
        _client(fake).overseas.derivatives_market_hours()


def test_market_hours_invalid_time_is_none():
    fake = FakeTransport(responses=[_response([_row(am_mkmn_strt_tmd="246000")])])
    assert _client(fake).overseas.derivatives_market_hours()[0].am_open is None
