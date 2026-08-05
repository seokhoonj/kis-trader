"""ELW 핸들 -- kis.elw(code) 고유 지표.

민감도(그릭스) 추이: 일별/체결별 라우팅(TR·URL·시장구분 W), output 배열 파싱(그릭스·이론가·
전일대비 부호 복원), 시간축(영업일자 vs 체결시각), optional 그릭스(None), fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import (
    ELW,
    ELWIndicatorPoint,
    ELWLPFlow,
    ELWSensitivityPoint,
    ELWVolatilityPoint,
    KISClient,
)
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_DAILY = "/uapi/elw/v1/quotations/sensitivity-trend-daily"
_CCNL = "/uapi/elw/v1/quotations/sensitivity-trend-ccnl"
_VOL_DAILY = "/uapi/elw/v1/quotations/volatility-trend-daily"
_VOL_CCNL = "/uapi/elw/v1/quotations/volatility-trend-ccnl"
_VOL_MINUTE = "/uapi/elw/v1/quotations/volatility-trend-minute"
_VOL_TICK = "/uapi/elw/v1/quotations/volatility-trend-tick"
_IND_DAILY = "/uapi/elw/v1/quotations/indicator-trend-daily"
_IND_MINUTE = "/uapi/elw/v1/quotations/indicator-trend-minute"
_LP = "/uapi/elw/v1/quotations/lp-trade-trend"


def _resp2(rows):
    """LP 매매추이는 output1(요약) + output2(일별 흐름)."""
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {"elw_prpr": "40"}, "output2": rows})


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
    return KISClient(app_key="k", app_secret="s", transport=transport)


def _daily_row(bsop="20240507", price="25", vrss="20", sign="5", ctrt="44.44",
               thpr="20.39", delta="-0.4034", gama="0.0000", theta="0.5843",
               vega="0.9954", rho="-0.3529"):
    return {"stck_bsop_date": bsop, "elw_prpr": price, "prdy_vrss": vrss,
            "prdy_vrss_sign": sign, "prdy_ctrt": ctrt, "hts_thpr": thpr,
            "delta_val": delta, "gama": gama, "theta": theta, "vega": vega, "rho": rho}


def test_elw_accessor_returns_handle():
    handle = _client(FakeTransport(response=_resp([]))).elw("58J297")
    assert isinstance(handle, ELW)
    assert handle.code == "58J297"


def test_sensitivity_trend_daily_maps_greeks_and_market():
    fake = FakeTransport(response=_resp([_daily_row()]))
    points = _client(fake).elw("58J438").sensitivity_trend("day")
    assert all(isinstance(p, ELWSensitivityPoint) for p in points)
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
    with pytest.raises(KISUsageError):
        _client(fake).elw("58J438").sensitivity_trend("minute")


def test_sensitivity_trend_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).elw("58J438").sensitivity_trend("day")


def test_sensitivity_trend_bad_value_fails_closed():
    fake = FakeTransport(response=_resp([_daily_row(price="n/a")]))
    with pytest.raises(KISError):
        _client(fake).elw("58J438").sensitivity_trend("day")


# --- volatility trend (내재변동성; 4개 시간축) ------------------------------
def test_volatility_trend_daily_maps_iv_and_change():
    row = {"stck_bsop_date": "20240503", "elw_prpr": "5", "prdy_vrss": "0",
           "prdy_vrss_sign": "3", "prdy_ctrt": "0.00", "elw_oprc": "5", "elw_hgpr": "5",
           "elw_lwpr": "5", "acml_vol": "76410", "d10_hist_vltl": "21.05",
           "hts_ints_vltl": "23.37"}
    fake = FakeTransport(response=_resp([row]))
    points = _client(fake).elw("58J297").volatility_trend("day")
    assert all(isinstance(p, ELWVolatilityPoint) for p in points)
    point = points[0]
    assert point.price == Decimal(5)
    assert point.implied_volatility == Decimal("23.37")
    assert point.change == Decimal(0)
    assert point.change_percent == Decimal("0.00")
    assert f"{point.timestamp:%Y%m%d}" == "20240503"
    assert point._raw["d10_hist_vltl"] == "21.05"          # 역사변동성 곡선은 _raw
    call = fake.calls[0]
    assert call["path"] == _VOL_DAILY
    assert call["tr_id"] == "FHPEW02840200"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "W"


def test_volatility_trend_ccnl_uses_execution_time_and_change():
    row = {"stck_cntg_hour": "150121", "elw_prpr": "45", "prdy_vrss": "10",
           "prdy_vrss_sign": "5", "prdy_ctrt": "18.18", "bidp": "45", "askp": "50",
           "acml_vol": "52690", "hts_ints_vltl": "33.05"}
    fake = FakeTransport(response=_resp([row]))
    point = _client(fake).elw("58J297").volatility_trend("trade")[0]
    assert fake.calls[0]["path"] == _VOL_CCNL
    assert fake.calls[0]["tr_id"] == "FHPEW02840100"
    assert point.implied_volatility == Decimal("33.05")
    assert point.change == Decimal(-10)                    # sign 5 -> 하락
    assert point.timestamp.hour == 15 and point.timestamp.minute == 1


def test_volatility_trend_minute_combines_date_time_and_no_change():
    row = {"stck_bsop_date": "20240422", "stck_cntg_hour": "142800", "stck_prpr": "265",
           "elw_oprc": "265", "elw_hgpr": "265", "elw_lwpr": "265", "hts_ints_vltl": "21.90",
           "hist_vltl": ""}
    fake = FakeTransport(response=_resp([row]))
    point = _client(fake).elw("58J297").volatility_trend("minute", minutes=5)[0]
    assert fake.calls[0]["path"] == _VOL_MINUTE
    assert fake.calls[0]["params"]["FID_HOUR_CLS_CODE"] == "300"     # 5분
    assert fake.calls[0]["params"]["FID_PW_DATA_INCU_YN"] == "N"
    assert point.price == Decimal(265)                     # 분별은 stck_prpr
    assert point.change is None                            # 분별은 전일대비 없음
    assert f"{point.timestamp:%Y%m%d %H%M%S}" == "20240422 142800"


def test_volatility_trend_tick_date_plus_time():
    row = {"bsop_date": "20240507", "stck_cntg_hour": "150619", "elw_prpr": "25",
           "hts_ints_vltl": "33.03"}
    fake = FakeTransport(response=_resp([row]))
    point = _client(fake).elw("58J297").volatility_trend("tick")[0]
    assert fake.calls[0]["path"] == _VOL_TICK
    assert point.implied_volatility == Decimal("33.03")
    assert f"{point.timestamp:%Y%m%d %H%M%S}" == "20240507 150619"


def test_volatility_trend_include_past_flag():
    row = {"stck_bsop_date": "20240422", "stck_cntg_hour": "142800", "stck_prpr": "265",
           "hts_ints_vltl": "21.90"}
    fake = FakeTransport(response=_resp([row]))
    _client(fake).elw("58J297").volatility_trend("minute", include_past=True)
    assert fake.calls[0]["params"]["FID_PW_DATA_INCU_YN"] == "Y"


def test_volatility_trend_rejects_bad_minutes():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).elw("58J297").volatility_trend("minute", minutes=2)


def test_volatility_trend_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).elw("58J297").volatility_trend("day")


# --- indicator trend (레버리지·기어링·내재가치·패리티) -----------------------
def test_indicator_trend_daily_maps_indicators_and_change():
    row = {"stck_bsop_date": "20240503", "elw_prpr": "40", "prdy_vrss_sign": "5",
           "prdy_vrss": "5", "prdy_ctrt": "11.11", "acml_vol": "1000020",
           "lvrg_val": "-11.0377", "gear": "19.45", "tmvl_val": "18.00", "invl_val": "22.00",
           "prit": "102.82", "elw_oprc": "40", "apprch_rate": "0.00"}
    fake = FakeTransport(response=_resp([row]))
    points = _client(fake).elw("57K281").indicator_trend("day")
    assert all(isinstance(p, ELWIndicatorPoint) for p in points)
    point = points[0]
    assert point.price == Decimal(40)
    assert point.leverage == Decimal("-11.0377")
    assert point.gearing == Decimal("19.45")
    assert point.intrinsic_value == Decimal("22.00")
    assert point.parity == Decimal("102.82")
    assert point.change == Decimal(-5)                      # sign 5 -> 하락
    assert point._raw["tmvl_val"] == "18.00"               # 시간가치는 _raw
    call = fake.calls[0]
    assert call["path"] == _IND_DAILY
    assert call["tr_id"] == "FHPEW02740200"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "W"


def test_indicator_trend_minute_no_change_and_span():
    row = {"stck_bsop_date": "20240503", "stck_cntg_hour": "131900", "elw_prpr": "40",
           "elw_oprc": "40", "lvrg_val": "-10.88", "gear": "19.57", "prmm_val": "5.1086",
           "invl_val": "17.00", "prit": "102.17", "acml_vol": "827720", "cntg_vol": "55700"}
    fake = FakeTransport(response=_resp([row]))
    point = _client(fake).elw("57K281").indicator_trend("minute", minutes=10)[0]
    assert fake.calls[0]["path"] == _IND_MINUTE
    assert fake.calls[0]["params"]["FID_HOUR_CLS_CODE"] == "600"     # 10분
    assert point.change is None                            # 분별은 전일대비 없음
    assert point.leverage == Decimal("-10.88")
    assert f"{point.timestamp:%Y%m%d %H%M%S}" == "20240503 131900"
    assert point._raw["prmm_val"] == "5.1086"              # 프리미엄은 _raw


def test_indicator_trend_rejects_tick():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).elw("57K281").indicator_trend("tick")


def test_indicator_trend_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).elw("57K281").indicator_trend("day")


# --- LP trade trend (output2, 순매수 property) -------------------------------
def _lp_row(bsop="20240516", price="35", vrss="0", sign="3", ctrt="0.00",
            seln="30030", seln_unpr="30", shnu="84810", shnu_unpr="34",
            hvol="7999900", hldn="99.99"):
    return {"stck_bsop_date": bsop, "elw_prpr": price, "prdy_vrss": vrss,
            "prdy_vrss_sign": sign, "prdy_ctrt": ctrt, "lp_seln_qty": seln,
            "lp_seln_avrg_unpr": seln_unpr, "lp_shnu_qty": shnu,
            "lp_shnu_avrg_unpr": shnu_unpr, "lp_hvol": hvol, "lp_hldn_rate": hldn}


def test_lp_trend_maps_flow_from_output2():
    fake = FakeTransport(response=_resp2([_lp_row()]))
    flows = _client(fake).elw("52K577").lp_trend()
    assert all(isinstance(f, ELWLPFlow) for f in flows)
    flow = flows[0]
    assert flow.code == "52K577"
    assert flow.lp_buy_quantity == 84810
    assert flow.lp_buy_avg_price == Decimal(34)
    assert flow.lp_sell_quantity == 30030
    assert flow.lp_sell_avg_price == Decimal(30)
    assert flow.lp_holding_quantity == 7999900
    assert flow.lp_holding_rate == Decimal("99.99")
    assert flow.net_quantity == 84810 - 30030             # 순매수 = 매수-매도
    assert f"{flow.timestamp:%Y%m%d}" == "20240516"
    call = fake.calls[0]
    assert call["path"] == _LP
    assert call["tr_id"] == "FHPEW03760000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "W"


def test_lp_trend_net_quantity_negative_when_lp_supplies():
    fake = FakeTransport(response=_resp2([_lp_row(seln="90000", shnu="10000")]))
    flow = _client(fake).elw("52K577").lp_trend()[0]
    assert flow.net_quantity == 10000 - 90000             # LP 순매도(공급) -> 음수


def test_lp_trend_skips_empty_rows():
    fake = FakeTransport(response=_resp2([_lp_row(), {"stck_bsop_date": ""}]))
    assert len(_client(fake).elw("52K577").lp_trend()) == 1


def test_lp_trend_missing_output2_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {"elw_prpr": "40"}})
    fake = FakeTransport(response=resp)
    with pytest.raises(KISError):
        _client(fake).elw("52K577").lp_trend()


def test_lp_trend_bad_quantity_fails_closed():
    fake = FakeTransport(response=_resp2([_lp_row(shnu="n/a")]))
    with pytest.raises(KISError):
        _client(fake).elw("52K577").lp_trend()
