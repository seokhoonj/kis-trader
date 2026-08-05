"""지수/업종 핸들 -- kis.index(code).quote().

업종 시장구분 U + 업종코드로 조회, 지수 레벨/시고저/전일대비 부호 복원/등락종목수(breadth),
fail-closed 파싱을 가짜 전송으로 검증.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import Bar, IndexQuote, KISClient
from kis_openapi.errors import KISError, KISUsageError
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
    return KISClient(app_key="k", app_secret="s", transport=transport)


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
    with pytest.raises(KISError):
        _client(fake).index("0001").quote()


def test_index_quote_error_response_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="1", msg_cd="X", msg1="실패", body={}))
    with pytest.raises(KISError):
        _client(fake).index("0001").quote()


def test_index_quote_bad_value_fails_closed():
    fake = FakeTransport(response=_resp(_output(value="n/a")))
    with pytest.raises(KISError):
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
    with pytest.raises(KISUsageError):
        _client(fake).index("0001").bars()


def test_index_bars_minute_not_implemented():
    fake = FakeTransport(response=_bars_resp([]))
    with pytest.raises(NotImplementedError):
        _client(fake).index("0001").bars(interval="1m")


def test_index_bars_non_list_output2_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output2": "oops"})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).index("0001").bars(start="20240101")


_INDEX_INTRADAY = "/uapi/domestic-stock/v1/quotations/inquire-index-timeprice"


def _intraday_row(hour, value, change, sign, acml, cntg):
    return {"bsop_hour": hour, "bstp_nmix_prpr": value, "bstp_nmix_prdy_vrss": change,
            "prdy_vrss_sign": sign, "acml_vol": acml, "cntg_vol": cntg}


def _intraday_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def test_index_intraday_maps_fields_sorted_ascending():
    from kis_openapi import IndexIntradayPoint
    fake = FakeTransport(response=_intraday_resp([
        _intraday_row("100600", "2650.10", "12.30", "2", "500", "40"),
        _intraday_row("100500", "2649.80", "12.00", "2", "460", "38"),
    ]))
    points = _client(fake).index("0001").intraday()
    assert [f"{p.time:%H%M%S}" for p in points] == ["100500", "100600"]   # 오름차순 정렬
    assert all(isinstance(p, IndexIntradayPoint) for p in points)
    assert points[-1].value == Decimal("2650.10")
    assert points[-1].change == Decimal("12.30")
    assert points[-1].volume == 500
    assert points[-1].interval_volume == 40
    call = fake.calls[0]
    assert call["path"] == _INDEX_INTRADAY
    assert call["tr_id"] == "FHPUP02110200"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "U"
    assert call["params"]["FID_INPUT_ISCD"] == "0001"
    assert call["params"]["FID_INPUT_HOUR_1"] == "60"           # 1m = 60초


def test_index_intraday_interval_maps_and_negative_change():
    fake = FakeTransport(response=_intraday_resp([_intraday_row("131000", "900.00", "5.5", "5",
                                                                "100", "10")]))
    points = _client(fake).index("1001").intraday(interval="10m")
    assert fake.calls[0]["params"]["FID_INPUT_HOUR_1"] == "600"  # 10m = 600초
    assert points[0].change == Decimal("-5.5")                   # 하락 -> 음수


def test_index_intraday_bad_interval():
    fake = FakeTransport(response=_intraday_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).index("0001").intraday(interval="3m")


def test_index_intraday_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).index("0001").intraday()


_INDEX_CATEGORY = "/uapi/domestic-stock/v1/quotations/inquire-index-category-price"


def _category_row(code="0002", name="대형주", value="2700.10", change="15.0", sign="2",
                  pct="0.56", vol="120000000", amt="4200000000000", vol_rlim="23.4",
                  amt_rlim="42.9"):
    return {"bstp_cls_code": code, "hts_kor_isnm": name, "bstp_nmix_prpr": value,
            "bstp_nmix_prdy_vrss": change, "prdy_vrss_sign": sign, "bstp_nmix_prdy_ctrt": pct,
            "acml_vol": vol, "acml_tr_pbmn": amt, "acml_vol_rlim": vol_rlim,
            "acml_tr_pbmn_rlim": amt_rlim}


def _category_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {"bstp_nmix_prpr": "2650"}, "output2": rows})


def test_index_categories_maps_fields_and_market_class():
    from kis_openapi import CategoryIndex
    fake = FakeTransport(response=_category_resp([_category_row(), _category_row(code="0003",
                                                                                name="중형주")]))
    cats = _client(fake).index("0001").categories()
    assert [c.code for c in cats] == ["0002", "0003"]
    first = cats[0]
    assert isinstance(first, CategoryIndex)
    assert first.name == "대형주"
    assert first.value == Decimal("2700.10")
    assert first.change == Decimal("15.0")
    assert first.change_percent == Decimal("0.56")
    assert first.volume_share == Decimal("23.4")
    assert first.amount_share == Decimal("42.9")
    call = fake.calls[0]
    assert call["path"] == _INDEX_CATEGORY
    assert call["tr_id"] == "FHPUP02140000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20214"
    assert call["params"]["FID_MRKT_CLS_CODE"] == "K"           # 0001 -> 거래소
    assert call["params"]["FID_BLNG_CLS_CODE"] == "0"


def test_index_categories_market_class_for_kosdaq_and_kospi200():
    for code, cls in [("1001", "Q"), ("2001", "K2")]:
        fake = FakeTransport(response=_category_resp([_category_row()]))
        _client(fake).index(code).categories()
        assert fake.calls[0]["params"]["FID_MRKT_CLS_CODE"] == cls


def test_index_categories_rejects_non_market_code():
    fake = FakeTransport(response=_category_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).index("0002").categories()      # 하위 업종엔 categories 없음


def test_index_categories_missing_output2_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output1": {"bstp_nmix_prpr": "2650"}}))
    with pytest.raises(KISError):
        _client(fake).index("0001").categories()
