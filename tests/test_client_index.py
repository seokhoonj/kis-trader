"""지수/업종 핸들 -- kis.index(code).quote().

업종 시장구분 U + 업종코드로 조회, 지수 레벨/시고저/전일대비 부호 복원/등락종목수(breadth),
fail-closed 파싱을 가짜 전송으로 검증.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import Bar, IndexQuote, KisClient
from kis_openapi.errors import KisError, KisUsageError
from kis_openapi.transport import RawResponse

_INDEX_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
_INDEX_BARS = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"


def _output(*, value="2650.32", change="12.44", sign="2", pct="0.47", oprc="2640.10",
            hgpr="2655.00", lwpr="2638.00", volume="512000000", amount="9800000000000",
            up="480", down="360", flat="60", limit_up="3", limit_down="1"):
    return {"bstp_nmix_prpr": value, "bstp_nmix_prdy_vrss": change, "prdy_vrss_sign": sign,
            "bstp_nmix_prdy_ctrt": pct, "bstp_nmix_oprc": oprc, "bstp_nmix_hgpr": hgpr,
            "bstp_nmix_lwpr": lwpr, "acml_vol": volume, "acml_tr_pbmn": amount,
            "ascn_issu_cnt": up, "down_issu_cnt": down, "stnr_issu_cnt": flat,
            "uplm_issu_cnt": limit_up, "lslm_issu_cnt": limit_down}


class FakeTransport:
    def __init__(self, *, response=None, by_path=None):
        self.response = response
        self.by_path = by_path or {}
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        if path in self.by_path:
            outcome = self.by_path[path]
            return outcome.pop(0) if isinstance(outcome, list) else outcome
        return self.response


def _resp(output):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": output})


def _client(transport):
    return KisClient(app_key="k", app_secret="s", transport=transport)


def test_index_quote_maps_fields_and_params():
    fake = FakeTransport(response=_resp(_output()))
    quote = _client(fake).index("0001").quote()
    assert isinstance(quote, IndexQuote)
    assert quote.code == "0001"
    assert quote.value == Decimal("2650.32")
    assert quote.open == Decimal("2640.10")
    assert quote.high == Decimal("2655.00")
    assert quote.low == Decimal("2638.00")
    assert quote.change == Decimal("12.44")
    assert quote.change_percent == Decimal("0.47")
    assert quote.volume == 512000000
    assert quote.amount == Decimal(9800000000000)
    assert (quote.advances, quote.declines, quote.unchanged) == (480, 360, 60)
    assert (quote.limit_up, quote.limit_down) == (3, 1)
    call = fake.calls[0]
    assert call["path"] == _INDEX_PRICE
    assert call["tr_id"] == "FHPUP02100000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "U"       # 업종
    assert call["params"]["FID_INPUT_ISCD"] == "0001"


def test_index_quote_negative_change_sign_restored():
    fake = FakeTransport(response=_resp(_output(change="8.10", sign="5", pct="0.31")))
    quote = _client(fake).index("1001").quote()
    assert quote.change == Decimal("-8.10")                      # 하락 -> 음수
    assert quote.change_percent == Decimal("-0.31")
    assert fake.calls[0]["params"]["FID_INPUT_ISCD"] == "1001"


def test_index_quote_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KisError):
        _client(fake).index("0001").quote()


def test_index_quote_error_response_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="1", msg_cd="X", msg1="실패", body={}))
    with pytest.raises(KisError):
        _client(fake).index("0001").quote()


def test_index_quote_bad_value_fails_closed():
    fake = FakeTransport(response=_resp(_output(value="n/a")))
    with pytest.raises(KisError):
        _client(fake).index("0001").quote()


def _bar_row(date_text, oprc, hgpr, lwpr, prpr, vol):
    return {"stck_bsop_date": date_text, "bstp_nmix_oprc": oprc, "bstp_nmix_hgpr": hgpr,
            "bstp_nmix_lwpr": lwpr, "bstp_nmix_prpr": prpr, "acml_vol": vol}


def _bars_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": rows})


def test_index_bars_parses_ascending():
    fake = FakeTransport(response=_bars_resp([
        _bar_row("20240104", "2640", "2660", "2635", "2655", "500"),
        _bar_row("20240103", "2620", "2645", "2615", "2640", "480"),
        _bar_row("20240102", "2600", "2625", "2595", "2620", "460"),
    ]))
    bars = _client(fake).index("0001").bars(start="20240102")
    assert [b.symbol for b in bars] == ["0001", "0001", "0001"]   # 코드가 symbol 자리
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == ["20240102", "20240103", "20240104"]
    assert bars[-1].close == Decimal(2655)
    assert bars[0].open == Decimal(2600)
    call = fake.calls[0]
    assert call["path"] == _INDEX_BARS
    assert call["tr_id"] == "FHKUP03500100"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "U"
    assert call["params"]["FID_PERIOD_DIV_CODE"] == "D"           # 1d
    assert call["params"]["FID_INPUT_ISCD"] == "0001"


def test_index_bars_weekly_and_monthly_period_codes():
    for interval, code in [("1wk", "W"), ("1mo", "M")]:
        fake = FakeTransport(response=_bars_resp([_bar_row("20240105", "1", "1", "1", "1", "1")]))
        _client(fake).index("0001").bars(start="20240105", interval=interval)
        assert fake.calls[0]["params"]["FID_PERIOD_DIV_CODE"] == code


def test_index_bars_paginates_date_window():
    page_a = _bars_resp([_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1") for n in (8, 7, 6, 5)])
    page_b = _bars_resp([_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1")
                         for n in (5, 4, 3, 2, 1)])
    fake = FakeTransport(by_path={_INDEX_BARS: [page_a, page_b]})
    bars = _client(fake).index("0001").bars(start="20240101")
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == [f"2024010{n}" for n in range(1, 9)]
    assert isinstance(bars[0], Bar)


def test_index_bars_start_required():
    fake = FakeTransport(response=_bars_resp([]))
    with pytest.raises(KisUsageError):
        _client(fake).index("0001").bars()


def test_index_bars_minute_not_implemented():
    fake = FakeTransport(response=_bars_resp([]))
    with pytest.raises(NotImplementedError):
        _client(fake).index("0001").bars(interval="1m")


def test_index_bars_non_list_output2_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output2": "oops"})
    with pytest.raises(KisError):
        _client(FakeTransport(response=resp)).index("0001").bars(start="20240101")
