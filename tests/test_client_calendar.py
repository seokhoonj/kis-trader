"""기업행위 캘린더 -- kis.calendar.dividends().

배당일정(HHKDB669102C0)의 TR/URL·기간·종목·구분 파라미터·필드 매핑(zero/space padding, 날짜
sentinel)·fail-closed 를 가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_openapi import DividendEvent, KISClient
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse


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


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": rows})


def test_dividends_maps_and_params():
    # 원장 응답 예시값(현대건설 결산배당; zero-pad face_val, space-pad divi_rate, 지급일 미정=빈값).
    rows = [{"record_date": "20240326", "sht_cd": "000720", "isin_name": "현대건설",
             "divi_kind": "결산", "face_val": "000005000", "per_sto_divi_amt": "000000000600",
             "divi_rate": " 12.00", "stk_divi_rate": "  0.00", "divi_pay_dt": "",
             "stk_div_pay_dt": "", "odd_pay_dt": "", "stk_kind": "보통", "high_divi_gb": ""}]
    fake = FakeTransport(response=_resp(rows))
    events = _client(fake).calendar.dividends(start="20240301", end="20240331")
    assert isinstance(events[0], DividendEvent)
    e = events[0]
    assert e.symbol == "000720"                          # 코드 문자열 유지(정수화 안 함)
    assert e.name == "현대건설"
    assert e.record_date == date(2024, 3, 26)
    assert e.face_value == Decimal(5000)                 # zero-pad 파싱
    assert e.cash_dividend == Decimal(600)
    assert e.cash_dividend_rate == Decimal("12.00")      # space-pad 파싱
    assert e.cash_pay_date is None                       # 빈 지급일 -> None
    assert e.stock_kind == "보통"
    assert e.high_dividend is False
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/dividend"
    assert call["tr_id"] == "HHKDB669102C0"
    assert call["params"]["F_DT"] == "20240301"
    assert call["params"]["T_DT"] == "20240331"
    assert call["params"]["GB1"] == "0"                  # all(기본)
    assert call["params"]["SHT_CD"] == ""                # 종목 미지정 -> 전체


def test_dividends_symbol_and_kind_filters():
    fake = FakeTransport(response=_resp([]))
    _client(fake).calendar.dividends(
        start=date(2024, 3, 1), end=date(2024, 3, 31), symbol="005930", kind="interim"
    )
    call = fake.calls[0]
    assert call["params"]["SHT_CD"] == "005930"
    assert call["params"]["GB1"] == "2"                  # interim


def test_dividends_high_dividend_flag_and_zero_sentinel_date():
    rows = [{"record_date": "20240326", "sht_cd": "000720", "isin_name": "X", "divi_kind": "결산",
             "face_val": "5000", "per_sto_divi_amt": "600", "divi_rate": "12.0",
             "stk_divi_rate": "0.0", "divi_pay_dt": "00000000", "stk_div_pay_dt": "",
             "odd_pay_dt": "", "stk_kind": "보통", "high_divi_gb": "Y"}]
    fake = FakeTransport(response=_resp(rows))
    e = _client(fake).calendar.dividends(start="20240301", end="20240331")[0]
    assert e.high_dividend is True                        # "Y" -> True
    assert e.cash_pay_date is None                        # "00000000" sentinel -> None


def test_dividends_bad_kind_raises():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).calendar.dividends(start="20240301", end="20240331", kind="nope")


def test_dividends_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).calendar.dividends(start="20240301", end="20240331")
