"""해외주식 기간봉 -- kis.overseas.stock(symbol, exchange=...).bars().

dailyprice 엔드포인트, GUBN(일/주/월) 매핑, MODP(수정주가), BYMD 앵커 walk-back 페이지네이션,
오름차순 정렬, 분봉 미지원을 검증한다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader import Bar, KISClient
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse

_OVERSEAS_BARS = "/uapi/overseas-price/v1/quotations/dailyprice"


def _bar_row(xymd, open_p, high_p, low_p, clos, tvol):
    return {"xymd": xymd, "open": open_p, "high": high_p, "low": low_p, "clos": clos, "tvol": tvol}


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {}, "output2": rows})


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


def _client(transport):
    return KISClient(app_key="k", app_secret="s", environment="real", transport=transport)


def test_overseas_bars_parses_ascending_and_params():
    fake = FakeTransport(response=_resp([
        _bar_row("20240104", "149.0", "151.0", "148.5", "150.25", "500"),
        _bar_row("20240103", "147.0", "149.5", "146.5", "148.00", "480"),
        _bar_row("20240102", "145.0", "147.5", "144.5", "146.50", "460"),
    ]))
    bars = _client(fake).overseas.stock("AAPL", exchange="NAS").bars(start="20240102")
    assert [b.symbol for b in bars] == ["AAPL", "AAPL", "AAPL"]
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == ["20240102", "20240103", "20240104"]
    assert bars[-1].close == Decimal("150.25")
    assert bars[0].open == Decimal("145.0")
    assert bars[-1].volume == 500
    call = fake.calls[0]
    assert call["path"] == _OVERSEAS_BARS
    assert call["tr_id"] == "HHDFS76240000"
    assert call["params"]["EXCD"] == "NAS"
    assert call["params"]["SYMB"] == "AAPL"
    assert call["params"]["GUBN"] == "0"           # 1d
    assert call["params"]["MODP"] == "1"           # adjusted 기본


def test_overseas_bars_weekly_monthly_and_unadjusted():
    for interval, gubn in [("1wk", "1"), ("1mo", "2")]:
        fake = FakeTransport(response=_resp([_bar_row("20240105", "1", "1", "1", "1", "1")]))
        _client(fake).overseas.stock("AAPL", exchange="NAS").bars(start="20240105", interval=interval)
        assert fake.calls[0]["params"]["GUBN"] == gubn
    fake = FakeTransport(response=_resp([_bar_row("20240105", "1", "1", "1", "1", "1")]))
    _client(fake).overseas.stock("AAPL", exchange="NAS").bars(start="20240105", adjusted=False)
    assert fake.calls[0]["params"]["MODP"] == "0"


def test_overseas_bars_paginates_by_base_date():
    page_a = _resp([_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1") for n in (8, 7, 6, 5)])
    page_b = _resp([_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1") for n in (5, 4, 3, 2, 1)])
    fake = FakeTransport(by_path={_OVERSEAS_BARS: [page_a, page_b]})
    bars = _client(fake).overseas.stock("AAPL", exchange="NAS").bars(start="20240101")
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == [f"2024010{n}" for n in range(1, 9)]
    assert isinstance(bars[0], Bar)
    # 2페이지째 BYMD 는 첫 페이지 최소일(20240105) 하루 전(20240104).
    assert fake.calls[1]["params"]["BYMD"] == "20240104"


_OVERSEAS_MINUTE = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"


def _min_row(xymd, xhms, *, last="197.41", vol="5695"):
    return {"tymd": xymd, "xymd": xymd, "xhms": xhms, "kymd": xymd, "khms": xhms,
            "open": "197.34", "high": "197.41", "low": "197.28", "last": last, "evol": vol,
            "eamt": "1123799"}


class MinuteFakeTransport:
    """KEYB(현지 YYYYMMDDHHMMSS, 첫 조회 공백) 이하 분봉을 최신 3건씩 돌려주는 가짜 전송."""

    def __init__(self, minutes):
        # minutes: "YYYYMMDDHHMMSS" -> row, 오름차순
        self.minutes = dict(sorted(minutes.items()))
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        keyb = params["KEYB"] or "99999999999999"     # 공백 = 최신부터
        at_or_before = [k for k in self.minutes if k <= keyb]
        page = [self.minutes[k] for k in at_or_before[-3:]]
        return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                           body={"output1": {"next": "1"}, "output2": page})


def test_overseas_minute_bars_paginate_ascending_and_params():
    times = [f"2024022209{m:02d}00" for m in range(9)]   # 09:00..09:08, 1분 간격 9개
    minutes = {t: _min_row(t[:8], t[8:], last=str(197 + i)) for i, t in enumerate(times)}
    fake = MinuteFakeTransport(minutes)
    bars = _client(fake).overseas.stock("TSLA", exchange="NAS").bars(interval="1m")
    assert [f"{b.timestamp:%Y%m%d%H%M%S}" for b in bars] == times   # 과거->현재 오름차순, 전량
    assert fake.calls[0]["path"] == _OVERSEAS_MINUTE
    assert fake.calls[0]["tr_id"] == "HHDFS76950200"
    assert fake.calls[0]["params"]["NMIN"] == "1"
    assert fake.calls[0]["params"]["NREC"] == "120"
    assert fake.calls[0]["params"]["KEYB"] == ""               # 첫 조회 공백
    assert fake.calls[0]["params"]["SYMB"] == "TSLA"
    # 2페이지 KEYB = 1페이지 최오래 봉(090600) 1분 전
    assert fake.calls[1]["params"]["KEYB"] == "20240222090500"


def test_overseas_minute_bars_maps_close_volume_and_max_bars():
    times = [f"2024022209{m:02d}00" for m in range(9)]
    minutes = {t: _min_row(t[:8], t[8:], last=str(197 + i), vol=str(100 + i))
               for i, t in enumerate(times)}
    fake = MinuteFakeTransport(minutes)
    bars = _client(fake).overseas.stock("TSLA", exchange="NAS").bars(interval="1m", max_bars=4)
    assert len(bars) == 4
    assert [f"{b.timestamp:%Y%m%d%H%M%S}" for b in bars] == times[-4:]
    assert bars[-1].close == Decimal(205)                     # last 매핑
    assert bars[-1].volume == 108                             # evol 매핑


def test_overseas_minute_bars_missing_output2_fails_closed():
    class Bad:
        def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
            return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}})
    from kis_trader.errors import KISError
    with pytest.raises(KISError):
        _client(Bad()).overseas.stock("TSLA", exchange="NAS").bars(interval="1m")


@pytest.mark.parametrize(
    ("local_day", "local_time", "korean_day", "korean_time"),
    [
        ("20240222", "093000", "20240222", "233000"),
        ("20240722", "093000", "20240722", "223000"),
        ("20240222", "160000", "20240223", "060000"),
    ],
)
def test_overseas_minute_timestamp_uses_korean_fields(
    local_day, local_time, korean_day, korean_time,
):
    row = _min_row(local_day, local_time)
    row.update(kymd=korean_day, khms=korean_time)
    fake = FakeTransport(response=_resp([row]))
    bar = _client(fake).overseas.stock("TSLA", exchange="NAS").bars("1m", max_bars=1)[0]
    expected = datetime.strptime(korean_day + korean_time, "%Y%m%d%H%M%S").replace(
        tzinfo=timezone(timedelta(hours=9)),
    )
    assert bar.timestamp == expected
    assert bar.timestamp.utcoffset() == timedelta(hours=9)


def test_overseas_minute_korean_timestamps_keep_local_pagination():
    local_times = [f"2024022215{minute:02d}00" for minute in range(6)]
    korean_times = [f"2024022305{minute:02d}00" for minute in range(6)]
    minutes = {}
    for local, korean in zip(local_times, korean_times, strict=True):
        row = _min_row(local[:8], local[8:])
        row.update(kymd=korean[:8], khms=korean[8:])
        minutes[local] = row
    fake = MinuteFakeTransport(minutes)
    bars = _client(fake).overseas.stock("TSLA", exchange="NAS").bars("1m")
    assert [f"{bar.timestamp:%Y%m%d%H%M%S}" for bar in bars] == korean_times
    assert [call["params"]["KEYB"] for call in fake.calls] == [
        "", "20240222150200", "20240222145900",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kymd", None), ("khms", None),
        ("kymd", ""), ("khms", "   "),
        ("kymd", "20240230"), ("khms", "246000"),
        ("kymd", "2024022"), ("khms", "93000"),
        ("kymd", "not-date"), ("khms", "abcdef"),
    ],
)
def test_overseas_minute_requires_valid_korean_timestamp(field, value):
    row = _min_row("20240222", "093000")
    if value is None:
        row.pop(field)
    else:
        row[field] = value
    fake = FakeTransport(response=_resp([row]))
    with pytest.raises(KISError):
        _client(fake).overseas.stock("TSLA", exchange="NAS").bars("1m", max_bars=1)


@pytest.mark.parametrize("field", ["xymd", "xhms", "last"])
def test_overseas_minute_blank_bar_still_skipped_without_korean_fields(field):
    row = _min_row("20240222", "093000")
    row[field] = ""
    row.pop("kymd")
    row.pop("khms")
    fake = FakeTransport(response=_resp([row]))
    assert _client(fake).overseas.stock("TSLA", exchange="NAS").bars("1m") == []
