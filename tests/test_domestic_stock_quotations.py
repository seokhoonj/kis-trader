"""국내주식 시세(quotations) 테스트 -- 현재가 파싱(전일대비 부호 포함), 기간별 OHLCV 파싱,
날짜창 페이지네이션(중복 병합·빈 페이지 종료·상한 fail-closed), interval->기간코드 매핑,
분봉 미구현 가드, 수정주가 극성, 파라미터 매핑, fail-closed 수치 파싱. 전부 네트워크 없이
가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_openapi._wire import (
    optional_decimal,
    optional_int,
    required_decimal,
    required_int,
)
from kis_openapi.domestic_stock import DomesticStock
from kis_openapi.domestic_stock.quotations import (
    Bar,
    OrderBook,
    PriceLevel,
    Quotations,
    Quote,
)
from kis_openapi.domestic_stock.quotations import facade as facade_module
from kis_openapi.errors import KisError, KisUsageError
from kis_openapi.transport import RawResponse

_KST = timezone(timedelta(hours=9))

_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
_BARS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"


class FakeTransport:
    """읽기 전용 가짜 전송. 기본 응답(``response``), 경로별 순차 응답(``by_path`` 리스트),
    예외(``raises``)를 지원하고 모든 호출을 기록한다."""

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
        elif self.raises is not None:
            outcome = self.raises
        else:
            outcome = self.response
        if isinstance(outcome, BaseException):
            raise outcome
        assert outcome is not None, "FakeTransport 에 응답을 줘야 한다"
        return outcome


# --- 표본 응답 -------------------------------------------------------------
_QUOTE_OUTPUT = {
    "stck_prpr": "71500", "stck_oprc": "70800", "stck_hgpr": "71800", "stck_lwpr": "70600",
    "stck_sdpr": "70900", "prdy_vrss": "600", "prdy_vrss_sign": "2", "prdy_ctrt": "0.85",
    "acml_vol": "12345678", "w52_hgpr": "88000", "w52_lwpr": "49900",
}


def _quote_resp(output=None):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output": output if output is not None else dict(_QUOTE_OUTPUT)})


def _bar_row(d, o, h, l, c, v):
    return {"stck_bsop_date": d, "stck_oprc": o, "stck_hgpr": h, "stck_lwpr": l,
            "stck_clpr": c, "acml_vol": v}


def _bars_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": rows})


_ERROR = RawResponse(rt_cd="1", msg_cd="MCA05918", msg1="종목코드 오류", body={})


# --- quote -----------------------------------------------------------------
def test_quote_parses_price_snapshot():
    q = Quotations(FakeTransport(response=_quote_resp()))
    quote = q.quote("005930")
    assert quote.symbol == "005930"
    assert quote.market == "KRX"
    assert quote.currency == "KRW"
    assert quote.last == Decimal(71500)
    assert quote.open == Decimal(70800)
    assert quote.high == Decimal(71800)
    assert quote.low == Decimal(70600)
    assert quote.previous_close == Decimal(70900)
    assert quote.change == Decimal(600)            # sign 2 = 상승 -> 양수
    assert quote.change_percent == Decimal("0.85")
    assert quote.volume == 12345678
    assert quote.week_52_high == Decimal(88000)
    assert quote.week_52_low == Decimal(49900)
    assert quote.as_of.tzinfo is not None             # KST-aware


def test_quote_change_is_negative_on_down_sign():
    output = dict(_QUOTE_OUTPUT, prdy_vrss_sign="5", prdy_vrss="600", prdy_ctrt="0.85")
    quote = Quotations(FakeTransport(response=_quote_resp(output))).quote("005930")
    assert quote.change == Decimal(-600)            # sign 5 = 하락 -> 음수
    assert quote.change_percent == Decimal("-0.85")


def test_quote_week52_absent_is_none():
    output = dict(_QUOTE_OUTPUT, w52_hgpr="", w52_lwpr="  ")
    quote = Quotations(FakeTransport(response=_quote_resp(output))).quote("005930")
    assert quote.week_52_high is None
    assert quote.week_52_low is None


def test_quote_maps_market_and_symbol_to_params():
    fake = FakeTransport(response=_quote_resp())
    Quotations(fake).quote("005930", market="NXT")
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["idempotent"] is True                 # 읽기 -- 재시도 안전
    assert call["path"] == _QUOTE_PATH
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "NX"
    assert call["params"]["FID_INPUT_ISCD"] == "005930"


def test_quote_unknown_market_raises_before_io():
    fake = FakeTransport(response=_quote_resp())
    with pytest.raises(KisUsageError):
        Quotations(fake).quote("005930", market="FOO")  # type: ignore[arg-type]
    assert fake.calls == []                            # I/O 전에 중단


def test_quote_error_response_raises():
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=_ERROR)).quote("BADCODE")


def test_quote_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={})
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=resp)).quote("005930")


def test_quote_unparseable_price_fails_closed():
    output = dict(_QUOTE_OUTPUT, stck_prpr="N/A")
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=_quote_resp(output))).quote("005930")


# --- bars ------------------------------------------------------------------
def test_bars_parses_daily_ohlcv_ascending():
    rows = [                                            # KIS는 최근->과거 순으로 줄 수 있다
        _bar_row("20240104", "70000", "70500", "69800", "70200", "1000"),
        _bar_row("20240103", "69500", "70100", "69400", "70000", "1100"),
        _bar_row("20240102", "69000", "69600", "68900", "69500", "1200"),
    ]
    fake = FakeTransport(response=_bars_resp(rows))
    bars = Quotations(fake).bars("005930", start="20240102")
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == ["20240102", "20240103", "20240104"]
    first = bars[0]
    assert isinstance(first, Bar)
    assert first.open == Decimal(69000)
    assert first.high == Decimal(69600)
    assert first.low == Decimal(68900)
    assert first.close == Decimal(69500)
    assert first.volume == 1200
    assert first.timestamp.tzinfo is not None
    assert fake.calls[0]["params"]["FID_PERIOD_DIV_CODE"] == "D"
    assert fake.calls[0]["params"]["FID_ORG_ADJ_PRC"] == "0"   # 기본 수정주가
    assert len(fake.calls) == 1                        # oldest == start -> 한 번에 종료


def test_bars_skips_empty_trailing_bar():
    rows = [
        _bar_row("20240104", "", "", "", "", ""),      # 미체결 세션의 빈 바 -- 건너뜀
        _bar_row("20240103", "69500", "70100", "69400", "70000", "1100"),
    ]
    bars = Quotations(FakeTransport(response=_bars_resp(rows))).bars("005930", start="20240103")
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == ["20240103"]


def test_bars_paginates_and_merges_overlapping_windows():
    page_a = _bars_resp([
        _bar_row("20240108", "1", "1", "1", "108", "10"),
        _bar_row("20240107", "1", "1", "1", "107", "10"),
        _bar_row("20240106", "1", "1", "1", "106", "10"),
        _bar_row("20240105", "1", "1", "1", "105", "10"),   # oldest -> 더 뒤로
    ])
    page_b = _bars_resp([
        _bar_row("20240105", "1", "1", "1", "105", "10"),   # 창 겹침(중복) -> 병합 시 1개
        _bar_row("20240104", "1", "1", "1", "104", "10"),
        _bar_row("20240103", "1", "1", "1", "103", "10"),
        _bar_row("20240102", "1", "1", "1", "102", "10"),
        _bar_row("20240101", "1", "1", "1", "101", "10"),   # start 도달 -> 종료
    ])
    fake = FakeTransport(by_path={_BARS_PATH: [page_a, page_b]})
    bars = Quotations(fake).bars("005930", start="20240101")
    dates = [f"{b.timestamp:%Y%m%d}" for b in bars]
    assert dates == ["20240101", "20240102", "20240103", "20240104",
                     "20240105", "20240106", "20240107", "20240108"]  # 중복 제거 + 오름차순
    assert len(fake.calls) == 2
    assert fake.calls[1]["params"]["FID_INPUT_DATE_2"] == "20240104"  # oldest(20240105) 하루 전


def test_bars_stops_on_empty_page():
    page_a = _bars_resp([_bar_row("20240105", "1", "1", "1", "105", "10")])
    fake = FakeTransport(by_path={_BARS_PATH: [page_a, _bars_resp([])]})
    bars = Quotations(fake).bars("005930", start="20200101")  # 데이터보다 훨씬 이전
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == ["20240105"]
    assert len(fake.calls) == 2                        # 빈 페이지에서 종료


def test_bars_weekly_maps_to_period_w():
    fake = FakeTransport(response=_bars_resp([_bar_row("20240105", "1", "1", "1", "1", "1")]))
    Quotations(fake).bars("005930", start="20240105", interval="1wk")
    assert fake.calls[0]["params"]["FID_PERIOD_DIV_CODE"] == "W"


def test_bars_unadjusted_sets_raw_price_polarity():
    fake = FakeTransport(response=_bars_resp([_bar_row("20240105", "1", "1", "1", "1", "1")]))
    Quotations(fake).bars("005930", start="20240105", adjusted=False)
    assert fake.calls[0]["params"]["FID_ORG_ADJ_PRC"] == "1"   # 원주가


def test_bars_minute_interval_not_implemented_before_io():
    fake = FakeTransport(response=_bars_resp([]))
    with pytest.raises(NotImplementedError):
        Quotations(fake).bars("005930", start="20240101", interval="5m")
    assert fake.calls == []                            # I/O 전에 중단


def test_bars_unknown_interval_raises():
    fake = FakeTransport(response=_bars_resp([]))
    with pytest.raises(KisUsageError):
        Quotations(fake).bars("005930", start="20240101", interval="2d")  # type: ignore[arg-type]
    assert fake.calls == []


def test_bars_start_after_end_raises():
    with pytest.raises(KisUsageError):
        Quotations(FakeTransport(response=_bars_resp([]))).bars(
            "005930", start="20240201", end="20240101"
        )


def test_bars_accepts_date_objects():
    fake = FakeTransport(response=_bars_resp([_bar_row("20240105", "1", "1", "1", "1", "1")]))
    Quotations(fake).bars("005930", start=date(2024, 1, 5), end=date(2024, 1, 5))
    assert fake.calls[0]["params"]["FID_INPUT_DATE_1"] == "20240105"
    assert fake.calls[0]["params"]["FID_INPUT_DATE_2"] == "20240105"


def test_bars_default_end_is_today_kst(monkeypatch):
    monkeypatch.setattr(facade_module, "_today_kst", lambda: "20240131")  # 자정 경계 플레이크 제거
    fake = FakeTransport(response=_bars_resp([]))
    Quotations(fake).bars("005930", start="20240101")
    assert fake.calls[0]["params"]["FID_INPUT_DATE_2"] == "20240131"


def test_bars_max_bars_keeps_most_recent():
    rows = [_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1") for n in (4, 3, 2, 1)]
    bars = Quotations(FakeTransport(response=_bars_resp(rows))).bars(
        "005930", start="20240101", max_bars=2
    )
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == ["20240103", "20240104"]  # 최근 2개


def test_bars_max_bars_must_be_positive():
    with pytest.raises(KisUsageError):
        Quotations(FakeTransport(response=_bars_resp([]))).bars(
            "005930", start="20240101", max_bars=0
        )


def test_bars_error_response_raises():
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=_ERROR)).bars("BADCODE", start="20240101")


def test_bars_pagination_cap_fails_closed(monkeypatch):
    monkeypatch.setattr(facade_module, "_MAX_BAR_PAGES", 3)
    full_page = _bars_resp([_bar_row("20240105", "1", "1", "1", "105", "10")])  # oldest > start 매번
    fake = FakeTransport(response=full_page)           # 매 호출 같은 페이지 -> start 영영 못 미침
    with pytest.raises(KisError):
        Quotations(fake).bars("005930", start="20240101")
    assert len(fake.calls) == 3                        # 상한까지만


def test_bars_non_list_output2_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": "oops"})
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=resp)).bars("005930", start="20240101")


def test_bars_row_with_close_but_missing_open_fails_closed():
    rows = [_bar_row("20240103", "", "70100", "69400", "70000", "1100")]  # close 있고 open 빔
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=_bars_resp(rows))).bars("005930", start="20240103")


def test_bars_monthly_maps_to_period_m():
    fake = FakeTransport(response=_bars_resp([_bar_row("20240101", "1", "1", "1", "1", "1")]))
    Quotations(fake).bars("005930", start="20240101", interval="1mo")
    assert fake.calls[0]["params"]["FID_PERIOD_DIV_CODE"] == "M"


def test_bars_market_nxt_maps_to_nx():
    fake = FakeTransport(response=_bars_resp([_bar_row("20240101", "1", "1", "1", "1", "1")]))
    Quotations(fake).bars("005930", start="20240101", market="NXT")
    assert fake.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"] == "NX"


def test_bars_window_step_crosses_year_boundary():
    page_a = _bars_resp([_bar_row("20240101", "1", "1", "1", "101", "10")])  # oldest 새해 첫날
    page_b = _bars_resp([_bar_row("20231229", "1", "1", "1", "1229", "10")])  # start 도달
    fake = FakeTransport(by_path={_BARS_PATH: [page_a, page_b]})
    bars = Quotations(fake).bars("005930", start="20231229")
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == ["20231229", "20240101"]
    assert fake.calls[1]["params"]["FID_INPUT_DATE_2"] == "20231231"  # 20240101 하루 전


def test_bars_max_bars_short_circuits_paging():
    page_a = _bars_resp([_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1") for n in (5, 4, 3)])
    # 최근 2개면 충분 -> 첫 페이지에서 멈추고 두 번째 창을 조회하지 않는다.
    fake = FakeTransport(by_path={_BARS_PATH: [page_a, _bars_resp([])]})
    bars = Quotations(fake).bars("005930", start="20200101", max_bars=2)
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == ["20240104", "20240105"]
    assert len(fake.calls) == 1                        # 두 번째 창 조회 안 함


# --- _wire (fail-closed coercion) -----------------------------------------
def test_required_decimal_empty_raises():
    with pytest.raises(KisError):
        required_decimal("", "stck_prpr")


def test_required_decimal_unparseable_raises():
    with pytest.raises(KisError):
        required_decimal("N/A", "stck_prpr")


def test_optional_decimal_empty_is_none_but_unparseable_raises():
    assert optional_decimal("  ", "w52_hgpr") is None
    with pytest.raises(KisError):
        optional_decimal("N/A", "w52_hgpr")


def test_required_int_rejects_fraction_but_accepts_integral_decimal():
    assert required_int("1234.0", "acml_vol") == 1234    # 정수값의 소수표기는 허용
    with pytest.raises(KisError):
        required_int("1.5", "acml_vol")                  # 진짜 소수부는 거부(조작 금지)


def test_optional_int_empty_is_none_and_parses_when_present():
    assert optional_int("", "cntg_vol") is None
    assert optional_int("42", "cntg_vol") == 42


# --- prdy_vrss_sign 부호 코드 전수 ----------------------------------------
@pytest.mark.parametrize(
    ("sign_code", "expected_change"),
    [
        ("1", Decimal(600)),    # 상한 -> 양수
        ("2", Decimal(600)),    # 상승 -> 양수
        ("4", Decimal(-600)),   # 하한 -> 음수
        ("5", Decimal(-600)),   # 하락 -> 음수
        ("9", Decimal(600)),    # 미지 코드 -> 하락 코드가 아니면 양수(문서화된 폴백)
    ],
)
def test_quote_change_sign_by_code(sign_code, expected_change):
    output = dict(_QUOTE_OUTPUT, prdy_vrss_sign=sign_code, prdy_vrss="600")
    quote = Quotations(FakeTransport(response=_quote_resp(output))).quote("005930")
    assert quote.change == expected_change


def test_quote_change_zero_on_unchanged_sign():
    output = dict(_QUOTE_OUTPUT, prdy_vrss_sign="3", prdy_vrss="0", prdy_ctrt="0")  # 보합
    quote = Quotations(FakeTransport(response=_quote_resp(output))).quote("005930")
    assert quote.change == Decimal(0)
    assert quote.change_percent == Decimal(0)


# --- 값 의미론(raw 제외 동등성/해시) --------------------------------------
def test_quote_equality_ignores_raw_and_is_hashable():
    when = datetime(2024, 1, 2, tzinfo=_KST)             # as_of 고정(동등성에 포함되므로)
    fields = {
        "symbol": "005930", "market": "KRX", "currency": "KRW",
        "last": Decimal(1), "open": Decimal(1), "high": Decimal(1), "low": Decimal(1),
        "previous_close": Decimal(1), "change": Decimal(0), "change_percent": Decimal(0),
        "volume": 1, "week_52_high": None, "week_52_low": None, "as_of": when,
    }
    first = Quote(**fields, raw={"a": "b"})
    second = Quote(**fields, raw={"DIFFERENT": "raw"})
    assert first == second                               # 파싱된 값이 같으면 같다(raw 무시)
    assert hash(first) == hash(second)                   # frozen 인데 hash 가능해야 한다
    assert {first, second} == {first}                    # set 에 넣을 수 있다


# --- order_book ------------------------------------------------------------
_ORDER_BOOK_PATH = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"


def _order_book_output(bids, asks, *, total_bid="500", total_ask="600"):
    """bids/asks = 최우선 우선 (price, quantity) 목록. 나머지 단계는 0(주문 없음)으로 채움."""
    out = {"total_bidp_rsqn": total_bid, "total_askp_rsqn": total_ask, "aspr_acpt_hour": "123456"}
    for step in range(1, 11):
        bid = bids[step - 1] if step - 1 < len(bids) else ("0", "0")
        ask = asks[step - 1] if step - 1 < len(asks) else ("0", "0")
        out[f"bidp{step}"], out[f"bidp_rsqn{step}"] = bid
        out[f"askp{step}"], out[f"askp_rsqn{step}"] = ask
    return out


def _order_book_resp(output1):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": output1})


def test_order_book_parses_ladders_best_first():
    output1 = _order_book_output(
        bids=[("71500", "150"), ("71400", "250")],   # 매수: 높은 가격이 최우선
        asks=[("71600", "100"), ("71700", "200")],   # 매도: 낮은 가격이 최우선
    )
    book = Quotations(FakeTransport(response=_order_book_resp(output1))).order_book("005930")
    assert isinstance(book, OrderBook)
    assert book.symbol == "005930"
    assert book.market == "KRX"
    assert book.bids[0] == PriceLevel(price=Decimal(71500), quantity=150)  # 최우선 매수
    assert book.bids[1].price == Decimal(71400)
    assert book.asks[0] == PriceLevel(price=Decimal(71600), quantity=100)  # 최우선 매도
    assert book.asks[1].price == Decimal(71700)
    assert len(book.bids) == 2 and len(book.asks) == 2   # 빈 단계는 제외
    assert book.total_bid_quantity == 500
    assert book.total_ask_quantity == 600
    assert book.as_of.tzinfo is not None


def test_order_book_skips_empty_levels():
    output1 = _order_book_output(bids=[("71500", "150")], asks=[("71600", "100")])
    book = Quotations(FakeTransport(response=_order_book_resp(output1))).order_book("005930")
    assert len(book.bids) == 1 and len(book.asks) == 1


def test_order_book_unparseable_price_fails_closed():
    output1 = _order_book_output(bids=[("71500", "150")], asks=[("N/A", "100")])
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=_order_book_resp(output1))).order_book("005930")


def test_order_book_missing_quantity_on_real_level_fails_closed():
    output1 = _order_book_output(bids=[("71500", "150")], asks=[("71600", "")])
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=_order_book_resp(output1))).order_book("005930")


def test_order_book_maps_market_and_symbol_to_params():
    fake = FakeTransport(response=_order_book_resp(_order_book_output([("1", "1")], [("2", "1")])))
    Quotations(fake).order_book("005930", market="NXT")
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["idempotent"] is True
    assert call["path"] == _ORDER_BOOK_PATH
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "NX"
    assert call["params"]["FID_INPUT_ISCD"] == "005930"


def test_order_book_error_response_raises():
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=_ERROR)).order_book("BADCODE")


def test_order_book_missing_output1_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={})
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=resp)).order_book("005930")


def test_order_book_equality_ignores_raw_and_is_hashable():
    when = datetime(2024, 1, 2, tzinfo=_KST)
    fields = {
        "symbol": "005930", "market": "KRX",
        "bids": (PriceLevel(price=Decimal(71500), quantity=150),),
        "asks": (PriceLevel(price=Decimal(71600), quantity=100),),
        "total_bid_quantity": 150, "total_ask_quantity": 100, "as_of": when,
    }
    first_book = OrderBook(**fields, raw={"a": "b"})
    second_book = OrderBook(**fields, raw={"DIFFERENT": "raw"})
    assert first_book == second_book
    assert hash(first_book) == hash(second_book)
    assert {first_book, second_book} == {first_book}


def test_price_level_value_equality_and_is_hashable():
    first = PriceLevel(price=Decimal(71500), quantity=150)
    same = PriceLevel(price=Decimal(71500), quantity=150)
    other = PriceLevel(price=Decimal(71500), quantity=151)
    assert first == same
    assert first != other
    assert hash(first) == hash(same)
    assert {first, same, other} == {first, other}


def test_order_book_halt_has_empty_ladders_and_zero_totals():
    output1 = _order_book_output([], [], total_bid="", total_ask="")  # 정지/동시호가: 다 빔
    book = Quotations(FakeTransport(response=_order_book_resp(output1))).order_book("005930")
    assert book.bids == ()
    assert book.asks == ()
    assert book.total_bid_quantity == 0    # 빈 총잔량 -> 0(빈 사다리와 대칭, raise 아님)
    assert book.total_ask_quantity == 0


def test_order_book_skips_gap_without_dropping_later_level():
    output1 = _order_book_output(
        bids=[("", "0"), ("71400", "250")],   # 1단계 빔, 2단계 실재
        asks=[("0", "0"), ("71700", "200")],
    )
    book = Quotations(FakeTransport(response=_order_book_resp(output1))).order_book("005930")
    assert book.bids == (PriceLevel(price=Decimal(71400), quantity=250),)
    assert book.asks == (PriceLevel(price=Decimal(71700), quantity=200),)


def test_order_book_keeps_priced_level_with_zero_quantity():
    output1 = _order_book_output(
        bids=[("71500", "0")], asks=[("71600", "0")], total_bid="0", total_ask="0"
    )
    book = Quotations(FakeTransport(response=_order_book_resp(output1))).order_book("005930")
    assert book.bids == (PriceLevel(price=Decimal(71500), quantity=0),)  # 가격 있으면 유지


def test_order_book_negative_price_fails_closed():
    output1 = _order_book_output(bids=[("-100", "10")], asks=[("71600", "100")])
    with pytest.raises(KisError):
        Quotations(FakeTransport(response=_order_book_resp(output1))).order_book("005930")


def test_order_book_market_un_maps_to_un():
    fake = FakeTransport(response=_order_book_resp(_order_book_output([("1", "1")], [("2", "1")])))
    Quotations(fake).order_book("005930", market="UN")
    assert fake.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"] == "UN"


def test_order_book_raw_preserves_acceptance_time():
    output1 = _order_book_output([("71500", "1")], [("71600", "1")])
    book = Quotations(FakeTransport(response=_order_book_resp(output1))).order_book("005930")
    assert book.raw["aspr_acpt_hour"] == "123456"       # output1 필드는 raw 에 보존


# --- facade wiring ---------------------------------------------------------
def test_domestic_stock_exposes_quotations():
    client = DomesticStock(FakeTransport(response=_quote_resp()))
    quote = client.quotations.quote("005930")
    assert quote.last == Decimal(71500)
