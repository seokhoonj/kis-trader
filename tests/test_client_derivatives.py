"""선물/옵션 핸들 -- kis.domestic.futures(code).quote() / kis.domestic.option(code).quote().

시장구분 F/O 라우팅, output1 파싱(미결제약정·베이시스·이론가·괴리율), 전일대비 부호 복원,
optional 필드(None), fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import Bar, DerivativeQuote, KISClient, OrderBook
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_PRICE = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
_ASKING = "/uapi/domestic-futureoption/v1/quotations/inquire-asking-price"
_CHART = "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice"


def _output(*, last="335.20", oprc="334.10", hgpr="336.00", lwpr="333.50", clpr="333.00",
            vrss="2.20", sign="2", ctrt="0.66", vol="120000", oi="380000", thpr="335.05",
            basis="0.15", dprt="-0.04"):
    return {"hts_kor_isnm": "K200 F 202409", "futs_prpr": last, "futs_oprc": oprc,
            "futs_hgpr": hgpr, "futs_lwpr": lwpr, "futs_prdy_clpr": clpr, "futs_prdy_vrss": vrss,
            "prdy_vrss_sign": sign, "futs_prdy_ctrt": ctrt, "acml_vol": vol,
            "hts_otst_stpl_qty": oi, "hts_thpr": thpr, "basis": basis, "dprt": dprt}


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
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": output})


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def test_futures_quote_maps_fields_and_market():
    fake = FakeTransport(response=_resp(_output()))
    quote = _client(fake).domestic.futures("101W09").quote()
    assert isinstance(quote, DerivativeQuote)
    assert quote.code == "101W09"
    assert quote.last == Decimal("335.20")
    assert quote.previous_close == Decimal("333.00")
    assert quote.change == Decimal("2.20")
    assert quote.change_percent == Decimal("0.66")
    assert quote.volume == 120000
    assert quote.open_interest == 380000               # 미결제약정
    assert quote.theoretical_price == Decimal("335.05")
    assert quote.basis == Decimal("0.15")
    assert quote.premium == Decimal("-0.04")
    call = fake.calls[0]
    assert call["path"] == _PRICE
    assert call["tr_id"] == "FHMIF10000000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "F"   # 지수선물
    assert call["params"]["FID_INPUT_ISCD"] == "101W09"


def test_option_quote_uses_o_market():
    fake = FakeTransport(response=_resp(_output()))
    _client(fake).domestic.option("201W09335").quote()
    assert fake.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"] == "O"   # 지수옵션


def test_derivatives_quote_negative_change():
    fake = FakeTransport(response=_resp(_output(vrss="1.50", sign="5", ctrt="0.45")))
    quote = _client(fake).domestic.futures("101W09").quote()
    assert quote.change == Decimal("-1.50")            # 하락 -> 음수
    assert quote.change_percent == Decimal("-0.45")


def test_derivatives_quote_optional_fields_none():
    fake = FakeTransport(response=_resp(_output(thpr="", basis="", dprt="")))
    quote = _client(fake).domestic.futures("101W09").quote()
    assert quote.theoretical_price is None
    assert quote.basis is None
    assert quote.premium is None
    assert quote.open_interest == 380000               # 핵심 필드는 여전히 파싱


def test_derivatives_quote_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.futures("101W09").quote()


def test_derivatives_quote_bad_value_fails_closed():
    fake = FakeTransport(response=_resp(_output(last="n/a")))
    with pytest.raises(KISError):
        _client(fake).domestic.futures("101W09").quote()


# --- order_book (호가 사다리는 output2) -------------------------------------
def _book(**over):
    out = {
        "futs_askp1": "364.40", "futs_askp2": "364.45", "futs_askp3": "0",
        "futs_askp4": "0", "futs_askp5": "0",
        "askp_rsqn1": "35", "askp_rsqn2": "47", "askp_rsqn3": "0",
        "askp_rsqn4": "0", "askp_rsqn5": "0",
        "futs_bidp1": "364.35", "futs_bidp2": "364.30", "futs_bidp3": "364.25",
        "futs_bidp4": "0", "futs_bidp5": "0",
        "bidp_rsqn1": "22", "bidp_rsqn2": "70", "bidp_rsqn3": "68",
        "bidp_rsqn4": "0", "bidp_rsqn5": "0",
        "total_askp_rsqn": "7140", "total_bidp_rsqn": "9319",
    }
    out.update(over)
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {"hts_kor_isnm": "F 202409"}, "output2": out})


def test_futures_order_book_maps_output2_and_market():
    fake = FakeTransport(response=_book())
    book = _client(fake).domestic.futures("101W09").order_book()
    assert isinstance(book, OrderBook)
    assert book.symbol == "101W09"
    assert book.market == "F"
    assert [(lvl.price, lvl.quantity) for lvl in book.asks] == [
        (Decimal("364.40"), 35), (Decimal("364.45"), 47),
    ]                                                          # 0-가격 단계 skip
    assert [(lvl.price, lvl.quantity) for lvl in book.bids] == [
        (Decimal("364.35"), 22), (Decimal("364.30"), 70), (Decimal("364.25"), 68),
    ]
    assert book.total_ask_quantity == 7140
    assert book.total_bid_quantity == 9319
    call = fake.calls[0]
    assert call["path"] == _ASKING
    assert call["tr_id"] == "FHMIF10010000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "F"


def test_option_order_book_uses_o_market():
    fake = FakeTransport(response=_book())
    _client(fake).domestic.option("201W09335").order_book()
    assert fake.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"] == "O"


def test_derivatives_order_book_missing_output2_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                       body={"output1": {"hts_kor_isnm": "F"}})    # output2 없음
    fake = FakeTransport(response=resp)
    with pytest.raises(KISError):
        _client(fake).domestic.futures("101W09").order_book()


# --- bars (기간봉, 캔들은 output2, 종가=futs_prpr) ---------------------------
def _candle(bsop, o, h, low, close, vol="100"):
    return {"stck_bsop_date": bsop, "futs_oprc": o, "futs_hgpr": h,
            "futs_lwpr": low, "futs_prpr": close, "acml_vol": vol}


def _bars_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {"hts_kor_isnm": "F 202409"}, "output2": rows})


def test_futures_bars_maps_candles_ascending():
    rows = [_candle("20260803", "334.0", "336.0", "333.0", "335.0"),
            _candle("20260801", "332.0", "335.0", "331.0", "334.0")]
    fake = FakeTransport(response=_bars_resp(rows))
    bars = _client(fake).domestic.futures("101W09").bars("1d", start="20260801", end="20260803")
    assert all(isinstance(b, Bar) for b in bars)
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == ["20260801", "20260803"]  # 오름차순
    last = bars[-1]
    assert last.open == Decimal("334.0")
    assert last.close == Decimal("335.0")                 # 종가=futs_prpr
    assert last.volume == 100
    call = fake.calls[0]
    assert call["path"] == _CHART
    assert call["tr_id"] == "FHKIF03020100"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "F"
    assert call["params"]["FID_PERIOD_DIV_CODE"] == "D"
    assert "FID_ORG_ADJ_PRC" not in call["params"]        # 파생엔 수정주가 없음


def test_derivatives_bars_requires_start():
    fake = FakeTransport(response=_bars_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.futures("101W09").bars("1d")


_MINUTE_CHART = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"


def _min_candle(hhmmss, close, *, vol="10"):
    return {"stck_bsop_date": "20240417", "stck_cntg_hour": hhmmss, "futs_oprc": "359.60",
            "futs_hgpr": "359.80", "futs_lwpr": "359.40", "futs_prpr": close, "cntg_vol": vol,
            "acml_tr_pbmn": "31394925"}


class MinuteFakeTransport:
    """FID_INPUT_HOUR_1 이하 분봉을 최신 3건씩 돌려주는 가짜 전송(당일)."""

    def __init__(self, minutes):
        self.minutes = dict(sorted(minutes.items()))
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        anchor = params["FID_INPUT_HOUR_1"]
        at_or_before = [t for t in self.minutes if t <= anchor]
        page = [self.minutes[t] for t in at_or_before[-3:]]
        return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": page})


def test_futures_minute_bars_paginate_ascending_and_params():
    times = [f"09{m:02d}00" for m in range(9)]                   # 0900..0908, 1분 간격
    minutes = {t: _min_candle(t, str(359 + i)) for i, t in enumerate(times)}
    fake = MinuteFakeTransport(minutes)
    bars = _client(fake).domestic.futures("101W09").bars("1m")
    assert [f"{b.timestamp:%H%M%S}" for b in bars] == times       # 과거->현재 오름차순, 전량
    assert bars[-1].close == Decimal(367)                         # 종가=futs_prpr
    assert bars[-1].volume == 10                                  # 분당 거래량=cntg_vol
    call = fake.calls[0]
    assert call["path"] == _MINUTE_CHART
    assert call["tr_id"] == "FHKIF03020200"
    assert call["params"]["FID_HOUR_CLS_CODE"] == "60"           # 1분
    assert call["params"]["FID_PW_DATA_INCU_YN"] == "N"          # 당일치
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "F"
    assert call["params"]["FID_INPUT_HOUR_1"] == "235959"        # 최신부터
    # 2페이지 기준시각 = 1페이지 최오래 봉(090600) 1분 전
    assert fake.calls[1]["params"]["FID_INPUT_HOUR_1"] == "090500"


def test_futures_minute_bars_respects_max_bars():
    times = [f"09{m:02d}00" for m in range(9)]
    minutes = {t: _min_candle(t, str(359 + i)) for i, t in enumerate(times)}
    fake = MinuteFakeTransport(minutes)
    bars = _client(fake).domestic.futures("101W09").bars("1m", max_bars=4)
    assert len(bars) == 4
    assert [f"{b.timestamp:%H%M%S}" for b in bars] == times[-4:]


def test_futures_minute_bars_missing_output2_fails_closed():
    class Bad:
        def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
            return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={})
    from kis_openapi.errors import KISError
    with pytest.raises(KISError):
        _client(Bad()).domestic.futures("101W09").bars("1m")


def test_underlying_quote_maps_two_sign_fields():
    # 원장 응답 예시값(F 202406). 기초자산·선물이 서로 다른 부호 필드로 복원돼야 한다(둘 다 sign=5 하락).
    output1 = {"unas_prpr": "367.25", "unas_prdy_vrss": "3.47", "unas_prdy_vrss_sign": "5",
               "unas_prdy_ctrt": "0.94", "unas_acml_vol": "161725000", "hts_kor_isnm": "F 202406",
               "futs_prpr": "369.35", "futs_prdy_vrss": "3.45", "prdy_vrss_sign": "5",
               "futs_prdy_ctrt": "0.93"}
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": output1, "output2": []})
    fake = FakeTransport(response=resp)
    from kis_openapi import UnderlyingQuote
    uq = _client(fake).domestic.futures("101V06").underlying_quote()
    assert isinstance(uq, UnderlyingQuote)
    assert uq.name == "F 202406"
    assert uq.underlying_price == Decimal("367.25")
    assert uq.underlying_change == Decimal("-3.47")      # unas_prdy_vrss_sign=5 -> 음수
    assert uq.underlying_change_percent == Decimal("-0.94")
    assert uq.underlying_volume == 161725000
    assert uq.futures_price == Decimal("369.35")
    assert uq.futures_change == Decimal("-3.45")         # prdy_vrss_sign=5 -> 음수
    assert uq.futures_change_percent == Decimal("-0.93")
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-futureoption/v1/quotations/display-board-top"
    assert call["tr_id"] == "FHPIF05030000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "F"


def test_underlying_quote_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    from kis_openapi.errors import KISError
    with pytest.raises(KISError):
        _client(fake).domestic.futures("101V06").underlying_quote()


def test_expected_execution_trend_maps_summary_and_sorted_points():
    summary = {
        "hts_kor_isnm": "K200 F 202409", "futs_antc_cnpr": "335.20",
        "antc_cntg_vrss_sign": "5", "futs_antc_cntg_vrss": "1.30",
        "antc_cntg_prdy_ctrt": "0.39", "futs_sdpr": "336.50",
    }
    rows = [
        {"stck_cntg_hour": "085902", "futs_antc_cnpr": "335.20",
         "antc_cntg_vrss_sign": "5", "futs_antc_cntg_vrss": "1.30",
         "antc_cntg_prdy_ctrt": "0.39"},
        {"stck_cntg_hour": "085901", "futs_antc_cnpr": "336.80",
         "antc_cntg_vrss_sign": "2", "futs_antc_cntg_vrss": "0.30",
         "antc_cntg_prdy_ctrt": "0.09"},
    ]
    response = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                           body={"output1": summary, "output2": rows})
    fake = FakeTransport(response=response)

    from kis_openapi import ExpectedExecutionPoint, ExpectedExecutionTrend
    trend = _client(fake).domestic.futures("101W09").expected_execution_trend()

    assert isinstance(trend, ExpectedExecutionTrend)
    assert isinstance(trend.points[0], ExpectedExecutionPoint)
    assert trend.name == "K200 F 202409"
    assert trend.price == Decimal("335.20")
    assert trend.change == Decimal("-1.30")
    assert trend.change_percent == Decimal("-0.39")
    assert trend.base_price == Decimal("336.50")
    assert [f"{point.timestamp:%H%M%S}" for point in trend.points] == ["085901", "085902"]
    assert trend.points[0].change == Decimal("0.30")
    assert trend.points[1].change == Decimal("-1.30")
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-futureoption/v1/quotations/exp-price-trend"
    assert call["tr_id"] == "FHPIF05110100"
    assert call["params"] == {"FID_INPUT_ISCD": "101W09", "FID_COND_MRKT_DIV_CODE": "F"}


def test_expected_execution_trend_missing_output2_fails_closed():
    response = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                           body={"output1": {}})
    fake = FakeTransport(response=response)
    with pytest.raises(KISError):
        _client(fake).domestic.option("201W09335").expected_execution_trend()


def test_option_board_futures_maps_official_output_array():
    row = {
        "futs_shrn_iscd": "101W09", "hts_kor_isnm": "K200 F 202409",
        "futs_prpr": "335.20", "futs_prdy_vrss": "1.30", "prdy_vrss_sign": "5",
        "futs_prdy_ctrt": "0.39", "hts_thpr": "335.15", "acml_vol": "120000",
        "futs_askp": "335.25", "futs_bidp": "335.20", "hts_otst_stpl_qty": "380000",
        "futs_hgpr": "338.00", "futs_lwpr": "334.50", "hts_rmnn_dynu": "31",
        "total_askp_rsqn": "7140", "total_bidp_rsqn": "9319",
        "futs_antc_cnpr": "335.10", "futs_antc_cntg_vrss": "1.40",
        "antc_cntg_vrss_sign": "5", "antc_cntg_prdy_ctrt": "0.42",
    }
    response = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                           body={"output": [row]})
    fake = FakeTransport(response=response)

    from kis_openapi import FuturesBoardQuote
    quotes = _client(fake).domestic.option_board_futures()

    assert isinstance(quotes[0], FuturesBoardQuote)
    quote = quotes[0]
    assert quote.code == "101W09"
    assert quote.price == Decimal("335.20")
    assert quote.change == Decimal("-1.30")
    assert quote.theoretical_price == Decimal("335.15")
    assert quote.open_interest == 380000
    assert quote.days_to_expiry == 31
    assert quote.total_ask_quantity == 7140
    assert quote.expected_price == Decimal("335.10")
    assert quote.expected_change == Decimal("-1.40")
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-futureoption/v1/quotations/display-board-futures"
    assert call["tr_id"] == "FHPIF05030200"
    assert call["params"] == {
        "FID_COND_MRKT_DIV_CODE": "F", "FID_COND_SCR_DIV_CODE": "20503",
        "FID_COND_MRKT_CLS_CODE": "MKI",
    }


def test_option_board_futures_rejects_missing_output_and_blank_market_class():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.option_board_futures()
    with pytest.raises(KISUsageError):
        _client(fake).domestic.option_board_futures(market_class=" ")


def test_option_expiries_reads_output_array():
    # 원장 예시: 배열 키가 output(레이아웃엔 output1). 예시값 그대로.
    rows = [{"mtrt_yymm_code": "0V05", "mtrt_yymm": "202405"},
            {"mtrt_yymm_code": "0V06", "mtrt_yymm": "202406"}]
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})
    fake = FakeTransport(response=resp)
    from kis_openapi import OptionExpiry
    expiries = _client(fake).domestic.option_expiries()
    assert isinstance(expiries[0], OptionExpiry)
    assert expiries[0].code == "0V05"
    assert expiries[0].year_month == "202405"
    assert expiries[1].year_month == "202406"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-futureoption/v1/quotations/display-board-option-list"
    assert call["tr_id"] == "FHPIO056104C0"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "509"


def test_option_expiries_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.option_expiries()
