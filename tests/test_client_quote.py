"""새 행위중심 API 첫 수직 -- KisClient + Ticker + quote.

kis.ticker("005930").quote() 엔드투엔드(FakeTransport), 시장 자동판별, 계좌 파싱, transport
주입, fail-closed 파싱, 전일대비 부호, 값 의미론을 네트워크 없이 검증한다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_openapi import Bar, KisClient, OrderBook, PriceLevel, Quote
from kis_openapi.errors import KisError, KisUsageError
from kis_openapi.transport import RawResponse

_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"

_QUOTE_OUTPUT = {
    "stck_prpr": "71500", "stck_oprc": "70800", "stck_hgpr": "71800", "stck_lwpr": "70600",
    "stck_sdpr": "70900", "prdy_vrss": "600", "prdy_vrss_sign": "2", "prdy_ctrt": "0.85",
    "acml_vol": "12345678", "w52_hgpr": "88000", "w52_lwpr": "49900",
}


class FakeTransport:
    """읽기 전용 가짜 전송. 기본 응답/경로별 순차 응답(by_path 리스트)/예외, 모든 호출 기록."""

    def __init__(self, *, response=None, by_path=None, raises=None):
        self.response = response
        self.by_path = by_path or {}
        self.raises = raises
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent})
        if path in self.by_path:
            outcome = self.by_path[path]
            if isinstance(outcome, list):
                outcome = outcome.pop(0)
            return outcome
        if self.raises is not None:
            raise self.raises
        assert self.response is not None, "FakeTransport 에 응답을 줘야 한다"
        return self.response


def _quote_resp(output=None):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output": output if output is not None else dict(_QUOTE_OUTPUT)})


def _client(transport):
    return KisClient(app_key="k", app_secret="s", account="12345678-01", transport=transport)


def test_ticker_quote_returns_unified_quote():
    quote = _client(FakeTransport(response=_quote_resp())).ticker("005930").quote()
    assert isinstance(quote, Quote)
    assert quote.symbol == "005930"
    assert quote.market == "KRX"
    assert quote.currency == "KRW"
    assert quote.last == Decimal(71500)
    assert quote.open == Decimal(70800)
    assert quote.previous_close == Decimal(70900)
    assert quote.change == Decimal(600)           # sign 2 상승 -> 양수
    assert quote.change_percent == Decimal("0.85")
    assert quote.volume == 12345678
    assert quote.week_52_high == Decimal(88000)
    assert quote.as_of.tzinfo is not None


def test_ticker_defaults_to_domestic_krx():
    fake = FakeTransport(response=_quote_resp())
    _client(fake).ticker("005930").quote()
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["idempotent"] is True
    assert call["path"] == _QUOTE_PATH
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "J"
    assert call["params"]["FID_INPUT_ISCD"] == "005930"


def test_ticker_market_override_to_nextrade():
    fake = FakeTransport(response=_quote_resp())
    _client(fake).ticker("005930", market="NXT").quote()
    assert fake.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"] == "NX"


def test_overseas_symbol_not_yet_supported():
    with pytest.raises(KisUsageError):        # 영문 심볼 -> 해외 미지원 안내
        _client(FakeTransport(response=_quote_resp())).ticker("AAPL")


def test_quote_change_negative_on_down_sign():
    output = dict(_QUOTE_OUTPUT, prdy_vrss_sign="5", prdy_vrss="600", prdy_ctrt="0.85")
    quote = _client(FakeTransport(response=_quote_resp(output))).ticker("005930").quote()
    assert quote.change == Decimal(-600)
    assert quote.change_percent == Decimal("-0.85")


def test_quote_week52_absent_is_none():
    output = dict(_QUOTE_OUTPUT, w52_hgpr="", w52_lwpr="  ")
    quote = _client(FakeTransport(response=_quote_resp(output))).ticker("005930").quote()
    assert quote.week_52_high is None
    assert quote.week_52_low is None


def test_quote_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="MCA05918", msg1="종목코드 오류", body={})
    with pytest.raises(KisError):
        _client(FakeTransport(response=resp)).ticker("005930").quote()


def test_quote_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={})
    with pytest.raises(KisError):
        _client(FakeTransport(response=resp)).ticker("005930").quote()


def test_quote_unparseable_price_fails_closed():
    output = dict(_QUOTE_OUTPUT, stck_prpr="N/A")
    with pytest.raises(KisError):
        _client(FakeTransport(response=_quote_resp(output))).ticker("005930").quote()


def test_quote_value_semantics_ignore_raw_and_hashable():
    when = datetime(2024, 1, 2, tzinfo=timezone(timedelta(hours=9)))   # as_of 고정
    fields = {
        "symbol": "005930", "market": "KRX", "currency": "KRW",
        "last": Decimal(1), "open": Decimal(1), "high": Decimal(1), "low": Decimal(1),
        "previous_close": Decimal(1), "change": Decimal(0), "change_percent": Decimal(0),
        "volume": 1, "week_52_high": None, "week_52_low": None, "as_of": when,
    }
    first = Quote(**fields, _raw={"a": "b"})
    second = Quote(**fields, _raw={"z": "1"})
    assert first == second                # 파싱 값 같으면 같다(raw 무시)
    assert hash(first) == hash(second)
    assert {first, second} == {first}


# --- bars ------------------------------------------------------------------
_BARS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"


def _bar_row(date_text, open_price, high_price, low_price, close_price, volume):
    return {"stck_bsop_date": date_text, "stck_oprc": open_price, "stck_hgpr": high_price,
            "stck_lwpr": low_price, "stck_clpr": close_price, "acml_vol": volume}


def _bars_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": rows})


def test_ticker_bars_parses_ascending():
    rows = [
        _bar_row("20240104", "70000", "70500", "69800", "70200", "1000"),
        _bar_row("20240103", "69500", "70100", "69400", "70000", "1100"),
        _bar_row("20240102", "69000", "69600", "68900", "69500", "1200"),
    ]
    fake = FakeTransport(response=_bars_resp(rows))
    bars = _client(fake).ticker("005930").bars(start="20240102")
    assert [f"{bar.timestamp:%Y%m%d}" for bar in bars] == ["20240102", "20240103", "20240104"]
    assert bars[0].open == Decimal(69000)
    assert bars[0].close == Decimal(69500)
    assert bars[0].volume == 1200
    assert fake.calls[0]["params"]["FID_PERIOD_DIV_CODE"] == "D"
    assert fake.calls[0]["params"]["FID_ORG_ADJ_PRC"] == "0"


def test_ticker_bars_weekly_maps_to_period_w():
    fake = FakeTransport(response=_bars_resp([_bar_row("20240105", "1", "1", "1", "1", "1")]))
    _client(fake).ticker("005930").bars(start="20240105", interval="1wk")
    assert fake.calls[0]["params"]["FID_PERIOD_DIV_CODE"] == "W"


def test_ticker_bars_unadjusted_polarity():
    fake = FakeTransport(response=_bars_resp([_bar_row("20240105", "1", "1", "1", "1", "1")]))
    _client(fake).ticker("005930").bars(start="20240105", adjusted=False)
    assert fake.calls[0]["params"]["FID_ORG_ADJ_PRC"] == "1"


def test_ticker_bars_paginates_date_window():
    page_a = _bars_resp([_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1") for n in (8, 7, 6, 5)])
    page_b = _bars_resp([_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1") for n in (5, 4, 3, 2, 1)])
    fake = FakeTransport(by_path={_BARS_PATH: [page_a, page_b]})
    bars = _client(fake).ticker("005930").bars(start="20240101")
    assert [f"{bar.timestamp:%Y%m%d}" for bar in bars] == [f"2024010{n}" for n in range(1, 9)]
    assert len(fake.calls) == 2
    assert fake.calls[1]["params"]["FID_INPUT_DATE_2"] == "20240104"   # oldest(0105) 하루 전


def test_ticker_bars_max_bars_keeps_recent():
    rows = [_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1") for n in (4, 3, 2, 1)]
    bars = _client(FakeTransport(response=_bars_resp(rows))).ticker("005930").bars(
        start="20240101", max_bars=2
    )
    assert [f"{bar.timestamp:%Y%m%d}" for bar in bars] == ["20240103", "20240104"]


def test_ticker_bars_non_list_output2_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": "oops"})
    with pytest.raises(KisError):
        _client(FakeTransport(response=resp)).ticker("005930").bars(start="20240101")


def test_ticker_bars_start_after_end_raises():
    with pytest.raises(KisUsageError):
        _client(FakeTransport(response=_bars_resp([]))).ticker("005930").bars(
            start="20240201", end="20240101"
        )


# --- order_book ------------------------------------------------------------
_ORDER_BOOK_PATH = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"


def _order_book_output(bids, asks, *, total_bid="500", total_ask="600"):
    out = {"total_bidp_rsqn": total_bid, "total_askp_rsqn": total_ask}
    for step in range(1, 11):
        bid = bids[step - 1] if step - 1 < len(bids) else ("0", "0")
        ask = asks[step - 1] if step - 1 < len(asks) else ("0", "0")
        out[f"bidp{step}"], out[f"bidp_rsqn{step}"] = bid
        out[f"askp{step}"], out[f"askp_rsqn{step}"] = ask
    return out


def _order_book_resp(output1):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": output1})


def test_ticker_order_book_best_first():
    output1 = _order_book_output(bids=[("71500", "150"), ("71400", "250")],
                                 asks=[("71600", "100"), ("71700", "200")])
    book = _client(FakeTransport(response=_order_book_resp(output1))).ticker("005930").order_book()
    assert isinstance(book, OrderBook)
    assert book.bids[0] == PriceLevel(price=Decimal(71500), quantity=150)
    assert book.asks[0] == PriceLevel(price=Decimal(71600), quantity=100)
    assert len(book.bids) == 2 and len(book.asks) == 2
    assert book.total_bid_quantity == 500


def test_ticker_order_book_negative_price_fails_closed():
    output1 = _order_book_output(bids=[("-100", "10")], asks=[("71600", "100")])
    with pytest.raises(KisError):
        _client(FakeTransport(response=_order_book_resp(output1))).ticker("005930").order_book()


def test_ticker_order_book_missing_output1_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={})
    with pytest.raises(KisError):
        _client(FakeTransport(response=resp)).ticker("005930").order_book()


def test_ticker_order_book_halt_empty_ladders_zero_totals():
    output1 = _order_book_output([], [], total_bid="", total_ask="")   # 정지/동시호가
    book = _client(FakeTransport(response=_order_book_resp(output1))).ticker("005930").order_book()
    assert book.bids == () and book.asks == ()
    assert book.total_bid_quantity == 0 and book.total_ask_quantity == 0


def test_ticker_order_book_skips_gap_and_keeps_best_first():
    output1 = _order_book_output(
        bids=[("71500", "100"), ("0", "0"), ("71300", "300")],   # 2단계 빔
        asks=[("71600", "110"), ("0", "0"), ("71800", "310")],
    )
    book = _client(FakeTransport(response=_order_book_resp(output1))).ticker("005930").order_book()
    assert book.bids == (PriceLevel(Decimal(71500), 100), PriceLevel(Decimal(71300), 300))
    assert book.asks == (PriceLevel(Decimal(71600), 110), PriceLevel(Decimal(71800), 310))


# --- 시장 매핑/시각 -------------------------------------------------------
def test_ticker_market_override_to_unified_board():
    fake = FakeTransport(response=_quote_resp())
    quote = _client(fake).ticker("005930", market="UN").quote()
    assert quote.market == "UN"
    assert fake.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"] == "UN"


def test_ticker_bad_market_override_rejected_before_io():
    fake = FakeTransport(response=_quote_resp())
    with pytest.raises(KisUsageError):        # KROX 같은 오타는 조회 전에 거부
        _client(fake).ticker("005930", market="KROX")
    assert fake.calls == []


def test_quote_as_of_uses_kst_offset():
    quote = _client(FakeTransport(response=_quote_resp())).ticker("005930").quote()
    assert quote.as_of.utcoffset() == timedelta(hours=9)


# --- bars/order_book 추가 엣지 -------------------------------------------
@pytest.mark.parametrize("max_bars", [0, -1])
def test_ticker_bars_max_bars_must_be_positive(max_bars):
    fake = FakeTransport(response=_bars_resp([]))
    with pytest.raises(KisUsageError):
        _client(fake).ticker("005930").bars(start="20240101", max_bars=max_bars)
    assert fake.calls == []


def test_ticker_bars_stops_on_empty_page():
    page_a = _bars_resp([_bar_row("20240105", "1", "1", "1", "5", "1")])
    fake = FakeTransport(by_path={_BARS_PATH: [page_a, _bars_resp([])]})
    bars = _client(fake).ticker("005930").bars(start="20200101")   # 데이터보다 훨씬 이전
    assert [f"{bar.timestamp:%Y%m%d}" for bar in bars] == ["20240105"]
    assert len(fake.calls) == 2


# --- 엔티티 값 의미론 -----------------------------------------------------
def test_bar_and_order_book_value_semantics_hashable():
    when = datetime(2024, 1, 2, tzinfo=timezone(timedelta(hours=9)))
    bar_a = Bar(symbol="005930", timestamp=when, open=Decimal(1), high=Decimal(2),
                low=Decimal(1), close=Decimal(2), volume=10, _raw={"a": "b"})
    bar_b = Bar(symbol="005930", timestamp=when, open=Decimal(1), high=Decimal(2),
                low=Decimal(1), close=Decimal(2), volume=10, _raw={"z": "1"})
    assert bar_a == bar_b and hash(bar_a) == hash(bar_b)   # raw 무시
    assert PriceLevel(Decimal(1), 2) == PriceLevel(Decimal(1), 2)
    fields = {"symbol": "005930", "market": "KRX",
              "bids": (PriceLevel(Decimal(1), 2),), "asks": (PriceLevel(Decimal(3), 4),),
              "total_bid_quantity": 2, "total_ask_quantity": 4, "as_of": when}
    assert OrderBook(**fields, _raw={"a": "b"}) == OrderBook(**fields, _raw={"z": "1"})


# --- 구성/인증 ------------------------------------------------------------
def test_transport_required_until_http_implemented():
    with pytest.raises(NotImplementedError):
        KisClient(app_key="k", app_secret="s", account="12345678-01")


@pytest.mark.parametrize("account", ["12345678", "1-2-3", "12345678-", "-01"])
def test_bad_account_format_rejected(account):
    with pytest.raises(KisUsageError):
        KisClient(app_key="k", app_secret="s", account=account,
                  transport=FakeTransport(response=_quote_resp()))


def test_account_optional_for_market_data():
    kis = KisClient(app_key="k", app_secret="s", transport=FakeTransport(response=_quote_resp()))
    assert kis.ticker("005930").quote().last == Decimal(71500)   # 계좌 없이 시세 OK
