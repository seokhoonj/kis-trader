"""해외주식 체결(time & sales) -- kis.ticker(symbol, exchange=...).trades().

inquire-ccnl 엔드포인트, 한국기준시간(khms)+조회일 결합, 체결량(evol), 전일대비 부호 복원,
벤더 순서 유지, fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KisClient, Trade
from kis_openapi.errors import KisError
from kis_openapi.transport import RawResponse

_OVERSEAS_TRADES = "/uapi/overseas-price/v1/quotations/inquire-ccnl"


def _trade_row(khms, last, evol, diff, sign, rate):
    return {"khms": khms, "last": last, "evol": evol, "diff": diff, "sign": sign, "rate": rate}


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": rows})


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
    return KisClient(app_key="k", app_secret="s", transport=transport)


def test_overseas_trades_maps_fields_and_params():
    fake = FakeTransport(response=_resp([
        _trade_row("093015", "150.25", "120", "2.25", "2", "1.52"),
        _trade_row("093012", "150.10", "80", "2.10", "2", "1.42"),
    ]))
    trades = _client(fake).ticker("AAPL", exchange="NAS").trades()
    assert [t.symbol for t in trades] == ["AAPL", "AAPL"]     # 벤더 순서 유지(최신순)
    first = trades[0]
    assert isinstance(first, Trade)
    assert first.price == Decimal("150.25")
    assert first.quantity == 120                              # evol = 체결량
    assert first.change == Decimal("2.25")
    assert first.change_percent == Decimal("1.52")
    assert f"{first.timestamp:%H%M%S}" == "093015"            # khms 한국기준시간
    call = fake.calls[0]
    assert call["path"] == _OVERSEAS_TRADES
    assert call["tr_id"] == "HHDFS76200300"
    assert call["params"]["EXCD"] == "NAS"
    assert call["params"]["SYMB"] == "AAPL"
    assert call["params"]["TDAY"] == "1"                      # 당일


def test_overseas_trades_negative_change_sign():
    fake = FakeTransport(response=_resp([_trade_row("100000", "148.00", "50", "3.00", "5", "1.99")]))
    trade = _client(fake).ticker("AAPL", exchange="NAS").trades()[0]
    assert trade.change == Decimal("-3.00")                   # 하락 -> 음수
    assert trade.change_percent == Decimal("-1.99")


def test_overseas_trades_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KisError):
        _client(fake).ticker("AAPL", exchange="NAS").trades()
