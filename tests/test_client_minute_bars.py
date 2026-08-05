"""당일 1분봉 -- kis.ticker(...).bars(interval="1m").

일봉과 같은 `bars()` 로 통합하되 분봉은 당일 세션(시각기준 페이지네이션)이라는 점, close=stck_prpr
/volume=cntg_vol 매핑, 개장까지 페이지네이션, max_bars, fail-closed 를 가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_MINUTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"


def _bar(hhmmss, *, close="71500", vol="100"):
    return {"stck_bsop_date": "20240102", "stck_cntg_hour": hhmmss, "stck_prpr": close,
            "stck_oprc": "71000", "stck_hgpr": "71800", "stck_lwpr": "70900", "cntg_vol": vol}


class FakeTransport:
    """FID_INPUT_HOUR_1 기준으로 그 시각 이하 분봉을 최대 3건 돌려주는 가짜 전송(당일)."""

    def __init__(self, minutes):
        # minutes: "HHMMSS" -> row, 시간 오름차순으로 준비된 하루치
        self.minutes = dict(sorted(minutes.items()))
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        anchor = params["FID_INPUT_HOUR_1"]
        at_or_before = [t for t in self.minutes if t <= anchor]
        page = [self.minutes[t] for t in at_or_before[-3:]]        # 최신 3건(내림 아님, 배열만)
        return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": page})


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def _session(times):
    return {t: _bar(t, close=str(70000 + i)) for i, t in enumerate(times)}


def test_minute_bars_paginate_to_open_ascending():
    times = [f"09{m:02d}00" for m in range(9)]                  # 0900..0908, 9개
    fake = FakeTransport(_session(times))
    bars = _client(fake).ticker("005930").bars(interval="1m")
    assert [f"{b.timestamp:%H%M%S}" for b in bars] == times        # 과거->현재 오름차순, 전량
    assert len(fake.calls) >= 2                                    # 3건/page -> 최소 3페이지로 9건
    assert fake.calls[0]["path"] == _MINUTE_PATH
    assert fake.calls[0]["tr_id"] == "FHKST03010200"
    assert fake.calls[0]["params"]["FID_INPUT_HOUR_1"] == "235959"  # 미래시각=현재로 클램프 -> 최신부터
    # 2페이지째 기준시각 = 1페이지 최소시각(0906)에서 1분 전(0905)
    assert fake.calls[1]["params"]["FID_INPUT_HOUR_1"] == "090500"


def test_minute_bar_maps_close_and_volume():
    fake = FakeTransport({"090000": _bar("090000", close="71234", vol="55")})
    bar = _client(fake).ticker("005930").bars(interval="1m")[0]
    assert bar.close == Decimal(71234)                            # 분봉 종가 = stck_prpr
    assert bar.volume == 55                                       # 분당 거래량 = cntg_vol
    assert bar.open == Decimal(71000)


def test_minute_bars_respects_max_bars():
    times = [f"09{m:02d}00" for m in range(9)]
    fake = FakeTransport(_session(times))
    bars = _client(fake).ticker("005930").bars(interval="1m", max_bars=4)
    assert len(bars) == 4
    assert [f"{b.timestamp:%H%M%S}" for b in bars] == times[-4:]   # 가장 최근 4개


def test_minute_bars_ignore_start_end():
    fake = FakeTransport({"090000": _bar("090000")})
    # start/end 를 줘도 분봉은 당일 기준 -- 예외 없이 동작
    bars = _client(fake).ticker("005930").bars(interval="1m", start="20200101", end="20200102")
    assert len(bars) == 1


def test_period_bars_still_require_start():
    fake = FakeTransport({})
    with pytest.raises(KISUsageError):
        _client(fake).ticker("005930").bars(interval="1d")        # 기간봉엔 start 필수


def test_minute_bars_bad_time_fails_closed():
    fake = FakeTransport({"090000": _bar("bad")})
    with pytest.raises(KISError):
        _client(fake).ticker("005930").bars(interval="1m")


def test_minute_bars_missing_block_fails_closed():
    class Bad:
        def request(self, **kw):
            return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={})  # output2 없음

    with pytest.raises(KISError):
        _client(Bad()).ticker("005930").bars(interval="1m")


class _StaticTransport:
    """anchor 와 무관하게 매번 같은 output2 를 돌려주는 가짜 전송(종료조건 격리 검증용)."""

    def __init__(self, rows, *, rt_cd="0"):
        self.rows = rows
        self.rt_cd = rt_cd
        self.calls: list[dict] = []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        self.calls.append({"params": params})
        return RawResponse(rt_cd=self.rt_cd, msg_cd="X", msg1="ok", body={"output2": self.rows})


def test_minute_bars_raise_when_page_cap_reached_before_session_open():
    # 매 페이지 anchor 시각 자체를 새 봉으로 돌려주면 개장까지 못 미쳐 상한(60p)에서 fail-closed.
    class AnchorEcho:
        def __init__(self):
            self.calls: list[dict] = []

        def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
            self.calls.append({"params": params})
            hh = params["FID_INPUT_HOUR_1"]
            return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output2": [_bar(hh)]})

    fake = AnchorEcho()
    with pytest.raises(KISError):
        _client(fake).ticker("005930").bars(interval="1m")
    assert len(fake.calls) == 60                           # _MAX_MINUTE_PAGES 만큼 돌고 중단


def test_minute_bars_error_response_fails_closed():
    fake = _StaticTransport([], rt_cd="1")                 # 비성공 응답을 빈 페이지로 오인 금지
    with pytest.raises(KISError):
        _client(fake).ticker("005930").bars(interval="1m")


def test_minute_bars_anchor_rolls_over_hour_boundary():
    # 1페이지 최소=100000 -> 2페이지 anchor 는 정시경계를 넘겨 095900 이어야 한다(1분 전).
    fake = FakeTransport({t: _bar(t) for t in ("100000", "100100", "100200")})
    _client(fake).ticker("005930").bars(interval="1m")
    assert fake.calls[0]["params"]["FID_INPUT_HOUR_1"] == "235959"
    assert fake.calls[1]["params"]["FID_INPUT_HOUR_1"] == "095900"


def test_minute_bars_stop_when_next_page_has_no_fresh_bars():
    fake = _StaticTransport([_bar("091000")])              # 개장 이후 같은 봉만 반복
    bars = _client(fake).ticker("005930").bars(interval="1m")
    assert len(fake.calls) == 2                            # 2페이지째 새 봉 없음 -> 중단(무한루프 방지)
    assert len(bars) == 1                                  # 중복 제거


def test_minute_bars_skip_empty_rows():
    fake = _StaticTransport([_bar("090000"), {"stck_bsop_date": "", "stck_cntg_hour": "",
                                              "stck_prpr": ""}])
    bars = _client(fake).ticker("005930").bars(interval="1m")
    assert len(bars) == 1                                  # 빈 행 skip, 090000 도달로 종료
