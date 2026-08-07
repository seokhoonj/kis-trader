"""채권 핸들 -- kis.bond(code).quote().

시장구분 B 라우팅, output 파싱(가격·시고저·전일대비·수익률), 전일대비 부호 복원,
optional 수익률(None), fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import Bar, BondQuote, BondValuation, KISClient, OrderBook, Trade
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_PRICE = "/uapi/domestic-bond/v1/quotations/inquire-price"
_ASKING = "/uapi/domestic-bond/v1/quotations/inquire-asking-price"
_CCNL = "/uapi/domestic-bond/v1/quotations/inquire-ccnl"
_BARS = "/uapi/domestic-bond/v1/quotations/inquire-daily-itemchartprice"
_VALUATIONS = "/uapi/domestic-bond/v1/quotations/avg-unit"


def _output(*, prpr="10250.0", oprc="10240.0", hgpr="10260.0", lwpr="10235.0",
            clpr="10230.0", vrss="20.0", sign="2", ctrt="0.20", vol="1500",
            ernn="3.85"):
    return {"hts_kor_isnm": "국고03750-3312", "bond_prpr": prpr, "bond_oprc": oprc,
            "bond_hgpr": hgpr, "bond_lwpr": lwpr, "bond_prdy_clpr": clpr,
            "bond_prdy_vrss": vrss, "prdy_vrss_sign": sign, "prdy_ctrt": ctrt,
            "acml_vol": vol, "ernn_rate": ernn}


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


def test_bond_quote_maps_fields_and_market():
    fake = FakeTransport(response=_resp(_output()))
    quote = _client(fake).bond("KR2033022D33").quote()
    assert isinstance(quote, BondQuote)
    assert quote.code == "KR2033022D33"
    assert quote.price == Decimal("10250.0")
    assert quote.open == Decimal("10240.0")
    assert quote.high == Decimal("10260.0")
    assert quote.low == Decimal("10235.0")
    assert quote.previous_close == Decimal("10230.0")
    assert quote.change == Decimal("20.0")
    assert quote.change_percent == Decimal("0.20")
    assert quote.volume == 1500
    assert quote.yield_rate == Decimal("3.85")             # 수익률(%)
    call = fake.calls[0]
    assert call["path"] == _PRICE
    assert call["tr_id"] == "FHKBJ773400C0"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "B"   # 채권
    assert call["params"]["FID_INPUT_ISCD"] == "KR2033022D33"


def test_bond_quote_negative_change():
    fake = FakeTransport(response=_resp(_output(vrss="15.0", sign="5", ctrt="0.15")))
    quote = _client(fake).bond("KR2033022D33").quote()
    assert quote.change == Decimal("-15.0")                 # 하락 -> 음수
    assert quote.change_percent == Decimal("-0.15")


def test_bond_quote_optional_yield_none():
    fake = FakeTransport(response=_resp(_output(ernn="")))
    quote = _client(fake).bond("KR2033022D33").quote()
    assert quote.yield_rate is None
    assert quote.price == Decimal("10250.0")               # 핵심 필드는 여전히 파싱


def test_bond_quote_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).bond("KR2033022D33").quote()


def test_bond_quote_bad_value_fails_closed():
    fake = FakeTransport(response=_resp(_output(prpr="n/a")))
    with pytest.raises(KISError):
        _client(fake).bond("KR2033022D33").quote()


# --- order_book ------------------------------------------------------------
def _book(**over):
    out = {
        "bond_bidp1": "10230.0", "bond_bidp2": "10225.0", "bond_bidp3": "10220.0",
        "bond_bidp4": "0", "bond_bidp5": "0",
        "bidp_rsqn1": "100", "bidp_rsqn2": "200", "bidp_rsqn3": "300",
        "bidp_rsqn4": "0", "bidp_rsqn5": "0",
        "bond_askp1": "10250.0", "bond_askp2": "10255.0", "bond_askp3": "0",
        "bond_askp4": "0", "bond_askp5": "0",
        "askp_rsqn1": "150", "askp_rsqn2": "250", "askp_rsqn3": "0",
        "askp_rsqn4": "0", "askp_rsqn5": "0",
        "total_bidp_rsqn": "600", "total_askp_rsqn": "400",
    }
    out.update(over)
    return out


def test_bond_order_book_maps_levels_and_market():
    fake = FakeTransport(response=_resp(_book()))
    book = _client(fake).bond("KR2033022D33").order_book()
    assert isinstance(book, OrderBook)
    assert book.symbol == "KR2033022D33"
    assert book.market == "B"
    assert [(lvl.price, lvl.quantity) for lvl in book.bids] == [
        (Decimal("10230.0"), 100), (Decimal("10225.0"), 200), (Decimal("10220.0"), 300),
    ]                                                          # 0-가격 단계는 skip
    assert [(lvl.price, lvl.quantity) for lvl in book.asks] == [
        (Decimal("10250.0"), 150), (Decimal("10255.0"), 250),
    ]
    assert book.total_bid_quantity == 600
    assert book.total_ask_quantity == 400
    call = fake.calls[0]
    assert call["path"] == _ASKING
    assert call["tr_id"] == "FHKBJ773401C0"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "B"


def test_bond_order_book_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).bond("KR2033022D33").order_book()


# --- trades ----------------------------------------------------------------
def _trade_rows():
    return [
        {"stck_cntg_hour": "101530", "bond_prpr": "10250.0", "cntg_vol": "50",
         "bond_prdy_vrss": "20.0", "prdy_vrss_sign": "2", "prdy_ctrt": "0.20"},
        {"stck_cntg_hour": "101500", "bond_prpr": "10245.0", "cntg_vol": "30",
         "bond_prdy_vrss": "15.0", "prdy_vrss_sign": "5", "prdy_ctrt": "0.15"},
        {"stck_cntg_hour": "", "bond_prpr": ""},                 # 빈 행은 건너뜀
    ]


def test_bond_trades_maps_rows_and_market():
    fake = FakeTransport(response=_resp(_trade_rows()))
    trades = _client(fake).bond("KR2033022D33").trades()
    assert all(isinstance(t, Trade) for t in trades)
    assert len(trades) == 2                                    # 빈 행 제외
    first, second = trades
    assert first.symbol == "KR2033022D33"
    assert first.price == Decimal("10250.0")
    assert first.quantity == 50
    assert first.change == Decimal("20.0")
    assert first.change_percent == Decimal("0.20")
    assert first.timestamp.hour == 10 and first.timestamp.minute == 15
    assert second.change == Decimal("-15.0")                   # 하락 부호 복원
    assert second.change_percent == Decimal("-0.15")
    call = fake.calls[0]
    assert call["path"] == _CCNL
    assert call["tr_id"] == "FHKBJ773403C0"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "B"


def test_bond_trades_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).bond("KR2033022D33").trades()


def test_bond_trades_bad_value_fails_closed():
    rows = [{"stck_cntg_hour": "101530", "bond_prpr": "10250.0", "cntg_vol": "n/a",
             "bond_prdy_vrss": "20.0", "prdy_vrss_sign": "2", "prdy_ctrt": "0.20"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).bond("KR2033022D33").trades()


# --- bars ------------------------------------------------------------------
def _bar_rows():
    return [
        {"stck_bsop_date": "20240610", "bond_oprc": "0.00", "bond_hgpr": "0.00",
         "bond_lwpr": "0.00", "bond_prpr": "10997.10", "acml_vol": "0"},
        {"stck_bsop_date": "20240607", "bond_oprc": "10997.10", "bond_hgpr": "10997.10",
         "bond_lwpr": "10997.10", "bond_prpr": "10997.10", "acml_vol": "119"},
        {"stck_bsop_date": "", "bond_prpr": ""},
    ]


def test_bond_bars_maps_ledger_rows_and_sorts_oldest_first():
    fake = FakeTransport(response=_resp(_bar_rows()))
    bars = _client(fake).bond("KR101501D967").bars()
    assert all(isinstance(bar, Bar) for bar in bars)
    assert [bar.timestamp.strftime("%Y%m%d") for bar in bars] == ["20240607", "20240610"]
    assert bars[0].symbol == "KR101501D967"
    assert bars[0].open == Decimal("10997.10")
    assert bars[0].high == Decimal("10997.10")
    assert bars[0].low == Decimal("10997.10")
    assert bars[0].close == Decimal("10997.10")
    assert bars[0].volume == 119
    call = fake.calls[0]
    assert call["path"] == _BARS
    assert call["tr_id"] == "FHKBJ773701C0"
    assert call["params"] == {
        "FID_COND_MRKT_DIV_CODE": "B",
        "FID_INPUT_ISCD": "KR101501D967",
    }


def test_bond_bars_rejects_unsupported_interval_before_transport():
    fake = FakeTransport(response=_resp(_bar_rows()))
    with pytest.raises(KISUsageError):
        _client(fake).bond("KR101501D967").bars("1wk")
    assert fake.calls == []


def test_bond_bars_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).bond("KR101501D967").bars()


def test_bond_bars_non_mapping_row_fails_closed():
    fake = FakeTransport(response=_resp(["bad-row"]))
    with pytest.raises(KISError):
        _client(fake).bond("KR101501D967").bars()


# --- valuations ------------------------------------------------------------
def _valuation_rows():
    base = {
        "pdno": "KR2033022D33", "prdt_name": "충북지역개발채권23-03",
        "kis_unpr": "9793.13", "kbp_unpr": "9799.81", "nice_evlu_unpr": "9809.20",
        "fnp_unpr": "9804.69", "avg_evlu_unpr": "9801.70",
        "kis_erng_rt": "3.703", "kbp_erng_rt": "3.685",
        "nice_evlu_erng_rt": "3.660", "fnp_erng_rt": "3.672",
        "avg_evlu_erng_rt": "3.680", "kis_crdt_grad_text": "AA+",
        "kbp_crdt_grad_text": "AA+", "nice_crdt_grad_text": "AA+",
        "fnp_crdt_grad_text": "AA+", "kis_rf_unpr": "0.00", "kbp_rf_unpr": "0.00",
        "nice_evlu_rf_unpr": "0.00", "chng_yn": "N",
    }
    return [
        {**base, "evlu_dt": "20240410"},
        {**base, "evlu_dt": "20240409", "avg_evlu_unpr": "9800.75", "chng_yn": "Y"},
        {"evlu_dt": "", "avg_evlu_unpr": ""},
    ]


def test_bond_valuations_maps_rows_and_sorts_oldest_first():
    response = RawResponse(
        rt_cd="0", msg_cd="KIOK0500", msg1="정상", body={"output1": _valuation_rows()}
    )
    fake = FakeTransport(response=response)
    valuations = _client(fake).bond("KR2033022D33").valuations(
        start="2024-04-01", end="20240410"
    )
    assert all(isinstance(valuation, BondValuation) for valuation in valuations)
    assert [valuation.date.isoformat() for valuation in valuations] == [
        "2024-04-09", "2024-04-10",
    ]
    first = valuations[0]
    assert first.code == "KR2033022D33"
    assert first.average_price == Decimal("9800.75")
    assert first.average_yield == Decimal("3.680")
    assert first.agency_prices["NICE"] == Decimal("9809.20")
    assert first.agency_yields["FNP"] == Decimal("3.672")
    assert first.credit_ratings["KIS"] == "AA+"
    assert first.risk_free_prices["KBP"] == Decimal("0.00")
    assert first.changed is True
    call = fake.calls[0]
    assert call["path"] == _VALUATIONS
    assert call["tr_id"] == "CTPF2005R"
    assert call["params"] == {
        "INQR_STRT_DT": "20240401", "INQR_END_DT": "20240410",
        "PDNO": "KR2033022D33", "PRDT_TYPE_CD": "302", "VRFC_KIND_CD": "00",
        "CTX_AREA_NK30": "", "CTX_AREA_FK100": "",
    }


def test_bond_valuations_rejects_reversed_dates_before_transport():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).bond("KR2033022D33").valuations(start="20240411", end="20240410")
    assert fake.calls == []


def test_bond_valuations_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).bond("KR2033022D33").valuations(start="20240401", end="20240410")


def test_bond_valuations_non_mapping_row_fails_closed():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": ["bad-row"]})
    fake = FakeTransport(response=response)
    with pytest.raises(KISError):
        _client(fake).bond("KR2033022D33").valuations(start="20240401", end="20240410")
