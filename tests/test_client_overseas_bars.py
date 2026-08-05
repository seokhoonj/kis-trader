"""해외주식 기간봉 -- kis.ticker(symbol, exchange=...).bars().

dailyprice 엔드포인트, GUBN(일/주/월) 매핑, MODP(수정주가), BYMD 앵커 walk-back 페이지네이션,
오름차순 정렬, 분봉 미지원을 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import Bar, KISClient
from kis_openapi.transport import RawResponse

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
    return KISClient(app_key="k", app_secret="s", transport=transport)


def test_overseas_bars_parses_ascending_and_params():
    fake = FakeTransport(response=_resp([
        _bar_row("20240104", "149.0", "151.0", "148.5", "150.25", "500"),
        _bar_row("20240103", "147.0", "149.5", "146.5", "148.00", "480"),
        _bar_row("20240102", "145.0", "147.5", "144.5", "146.50", "460"),
    ]))
    bars = _client(fake).ticker("AAPL", exchange="NAS").bars(start="20240102")
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
        _client(fake).ticker("AAPL", exchange="NAS").bars(start="20240105", interval=interval)
        assert fake.calls[0]["params"]["GUBN"] == gubn
    fake = FakeTransport(response=_resp([_bar_row("20240105", "1", "1", "1", "1", "1")]))
    _client(fake).ticker("AAPL", exchange="NAS").bars(start="20240105", adjusted=False)
    assert fake.calls[0]["params"]["MODP"] == "0"


def test_overseas_bars_paginates_by_base_date():
    page_a = _resp([_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1") for n in (8, 7, 6, 5)])
    page_b = _resp([_bar_row(f"2024010{n}", "1", "1", "1", str(n), "1") for n in (5, 4, 3, 2, 1)])
    fake = FakeTransport(by_path={_OVERSEAS_BARS: [page_a, page_b]})
    bars = _client(fake).ticker("AAPL", exchange="NAS").bars(start="20240101")
    assert [f"{b.timestamp:%Y%m%d}" for b in bars] == [f"2024010{n}" for n in range(1, 9)]
    assert isinstance(bars[0], Bar)
    # 2페이지째 BYMD 는 첫 페이지 최소일(20240105) 하루 전(20240104).
    assert fake.calls[1]["params"]["BYMD"] == "20240104"


def test_overseas_bars_minute_not_implemented():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(NotImplementedError):
        _client(fake).ticker("AAPL", exchange="NAS").bars(interval="1m")
