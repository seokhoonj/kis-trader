"""파생상품 기초자산 증거금율 -- kis.domestic.derivative_margin_rates(base_date).

기준일별 기초자산 증거금율 표(TTTO6032R, 실전 전용)를 네트워크 없이 FakeTransport 로 검증한다:
라우팅(TR/경로/파라미터), 기준일 형식검증, CTX_AREA_NK200 2페이지 연속조회, 파싱, 비배열 output
fail-closed.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient
from kis_trader.domestic.entities.derivative import DerivativeMarginRate
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_MARGIN_RATE_PATH = "/uapi/domestic-futureoption/v1/quotations/margin-rate"


class FakeTransport:
    def __init__(self, *, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "tr_cont": tr_cont})
        return self.responses.pop(0)


def _row(*, bast_id="101S", bast_name="KOSPI200", bast_pric="410.25", brkg="9.00",
         tr_mgna="6.00", mtpl="250000", futr="9230625"):
    return {"bast_id": bast_id, "bast_name": bast_name, "bast_pric": bast_pric,
            "brkg_mgna_rt": brkg, "tr_mgna_rt": tr_mgna, "tr_mtpl_idx": mtpl,
            "ctrt_per_futr_mgna": futr}


def _resp(rows, *, tr_cont="D", ctx_nk=""):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output": rows, "ctx_area_nk200": ctx_nk}, tr_cont=tr_cont)


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def test_margin_rates_parses_and_routes():
    fake = FakeTransport(responses=[_resp([_row()])])
    result = _client(fake).domestic.derivative_margin_rates("20260819")
    assert len(result) == 1
    rate = result[0]
    assert isinstance(rate, DerivativeMarginRate)
    assert rate.underlying_id == "101S"
    assert rate.underlying_name == "KOSPI200"
    assert rate.underlying_price == Decimal("410.25")
    assert rate.brokerage_margin_rate == Decimal("9.00")
    assert rate.trading_margin_rate == Decimal("6.00")
    assert rate.trading_multiplier == Decimal(250000)
    assert rate.futures_margin_per_contract == Decimal(9230625)
    assert rate._raw["bast_id"] == "101S"
    call = fake.calls[0]
    assert call["path"] == _MARGIN_RATE_PATH
    assert call["tr_id"] == "TTTO6032R"
    assert call["params"]["BASS_DT"] == "20260819"
    assert call["params"]["BAST_ID"] == ""              # 기본 -- 전체 기초자산
    assert call["params"]["CTX_AREA_NK200"] == ""


def test_margin_rates_underlying_id_param():
    fake = FakeTransport(responses=[_resp([_row()])])
    _client(fake).domestic.derivative_margin_rates("20260819", underlying_id="101S")
    assert fake.calls[0]["params"]["BAST_ID"] == "101S"


def test_margin_rates_bad_date_fails_closed_no_wire():
    fake = FakeTransport(responses=[_resp([_row()])])
    with pytest.raises(KISUsageError):
        _client(fake).domestic.derivative_margin_rates("2026-08-19")
    assert fake.calls == []                              # 와이어 미접촉


def test_margin_rates_paginates_on_nk200():
    page1 = _resp([_row(bast_id="101S")], tr_cont="M", ctx_nk="CURSOR42")
    page2 = _resp([_row(bast_id="105S")], tr_cont="D")
    fake = FakeTransport(responses=[page1, page2])
    result = _client(fake).domestic.derivative_margin_rates("20260819")
    assert [r.underlying_id for r in result] == ["101S", "105S"]
    assert len(fake.calls) == 2
    # 2페이지 요청은 첫 페이지 커서를 CTX_AREA_NK200 로 되먹이고 tr_cont="N".
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "CURSOR42"
    assert fake.calls[1]["tr_cont"] == "N"


def test_margin_rates_skips_blank_underlying():
    fake = FakeTransport(responses=[_resp([_row(), _row(bast_id="  ")])])
    result = _client(fake).domestic.derivative_margin_rates("20260819")
    assert len(result) == 1                              # 빈 bast_id 행은 건너뜀


def test_margin_rates_non_list_output_fails_closed():
    fake = FakeTransport(responses=[_resp({"bast_id": "101S"})])
    with pytest.raises(KISError):
        _client(fake).domestic.derivative_margin_rates("20260819")


def test_margin_rates_paper_fails_closed_no_wire():
    fake = FakeTransport(responses=[_resp([_row()])])
    kis = KISClient(app_key="k", app_secret="s", environment="paper", transport=fake)
    with pytest.raises(KISUsageError):
        kis.domestic.derivative_margin_rates("20260819")
    assert fake.calls == []                              # 가드는 와이어 이전 -- 호출 없음


class _StickyTransport:
    """연속조회가 끝나지 않는(항상 tr_cont="M") 응답을 무한히 돌려주는 전송."""

    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        self.calls.append({"params": params, "tr_cont": tr_cont})
        return self.response


def test_margin_rates_page_cap_fails_closed():
    never_ends = _resp([_row()], tr_cont="M", ctx_nk="CURSOR")
    fake = _StickyTransport(never_ends)
    with pytest.raises(KISError):
        _client(fake).domestic.derivative_margin_rates("20260819")
