"""거래 캘린더 -- kis.market.trading_calendar(). 필드는 원장 응답예시 실값."""
from __future__ import annotations

import threading

import pytest

from kis_openapi import FuturesMarketSchedule, KISClient, TradingDay
from kis_openapi.errors import KISError
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


def _client(t):
    return KISClient(app_key="k", app_secret="s", transport=t)


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": rows})


def test_trading_calendar_maps_flags():
    rows = [{"bass_dt": "20221227", "wday_dvsn_cd": "03", "bzdy_yn": "Y", "tr_day_yn": "Y",
             "opnd_yn": "Y", "sttl_day_yn": "Y"},
            {"bass_dt": "20221231", "wday_dvsn_cd": "07", "bzdy_yn": "N", "tr_day_yn": "N",
             "opnd_yn": "N", "sttl_day_yn": "N"}]
    fake = FakeTransport(response=_resp(rows))
    days = _client(fake).market.trading_calendar(base_date="20221227")
    assert all(isinstance(d, TradingDay) for d in days)
    assert f"{days[0].date:%Y%m%d}" == "20221227"
    assert days[0].is_open is True
    assert days[0].is_settlement_day is True
    assert days[1].is_open is False                      # 휴장일
    assert days[1].is_business_day is False
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/chk-holiday"
    assert call["tr_id"] == "CTCA0903R"
    assert call["params"]["BASS_DT"] == "20221227"


def test_trading_calendar_skips_empty_and_missing_output():
    fake = FakeTransport(response=_resp([{"bass_dt": ""}]))
    assert _client(fake).market.trading_calendar(base_date="20221227") == []
    fake2 = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake2).market.trading_calendar()


def test_futures_market_schedule_maps_business_days_and_times():
    response = RawResponse(
        rt_cd="0",
        msg_cd="X",
        msg1="ok",
        body={
            "output1": {
                "date1": "20240221",
                "date2": "20240222",
                "date3": "20240223",
                "date4": "20240226",
                "date5": "20240227",
                "today": "20240223",
                "time": "101530",
                "s_time": "084500",
                "e_time": "154500",
            }
        },
    )
    fake = FakeTransport(response=response)
    schedule = _client(fake).market.futures_market_schedule()

    assert isinstance(schedule, FuturesMarketSchedule)
    assert [f"{day:%Y%m%d}" for day in schedule.business_days] == [
        "20240221",
        "20240222",
        "20240223",
        "20240226",
        "20240227",
    ]
    assert f"{schedule.today:%Y%m%d}" == "20240223"
    assert f"{schedule.current_time:%Y%m%d%H%M%S}" == "20240223101530"
    assert f"{schedule.opens_at:%H%M%S}" == "084500"
    assert f"{schedule.closes_at:%H%M%S}" == "154500"
    assert schedule.current_time.tzinfo is not None
    assert fake.calls[0] == {
        "path": "/uapi/domestic-stock/v1/quotations/market-time",
        "tr_id": "HHMCM000002C0",
        "params": {},
    }


def test_futures_market_schedule_missing_output_fails_closed():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={})
    with pytest.raises(KISError):
        _client(FakeTransport(response=response)).market.futures_market_schedule()
