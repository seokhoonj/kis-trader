"""체결(time & sales) -- kis.ticker(...).trades().

행위중심 표면(`quotations.inquire_ccnl` 이 아니라 `ticker.trades()`)과 fail-closed 파싱, 전일대비
부호 복원, 빈 행 skip 을 가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KisClient, Trade
from kis_openapi.errors import KisError
from kis_openapi.transport import RawResponse

_TRADES_PATH = "/uapi/domestic-stock/v1/quotations/inquire-ccnl"


def _row(*, hour="093000", price="71500", volume="10",
         change="600", change_percent="0.85", sign="2"):
    return {"stck_cntg_hour": hour, "stck_prpr": price, "cntg_vol": volume,
            "prdy_vrss": change, "prdy_ctrt": change_percent, "prdy_vrss_sign": sign}


class FakeTransport:
    def __init__(self, *, response=None):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent})
        assert self.response is not None
        return self.response


def _trades_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def _client(transport):
    return KisClient(app_key="k", app_secret="s", transport=transport)


def test_trades_parses_rows_newest_first():
    fake = FakeTransport(response=_trades_resp([_row(hour="093015", price="71500", volume="12"),
                                               _row(hour="093010", price="71400", volume="5")]))
    trades = _client(fake).ticker("005930").trades()
    assert [t.price for t in trades] == [Decimal(71500), Decimal(71400)]
    assert all(isinstance(t, Trade) for t in trades)
    first = trades[0]
    assert first.symbol == "005930"
    assert first.volume == 12
    assert first.timestamp.hour == 9 and first.timestamp.minute == 30 and first.timestamp.second == 15
    call = fake.calls[0]
    assert call["path"] == _TRADES_PATH
    assert call["tr_id"] == "FHKST01010300"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "J"      # KRX
    assert call["idempotent"] is True


def test_trades_restores_down_sign():
    fake = FakeTransport(response=_trades_resp([_row(change="600", change_percent="0.85", sign="5")]))
    trade = _client(fake).ticker("005930").trades()[0]
    assert trade.change == Decimal(-600)                        # 하락(5) -> 음수
    assert trade.change_percent == Decimal("-0.85")


def test_trades_skips_empty_rows():
    fake = FakeTransport(response=_trades_resp([_row(), {"stck_cntg_hour": "", "stck_prpr": ""}]))
    assert len(_client(fake).ticker("005930").trades()) == 1


def test_trades_bad_time_fails_closed():
    fake = FakeTransport(response=_trades_resp([_row(hour="99xx99")]))
    with pytest.raises(KisError):
        _client(fake).ticker("005930").trades()


def test_trades_missing_output_block_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={}))
    with pytest.raises(KisError):
        _client(fake).ticker("005930").trades()


def test_trades_error_response_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="1", msg_cd="MCA05918", msg1="종목코드 오류", body={}))
    with pytest.raises(KisError):
        _client(fake).ticker("005930").trades()
