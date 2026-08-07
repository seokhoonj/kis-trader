"""해외 선물/옵션 핸들 -- kis.overseas_futures/option(srs_cd).quote().

선물/옵션 URL·TR 라우팅(HHDFC55010000 / HHDFO55010000), output1 파싱(공백 패딩 값·통화·거래소·
만기·정산가·전일대비 부호 복원), optional 필드(None), fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import (
    Bar,
    KISClient,
    OverseasDerivativeQuote,
    OverseasFuturesOpenInterest,
    Trade,
)
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_FUT = "/uapi/overseas-futureoption/v1/quotations/inquire-price"
_OPT = "/uapi/overseas-futureoption/v1/quotations/opt-price"


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def _output(**over):
    # 값에 공백 패딩이 오는 실제 응답을 흉내낸다(Decimal/int 가 strip).
    out = {
        "open_price": "          75.55", "high_price": "          75.61",
        "low_price": "          74.66", "last_price": "          74.90",
        "prev_price": "          75.57", "sttl_price": "          74.88",
        "vol": "33004", "prev_diff_flag": "5", "prev_diff_price": "           0.67",
        "prev_diff_rate": "     -0.89", "bid_qntt": "         7", "bid_price": "          74.89",
        "ask_qntt": "         4", "ask_price": "          74.90", "trst_mgn": "               3670",
        "exch_cd": "ICE", "crc_cd": "USD", "expr_date": "20251219",
        "trd_to_date": "20251218", "remn_cnt": "41", "tick_size": "0.01",
        "tot_ask_qntt": "100", "tot_bid_qntt": "120",
    }
    out.update(over)
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": out})


def test_overseas_futures_quote_maps_and_routes():
    fake = FakeTransport(response=_output())
    quote = _client(fake).overseas_futures("BONU25").quote()
    assert isinstance(quote, OverseasDerivativeQuote)
    assert quote.symbol == "BONU25"
    assert quote.last == Decimal("74.90")                 # 공백 패딩 strip
    assert quote.open == Decimal("75.55")
    assert quote.previous_close == Decimal("75.57")
    assert quote.settlement_price == Decimal("74.88")
    assert quote.change == Decimal("-0.67")               # flag 5(하락) -> 음수
    assert quote.change_percent == Decimal("-0.89")
    assert quote.volume == 33004
    assert quote.bid == Decimal("74.89")
    assert quote.ask == Decimal("74.90")
    assert quote.bid_size == 7
    assert quote.currency == "USD"
    assert quote.exchange == "ICE"
    assert f"{quote.expiry_date:%Y%m%d}" == "20251219"
    assert quote.remaining_days == 41
    assert quote.tick_size == Decimal("0.01")
    assert quote.margin == Decimal(3670)
    call = fake.calls[0]
    assert call["path"] == _FUT
    assert call["tr_id"] == "HHDFC55010000"
    assert call["params"]["SRS_CD"] == "BONU25"


def test_overseas_option_quote_routes_to_opt_endpoint():
    fake = FakeTransport(response=_output())
    _client(fake).overseas_option("ESZ25 C5000").quote()
    assert fake.calls[0]["path"] == _OPT
    assert fake.calls[0]["tr_id"] == "HHDFO55010000"


def test_overseas_derivative_quote_positive_change():
    fake = FakeTransport(response=_output(prev_diff_flag="2", prev_diff_price="1.10",
                                          prev_diff_rate="1.47"))
    quote = _client(fake).overseas_futures("BONU25").quote()
    assert quote.change == Decimal("1.10")                # flag 2(상승) -> 양수
    assert quote.change_percent == Decimal("1.47")


def test_overseas_derivative_quote_optional_none():
    fake = FakeTransport(response=_output(sttl_price="", tick_size="", expr_date=""))
    quote = _client(fake).overseas_futures("BONU25").quote()
    assert quote.settlement_price is None
    assert quote.tick_size is None
    assert quote.expiry_date is None
    assert quote.last == Decimal("74.90")                 # 핵심 필드는 여전히 파싱


def test_overseas_derivative_quote_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).overseas_futures("BONU25").quote()


def test_overseas_derivative_quote_bad_value_fails_closed():
    fake = FakeTransport(response=_output(last_price="n/a"))
    with pytest.raises(KISError):
        _client(fake).overseas_futures("BONU25").quote()


def _history_row(date_text="20240423", time_text="164434", **over):
    row = {
        "data_date": date_text, "data_time": time_text, "open_price": "74.80",
        "high_price": "75.10", "low_price": "74.70", "last_price": "74.90",
        "last_qntt": "4", "vol": "27806", "prev_diff_flag": "5",
        "prev_diff_price": "0.67", "prev_diff_rate": "-0.89",
    }
    row.update(over)
    return row


def test_overseas_futures_daily_bars_maps_and_routes():
    response = RawResponse(
        rt_cd="0", msg_cd="X", msg1="ok",
        body={"output1": {}, "output2": [_history_row(time_text="")]},
    )
    fake = FakeTransport(response=response)
    bars = _client(fake).overseas_futures("BONU25").bars(
        exchange="ICE", interval="1d", max_bars=1
    )
    assert len(bars) == 1 and isinstance(bars[0], Bar)
    assert bars[0].symbol == "BONU25"
    assert bars[0].close == Decimal("74.90")
    assert bars[0].volume == 27806
    assert fake.calls[0]["path"].endswith("/daily-ccnl")
    assert fake.calls[0]["tr_id"] == "HHDFC55020100"
    assert fake.calls[0]["params"]["EXCH_CD"] == "ICE"
    assert fake.calls[0]["params"]["QRY_CNT"] == "1"
    assert len(fake.calls[0]["params"]["CLOSE_DATE_TIME"]) == 8


@pytest.mark.parametrize(
    ("market", "interval", "endpoint", "tr_id"),
    [
        ("future", "1m", "inquire-time-futurechartprice", "HHDFC55020400"),
        ("future", "1wk", "weekly-ccnl", "HHDFC55020000"),
        ("future", "1mo", "monthly-ccnl", "HHDFC55020300"),
        ("option", "1m", "inquire-time-optchartprice", "HHDFO55020400"),
        ("option", "1d", "opt-daily-ccnl", "HHDFO55020100"),
        ("option", "1wk", "opt-weekly-ccnl", "HHDFO55020000"),
        ("option", "1mo", "opt-monthly-ccnl", "HHDFO55020300"),
    ],
)
def test_overseas_derivative_bars_routes_all_period_endpoints(
    market, interval, endpoint, tr_id
):
    row = _history_row(time_text="164434" if interval == "1m" else "")
    body = {"output1": [row], "output2": {}} if interval == "1m" else {
        "output1": {}, "output2": [row],
    }
    fake = FakeTransport(
        response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body=body)
    )
    handle = (
        _client(fake).overseas_futures("BONU25")
        if market == "future"
        else _client(fake).overseas_option("BONU25 C75")
    )
    bars = handle.bars(exchange="ICE", interval=interval, max_bars=1)
    assert len(bars) == 1
    assert fake.calls[0]["path"].endswith("/" + endpoint)
    assert fake.calls[0]["tr_id"] == tr_id
    assert fake.calls[0]["params"]["QRY_GAP"] == ("1" if interval == "1m" else "")


@pytest.mark.parametrize(
    ("market", "endpoint", "tr_id"),
    [
        ("future", "tick-ccnl", "HHDFC55020200"),
        ("option", "opt-tick-ccnl", "HHDFO55020200"),
    ],
)
def test_overseas_derivative_trades_maps_and_routes(market, endpoint, tr_id):
    response = RawResponse(
        rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}, "output2": [
            _history_row(time_text="164500", last_qntt="2"),
            _history_row(time_text="164434", last_qntt="4"),
        ]},
    )
    fake = FakeTransport(response=response)
    handle = (
        _client(fake).overseas_futures("BONU25")
        if market == "future"
        else _client(fake).overseas_option("BONU25 C75")
    )
    trades = handle.trades(exchange="ICE", max_trades=2)
    assert all(isinstance(trade, Trade) for trade in trades)
    assert [trade.quantity for trade in trades] == [4, 2]
    assert trades[0].change == Decimal("-0.67")
    assert fake.calls[0]["path"].endswith("/" + endpoint)
    assert fake.calls[0]["tr_id"] == tr_id


def test_overseas_derivative_history_rejects_invalid_inputs_before_transport():
    fake = FakeTransport(response=None)
    handle = _client(fake).overseas_futures("BONU25")
    with pytest.raises(KISUsageError):
        handle.bars(exchange="", interval="1d")
    with pytest.raises(KISUsageError):
        handle.bars(exchange="ICE", interval="1d", max_bars=41)
    with pytest.raises(KISUsageError):
        handle.trades(exchange="ICE", max_trades=41)
    assert fake.calls == []


def test_overseas_derivative_history_missing_rows_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).overseas_futures("BONU25").bars(exchange="ICE")


def _open_interest_row(**over):
    row = {
        "prod_iscd": "ES", "cftc_iscd": "13874A", "bsop_date": "20240611",
        "bidp_spec": "270380", "askp_spec": "381794", "spread_spec": "0",
        "bidp_hedge": "1606798", "askp_hedge": "1617849", "hts_otst_smtn": "2266096",
        "bidp_missing": "297310", "askp_missing": "174845",
        "bidp_spec_cust": "100", "askp_spec_cust": "90", "spread_spec_cust": "10",
        "bidp_hedge_cust": "80", "askp_hedge_cust": "70", "cust_smtn": "350",
    }
    row.update(over)
    return row


def test_overseas_futures_open_interest_maps_and_routes():
    response = RawResponse(
        rt_cd="0", msg_cd="X", msg1="ok",
        body={"output1": {"row_cnt": "1"}, "output2": [_open_interest_row()]},
    )
    fake = FakeTransport(response=response)
    points = _client(fake).overseas_futures_open_interest(
        "ES", as_of="2024-06-24"
    )
    assert len(points) == 1 and isinstance(points[0], OverseasFuturesOpenInterest)
    point = points[0]
    assert point.product == "ES"
    assert point.speculative_long == 270380
    assert point.hedging_short == 1617849
    assert point.total_open_interest == 2266096
    assert point.customer_total == 350
    assert fake.calls[0] == {
        "path": "/uapi/overseas-futureoption/v1/quotations/investor-unpd-trend",
        "tr_id": "HHDDB95030000",
        "params": {
            "PROD_ISCD": "ES", "BSOP_DATE": "20240624",
            "UPMU_GUBUN": "0", "CTS_KEY": "",
        },
    }


def test_overseas_futures_open_interest_change_mode_and_sort():
    response = RawResponse(
        rt_cd="0", msg_cd="X", msg1="ok", body={"output2": [
            _open_interest_row(bsop_date="20240612", bidp_spec="-5"),
            _open_interest_row(bsop_date="20240611", bidp_spec="-3"),
        ]},
    )
    fake = FakeTransport(response=response)
    points = _client(fake).overseas_futures_open_interest(
        "ES", as_of="20240624", mode="change"
    )
    assert [point.speculative_long for point in points] == [-3, -5]
    assert fake.calls[0]["params"]["UPMU_GUBUN"] == "1"


def test_overseas_futures_open_interest_rejects_invalid_input_before_transport():
    fake = FakeTransport(response=None)
    with pytest.raises(KISUsageError):
        _client(fake).overseas_futures_open_interest("", as_of="20240624")
    with pytest.raises(KISUsageError):
        _client(fake).overseas_futures_open_interest("ES", as_of="20240624", mode="bad")
    assert fake.calls == []


def test_overseas_futures_open_interest_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).overseas_futures_open_interest("ES", as_of="20240624")
