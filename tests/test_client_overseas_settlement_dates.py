"""해외 시장별 결제일자 조회의 라우팅·연속조회·파싱·실패 경계를 검증한다."""

from __future__ import annotations

import threading
from datetime import date

import pytest

from kis_trader import KISClient, OverseasSettlementDate
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_PATH = "/uapi/overseas-stock/v1/quotations/countries-holiday"


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


def _client(transport, *, profile="main"):
    return KISClient(
        app_key="k", app_secret="s", profile=profile, transport=transport
    )


def _row(**over):
    row = {
        "prdt_type_cd": "512",
        "tr_natn_cd": "840",
        "tr_natn_name": "미국",
        "natn_eng_abrv_cd": "US",
        "tr_mket_cd": "NAS",
        "tr_mket_name": "나스닥",
        "acpl_sttl_dt": "20260810",
        "dmst_sttl_dt": "20260811",
    }
    row.update(over)
    return row


def _response(rows, **extra):
    body = {"output": rows, **extra}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


def test_settlement_dates_single_page_routes_and_parses():
    fake = FakeTransport(responses=[_response([_row(dmst_sttl_dt="")])])
    dates = _client(fake).overseas.settlement_dates()

    assert len(dates) == 1
    item = dates[0]
    assert isinstance(item, OverseasSettlementDate)
    assert item.market_type_code == "512"
    assert item.country_code == "840"
    assert item.country_name == "미국"
    assert item.country_abbr == "US"
    assert item.market_code == "NAS"
    assert item.market_name == "나스닥"
    assert item.local_settlement_date == date(2026, 8, 10)
    assert item.domestic_settlement_date is None
    assert fake.calls[0] == {
        "method": "GET",
        "path": _PATH,
        "tr_id": "CTOS5011R",
        "params": {"CTX_AREA_NK": "", "CTX_AREA_FK": ""},
    }


def test_settlement_dates_walks_ctx_area_pages_without_tr_cont():
    fake = FakeTransport(
        responses=[
            _response([_row()], ctx_area_nk="NEXT", ctx_area_fk="FIRST"),
            _response([_row(prdt_type_cd="513", tr_mket_cd="NYS")]),
        ]
    )
    dates = _client(fake).overseas.settlement_dates()
    assert [item.market_type_code for item in dates] == ["512", "513"]
    assert fake.calls[1]["params"] == {
        "CTX_AREA_NK": "NEXT",
        "CTX_AREA_FK": "FIRST",
    }


def test_settlement_dates_rejects_demo_environment():
    fake = FakeTransport(responses=[])
    with pytest.raises(KISUsageError):
        _client(fake, profile="paper").overseas.settlement_dates()
    assert fake.calls == []


@pytest.mark.parametrize("output", [None, {}, "not-an-array"])
def test_settlement_dates_missing_or_non_list_output_fails_closed(output):
    body = {} if output is None else {"output": output}
    response = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)
    fake = FakeTransport(responses=[response])
    with pytest.raises(KISError):
        _client(fake).overseas.settlement_dates()


def test_settlement_dates_invalid_date_is_none():
    fake = FakeTransport(responses=[_response([_row(acpl_sttl_dt="20260230")])])
    assert _client(fake).overseas.settlement_dates()[0].local_settlement_date is None
