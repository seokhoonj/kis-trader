"""ETF/ETN 고유 정보 -- kis.domestic.stock(code).nav().

ETF 는 종목처럼 거래되므로 시세/주문은 일반 verb 로 하고, NAV/괴리율/추적오차만 별도. etfetn 세그먼트
경로, NAV 전일대비 부호 복원, fail-closed 파싱을 가짜 전송으로 검증.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import (
    ETFNAV,
    ETFNAVComparison,
    ETFNAVMinutePoint,
    ETFOrderBook,
    KISClient,
)
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_ETF_NAV = "/uapi/etfetn/v1/quotations/inquire-price"


def _output(*, nav="36110.50", nav_change="95.20", nav_sign="2", nav_pct="0.26",
            prev_nav="36015.30", disparity="-0.06", trc_err="0.03", net_assets="4200000000000"):
    return {"stck_prpr": "36090", "prdy_vrss_sign": "2", "prdy_vrss": "110", "prdy_ctrt": "0.31",
            "acml_vol": "1200000", "nav": nav, "nav_prdy_vrss": nav_change,
            "nav_prdy_vrss_sign": nav_sign, "nav_prdy_ctrt": nav_pct, "prdy_last_nav": prev_nav,
            "dprt": disparity, "trc_errt": trc_err, "etf_ntas_ttam": net_assets}


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
    return KISClient(app_key="k", app_secret="s", transport=transport)


def test_nav_maps_fields_and_params():
    fake = FakeTransport(response=_resp(_output()))
    nav = _client(fake).domestic.stock("069500").nav()
    assert isinstance(nav, ETFNAV)
    assert nav.symbol == "069500"
    assert nav.nav == Decimal("36110.50")
    assert nav.nav_change == Decimal("95.20")
    assert nav.nav_change_percent == Decimal("0.26")
    assert nav.previous_nav == Decimal("36015.30")
    assert nav.disparity_rate == Decimal("-0.06")
    assert nav.tracking_error == Decimal("0.03")
    assert nav.net_assets == Decimal(4200000000000)
    call = fake.calls[0]
    assert call["path"] == _ETF_NAV
    assert call["tr_id"] == "FHPST02400000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "J"
    assert call["params"]["FID_INPUT_ISCD"] == "069500"


def test_nav_negative_change_sign_restored():
    fake = FakeTransport(response=_resp(_output(nav_change="80.00", nav_sign="5", nav_pct="0.22")))
    nav = _client(fake).domestic.stock("069500").nav()
    assert nav.nav_change == Decimal("-80.00")                   # 하락 -> 음수
    assert nav.nav_change_percent == Decimal("-0.22")


def test_nav_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("069500").nav()


def test_nav_error_response_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="1", msg_cd="X", msg1="실패", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("069500").nav()


def test_nav_bad_value_fails_closed():
    fake = FakeTransport(response=_resp(_output(nav="n/a")))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("069500").nav()


_ETF_COMPONENTS = "/uapi/etfetn/v1/quotations/inquire-component-stock-price"


def _component_row(symbol="005930", name="삼성전자", price="72700", change="400", sign="2",
                   pct="0.55", weight="28.9", valuation="1210000000"):
    return {"stck_shrn_iscd": symbol, "hts_kor_isnm": name, "stck_prpr": price,
            "prdy_vrss": change, "prdy_vrss_sign": sign, "prdy_ctrt": pct,
            "etf_cnfg_issu_rlim": weight, "etf_vltn_amt": valuation}


def _components_summary(*, price="37195", change="-365", sign="5", pct="-0.97",
                        market_cap="18415000000000", nav="37200.50", nav_change="120.30",
                        nav_sign="2", nav_pct="0.32", net_assets="4200000000000",
                        prev_nav="37080.20", nav_open="37150.00", nav_high="37250.00",
                        nav_low="37020.00", cu_shares="50000", component_count="200"):
    return {"stck_prpr": price, "prdy_vrss": change, "prdy_vrss_sign": sign, "prdy_ctrt": pct,
            "etf_cnfg_issu_avls": market_cap, "nav": nav, "nav_prdy_vrss": nav_change,
            "nav_prdy_vrss_sign": nav_sign, "nav_prdy_ctrt": nav_pct,
            "etf_ntas_ttam": net_assets, "prdy_clpr_nav": prev_nav, "oprc_nav": nav_open,
            "hprc_nav": nav_high, "lprc_nav": nav_low, "etf_cu_unit_scrt_cnt": cu_shares,
            "etf_cnfg_issu_cnt": component_count}


def _components_resp(rows, *, summary=None):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": summary or _components_summary(), "output2": rows})


def test_components_maps_fields_summary_and_params():
    from kis_trader import ETFComponent, ETFComponents, ETFComponentsSummary
    rows = [_component_row(), _component_row(symbol="000660", name="SK하이닉스")]
    fake = FakeTransport(response=_components_resp(rows))
    result = _client(fake).domestic.stock("069500").etf_components()
    assert isinstance(result, ETFComponents)
    # PARITY: .components tuple equals the former bare-list return element-for-element.
    from kis_trader.domestic._engine.etf import _parse_etf_components
    assert list(result.components) == _parse_etf_components(rows)
    assert isinstance(result.components, tuple)
    assert [c.symbol for c in result.components] == ["005930", "000660"]
    first = result.components[0]
    assert isinstance(first, ETFComponent)
    assert first.name == "삼성전자"
    assert first.price == Decimal(72700)
    assert first.weight == Decimal("28.9")
    assert first.market_value == Decimal(1210000000)
    # DELTA: the previously-discarded output1 summary is now reachable and typed.
    assert isinstance(result.summary, ETFComponentsSummary)
    assert result.summary.price == Decimal(37195)
    assert result.summary.change == Decimal(-365)               # sign 5 -> 하락 -> 음수
    assert result.summary.nav == Decimal("37200.50")
    assert result.summary.nav_change == Decimal("120.30")
    assert result.summary.net_assets == Decimal(4200000000000)
    assert result.summary.cu_unit_shares == 50000
    assert result.summary.component_count == 200
    call = fake.calls[0]
    assert call["path"] == _ETF_COMPONENTS
    assert call["tr_id"] == "FHKST121600C0"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "11216"
    assert call["params"]["FID_INPUT_ISCD"] == "069500"


def test_components_negative_change_sign_restored():
    fake = FakeTransport(response=_components_resp([_component_row(change="300", sign="5")]))
    result = _client(fake).domestic.stock("069500").etf_components()
    assert result.components[0].change == Decimal(-300)          # 하락 -> 음수


def test_components_missing_output2_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output1": _components_summary()}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("069500").etf_components()


def test_components_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output2": [_component_row()]}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("069500").etf_components()


def test_components_malformed_output1_fails_closed():
    # output1 present but missing required summary fields -> fail closed on parse.
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output1": {"stck_prpr": "1"},
                                                    "output2": [_component_row()]}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("069500").etf_components()


_ETF_NAV_HISTORY = "/uapi/etfetn/v1/quotations/nav-comparison-daily-trend"


def _nav_hist_row(date_text, close, nav, nav_change, sign, nav_pct, disparity):
    return {"stck_bsop_date": date_text, "stck_clpr": close, "nav": nav,
            "nav_prdy_vrss": nav_change, "nav_prdy_vrss_sign": sign, "nav_prdy_ctrt": nav_pct,
            "dprt": disparity}


def _nav_hist_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def test_nav_history_maps_fields_sorted_and_params():
    from datetime import date as _date

    from kis_trader import ETFNAVHistoryPoint
    fake = FakeTransport(response=_nav_hist_resp([
        _nav_hist_row("20240104", "36090", "36110", "95", "2", "0.26", "-0.06"),
        _nav_hist_row("20240103", "35980", "36015", "40", "2", "0.11", "-0.10"),
    ]))
    points = _client(fake).domestic.stock("069500").nav_history(start="20240103", end="20240104")
    assert [p.trading_date for p in points] == [_date(2024, 1, 3), _date(2024, 1, 4)]   # 오름차순
    assert all(isinstance(p, ETFNAVHistoryPoint) for p in points)
    assert points[-1].close == Decimal(36090)
    assert points[-1].nav == Decimal(36110)
    assert points[-1].disparity_rate == Decimal("-0.06")
    call = fake.calls[0]
    assert call["path"] == _ETF_NAV_HISTORY
    assert call["tr_id"] == "FHPST02440200"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240103"
    assert call["params"]["FID_INPUT_DATE_2"] == "20240104"


def test_nav_history_negative_nav_change_and_date_objects():
    from datetime import date as _date
    fake = FakeTransport(response=_nav_hist_resp([
        _nav_hist_row("20240104", "36090", "36110", "95", "5", "0.26", "-0.06")]))
    points = _client(fake).domestic.stock("069500").nav_history(
        start=_date(2024, 1, 1), end=_date(2024, 1, 4)
    )
    assert points[0].nav_change == Decimal(-95)                  # 하락 -> 음수
    assert fake.calls[0]["params"]["FID_INPUT_DATE_1"] == "20240101"


def test_nav_history_start_after_end_raises():
    fake = FakeTransport(response=_nav_hist_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("069500").nav_history(start="20240104", end="20240103")


def test_nav_history_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("069500").nav_history(start="20240101", end="20240104")


def test_nav_comparison_maps_price_and_nav_ohlc():
    response = RawResponse(
        rt_cd="0", msg_cd="MCA00000", msg1="정상",
        body={
            "output1": {
                "stck_prpr": "36150", "stck_prdy_clpr": "36000", "stck_oprc": "36020",
                "stck_hgpr": "36200", "stck_lwpr": "35980", "prdy_vrss": "150",
                "prdy_vrss_sign": "2", "prdy_ctrt": "0.42", "acml_vol": "1000000",
                "acml_tr_pbmn": "36100000000",
            },
            "output2": {
                "nav": "36110.50", "prdy_clpr_nav": "36015.30", "oprc_nav": "36025.00",
                "hprc_nav": "36180.00", "lprc_nav": "35990.00", "nav_prdy_vrss": "95.20",
                "nav_prdy_vrss_sign": "5", "nav_prdy_ctrt": "0.26",
            },
        },
    )
    fake = FakeTransport(response=response)
    comparison = _client(fake).domestic.stock("069500").nav_comparison()
    assert isinstance(comparison, ETFNAVComparison)
    assert comparison.price == Decimal(36150)
    assert comparison.nav == Decimal("36110.50")
    assert comparison.nav_change == Decimal("-95.20")
    assert fake.calls[0]["path"] == "/uapi/etfetn/v1/quotations/nav-comparison-trend"
    assert fake.calls[0]["tr_id"] == "FHPST02440000"


def test_nav_intraday_maps_sorted_points_and_interval():
    rows = [
        {"bsop_hour": "101000", "nav": "36110", "nav_prdy_vrss_sign": "2",
         "nav_prdy_vrss": "100", "nav_prdy_ctrt": "0.28", "nav_vrss_prpr": "40",
         "dprt": "0.11", "stck_prpr": "36150", "prdy_vrss": "150",
         "prdy_vrss_sign": "2", "prdy_ctrt": "0.42", "acml_vol": "10000",
         "cntg_vol": "200"},
        {"bsop_hour": "100700", "nav": "36100", "nav_prdy_vrss_sign": "5",
         "nav_prdy_vrss": "90", "nav_prdy_ctrt": "0.25", "nav_vrss_prpr": "35",
         "dprt": "0.10", "stck_prpr": "36135", "prdy_vrss": "135",
         "prdy_vrss_sign": "2", "prdy_ctrt": "0.38", "acml_vol": "9800",
         "cntg_vol": "180"},
    ]
    fake = FakeTransport(response=_nav_hist_resp(rows))
    points = _client(fake).domestic.stock("069500").nav_intraday(interval_minutes=3)
    assert all(isinstance(point, ETFNAVMinutePoint) for point in points)
    assert [f"{point.timestamp:%H%M%S}" for point in points] == ["100700", "101000"]
    assert points[0].nav_change == Decimal(-90)
    assert points[-1].interval_volume == 200
    assert fake.calls[0]["params"] == {
        "fid_hour_cls_code": "180", "fid_cond_mrkt_div_code": "E",
        "fid_input_iscd": "069500",
    }


@pytest.mark.parametrize("minutes", [0, 121])
def test_nav_intraday_rejects_bad_interval_before_transport(minutes):
    fake = FakeTransport(response=_nav_hist_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("069500").nav_intraday(interval_minutes=minutes)
    assert fake.calls == []


def test_etf_order_book_maps_standard_and_lp_levels():
    output = {
        "aspr_acpt_hour": "101530",
        "total_askp_rsqn": "5500", "total_bidp_rsqn": "6500",
        "total_askp_rsqn_icdc": "-100", "total_bidp_rsqn_icdc": "200",
        "lp_total_askp_rsqn": "1200", "lp_total_bidp_rsqn": "1300",
        "mid_prc": "36125", "midp_total_rsqn": "400", "midp_cls_code": "1",
    }
    for position in range(1, 11):
        output[f"askp{position}"] = str(36150 + position * 5)
        output[f"bidp{position}"] = str(36150 - position * 5)
        output[f"askp_rsqn{position}"] = str(position * 100)
        output[f"bidp_rsqn{position}"] = str(position * 110)
        output[f"askp_rsqn_icdc{position}"] = str(-position)
        output[f"bidp_rsqn_icdc{position}"] = str(position)
        output[f"lp_askp_rsqn{position}"] = str(position * 10)
        output[f"lp_bidp_rsqn{position}"] = str(position * 11)
    fake = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": output}
    ))
    book = _client(fake).domestic.stock("069500").etf_order_book()

    assert isinstance(book, ETFOrderBook)
    assert len(book.order_book.asks) == 10
    assert book.order_book.asks[0].price == Decimal(36155)
    assert book.lp_asks[0].quantity == 10
    assert book.lp_bids[-1].quantity == 110
    assert book.ask_quantity_changes[0] == -1
    assert book.total_bid_quantity_change == 200
    assert book.midpoint == Decimal(36125)
    assert f"{book.order_book.as_of:%H%M%S}" == "101530"
    assert fake.calls[0]["path"] == "/uapi/etfetn/v1/quotations/inquire-asking-price"
    assert fake.calls[0]["tr_id"] == "FHPST02400200"


def test_etf_order_book_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("069500").etf_order_book()
