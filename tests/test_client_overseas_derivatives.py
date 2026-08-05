"""해외 선물/옵션 핸들 -- kis.overseas_futures/option(srs_cd).quote().

선물/옵션 URL·TR 라우팅(HHDFC55010000 / HHDFO55010000), output1 파싱(공백 패딩 값·통화·거래소·
만기·정산가·전일대비 부호 복원), optional 필드(None), fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, OverseasDerivativeQuote
from kis_openapi.errors import KISError
from kis_openapi.transport import RawResponse

_FUT = "/uapi/overseas-futureoption/v1/quotations/inquire-price"
_OPT = "/uapi/overseas-futureoption/v1/quotations/opt-price"


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


def _output(**over):
    # 값에 공백 패딩이 오는 실제 응답을 흉내낸다(Decimal/int 가 strip).
    out = {
        "open_price": "          75.55", "high_price": "          75.61",
        "low_price": "          74.66", "last_price": "          74.90",
        "prev_price": "          75.57", "sttl_price": "          74.88",
        "vol": "33004", "prev_diff_flag": "5", "prev_diff_price": "           0.67",
        "prev_diff_rate": "     -0.89", "bid_qntt": "         7", "bid_price": "          74.89",
        "ask_qntt": "         4", "ask_price": "          74.90", "trst_mgn": "               3670",
        "exch_cd": "ICE", "crc_cd": "USD", "expr_date": "20251219",
        "trd_to_date": "20251218", "remn_cnt": "41", "tick_size": "0.01",
        "tot_ask_qntt": "100", "tot_bid_qntt": "120",
    }
    out.update(over)
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": out})


def test_overseas_futures_quote_maps_and_routes():
    fake = FakeTransport(response=_output())
    quote = _client(fake).overseas_futures("BONU25").quote()
    assert isinstance(quote, OverseasDerivativeQuote)
    assert quote.symbol == "BONU25"
    assert quote.last == Decimal("74.90")                 # 공백 패딩 strip
    assert quote.open == Decimal("75.55")
    assert quote.previous_close == Decimal("75.57")
    assert quote.settlement_price == Decimal("74.88")
    assert quote.change == Decimal("-0.67")               # flag 5(하락) -> 음수
    assert quote.change_percent == Decimal("-0.89")
    assert quote.volume == 33004
    assert quote.bid == Decimal("74.89")
    assert quote.ask == Decimal("74.90")
    assert quote.bid_size == 7
    assert quote.currency == "USD"
    assert quote.exchange == "ICE"
    assert f"{quote.expiry_date:%Y%m%d}" == "20251219"
    assert quote.remaining_days == 41
    assert quote.tick_size == Decimal("0.01")
    assert quote.margin == Decimal(3670)
    call = fake.calls[0]
    assert call["path"] == _FUT
    assert call["tr_id"] == "HHDFC55010000"
    assert call["params"]["SRS_CD"] == "BONU25"


def test_overseas_option_quote_routes_to_opt_endpoint():
    fake = FakeTransport(response=_output())
    _client(fake).overseas_option("ESZ25 C5000").quote()
    assert fake.calls[0]["path"] == _OPT
    assert fake.calls[0]["tr_id"] == "HHDFO55010000"


def test_overseas_derivative_quote_positive_change():
    fake = FakeTransport(response=_output(prev_diff_flag="2", prev_diff_price="1.10",
                                          prev_diff_rate="1.47"))
    quote = _client(fake).overseas_futures("BONU25").quote()
    assert quote.change == Decimal("1.10")                # flag 2(상승) -> 양수
    assert quote.change_percent == Decimal("1.47")


def test_overseas_derivative_quote_optional_none():
    fake = FakeTransport(response=_output(sttl_price="", tick_size="", expr_date=""))
    quote = _client(fake).overseas_futures("BONU25").quote()
    assert quote.settlement_price is None
    assert quote.tick_size is None
    assert quote.expiry_date is None
    assert quote.last == Decimal("74.90")                 # 핵심 필드는 여전히 파싱


def test_overseas_derivative_quote_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).overseas_futures("BONU25").quote()


def test_overseas_derivative_quote_bad_value_fails_closed():
    fake = FakeTransport(response=_output(last_price="n/a"))
    with pytest.raises(KISError):
        _client(fake).overseas_futures("BONU25").quote()
