"""해외주식 체결(time & sales) -- kis.overseas.stock(symbol, exchange=...).trades().

inquire-ccnl 엔드포인트, 한국기준시간(khms)+조회일 결합, 체결량(evol), 전일대비 부호 복원,
벤더 순서 유지, fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, Trade
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse

_OVERSEAS_TRADES = "/uapi/overseas-price/v1/quotations/inquire-ccnl"


def _trade_row(khms, last, evol, diff, sign, rate):
    return {"khms": khms, "last": last, "evol": evol, "diff": diff, "sign": sign, "rate": rate}


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": rows})


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
    return KISClient(app_key="k", app_secret="s", environment="real", transport=transport)


def test_overseas_trades_maps_fields_and_params():
    fake = FakeTransport(response=_resp([
        _trade_row("093015", "150.25", "120", "2.25", "2", "1.52"),
        _trade_row("093012", "150.10", "80", "2.10", "2", "1.42"),
    ]))
    trades = _client(fake).overseas.stock("AAPL", exchange="NAS").trades()
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
    trade = _client(fake).overseas.stock("AAPL", exchange="NAS").trades()[0]
    assert trade.change == Decimal("-3.00")                   # 하락 -> 음수
    assert trade.change_percent == Decimal("-1.99")


def test_overseas_trades_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).overseas.stock("AAPL", exchange="NAS").trades()


def test_parse_trades_rolls_future_timestamp_back_a_day():
    # 미국 정규장은 KST 자정을 넘기므로(22:30~06:00 KST), 자정~개장 사이에 조회하면 '오늘' 날짜를
    # 붙인 전날 체결이 미래로 찍힌다. 결합 시각이 now 보다 미래면 전날로 롤백해야 한다.
    from datetime import datetime

    from kis_trader._internal._datetime import _KST
    from kis_trader.overseas._engine.market_data import _parse_trades

    now = datetime(2026, 8, 14, 3, 0, tzinfo=_KST)   # 03:00 KST (미국 전날장이 자정 넘겨 진행 중)
    rows = [
        {"khms": "233000", "last": "230.0", "evol": "5", "diff": "1.0", "rate": "0.5", "sign": "2"},  # 23:30 -> 미래
        {"khms": "020000", "last": "231.0", "evol": "3", "diff": "1.0", "rate": "0.5", "sign": "2"},  # 02:00 -> 오늘
    ]
    trades = _parse_trades(rows, symbol="AAPL", today="20260814", now=now)
    assert trades[0].timestamp == datetime(2026, 8, 13, 23, 30, tzinfo=_KST)   # 전날로 롤백
    assert trades[1].timestamp == datetime(2026, 8, 14, 2, 0, tzinfo=_KST)     # 미래 아님 -> 그대로


def test_parse_trades_does_not_roll_back_on_seconds_of_clock_skew():
    # 로컬 시계가 KIS 서버보다 몇 초 뒤처져 방금 체결이 now 를 1~2초 앞서는 경우는 롤백하면 안 된다 --
    # 1시간 슬랙이 없으면 신선 체결이 24시간 과거로 밀려 시계열이 오염된다.
    from datetime import datetime

    from kis_trader._internal._datetime import _KST
    from kis_trader.overseas._engine.market_data import _parse_trades

    now = datetime(2026, 8, 14, 2, 58, 59, tzinfo=_KST)  # 로컬 시계가 서버보다 ~1초 lag
    rows = [{"khms": "025900", "last": "231.0", "evol": "3", "diff": "1.0", "rate": "0.5", "sign": "2"}]
    trades = _parse_trades(rows, symbol="AAPL", today="20260814", now=now)
    assert trades[0].timestamp == datetime(2026, 8, 14, 2, 59, tzinfo=_KST)    # 오늘 그대로(롤백 금지)
