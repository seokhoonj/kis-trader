"""해외주식 현재가 -- kis.ticker(symbol, exchange=...).quote().

해외는 거래소코드로 라우팅, price-detail 엔드포인트, 통화(curr) 채움, 전일종가 대비 등락 계산,
그리고 해외 티커에서 아직 미구현 국내 메서드는 명확히 거부됨을 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, Quote
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_OVERSEAS_PRICE = "/uapi/overseas-price/v1/quotations/price-detail"


def _output(*, last="150.25", base="148.00", open_="149.00", high="151.00", low="148.50",
            tvol="52000000", h52="199.62", l52="124.17", curr="USD"):
    return {"last": last, "base": base, "open": open_, "high": high, "low": low, "tvol": tvol,
            "h52p": h52, "l52p": l52, "curr": curr, "zdiv": "4"}


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _resp(output):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": output})


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def test_overseas_quote_routes_and_maps():
    fake = FakeTransport(response=_resp(_output()))
    quote = _client(fake).ticker("AAPL", exchange="NAS").quote()
    assert isinstance(quote, Quote)
    assert quote.symbol == "AAPL"
    assert quote.market == "NAS"                  # 거래소코드
    assert quote.currency == "USD"
    assert quote.last == Decimal("150.25")
    assert quote.open == Decimal("149.00")
    assert quote.high == Decimal("151.00")
    assert quote.low == Decimal("148.50")
    assert quote.previous_close == Decimal("148.00")
    assert quote.change == Decimal("2.25")        # last - base
    assert quote.change_percent == Decimal("1.52")  # 2.25/148*100, 2자리 반올림
    assert quote.volume == 52000000
    assert quote.week_52_high == Decimal("199.62")
    assert quote.week_52_low == Decimal("124.17")
    call = fake.calls[0]
    assert call["path"] == _OVERSEAS_PRICE
    assert call["tr_id"] == "HHDFS76200200"
    assert call["params"]["EXCD"] == "NAS"
    assert call["params"]["SYMB"] == "AAPL"


def test_overseas_quote_negative_change():
    fake = FakeTransport(response=_resp(_output(last="145.00", base="148.00")))
    quote = _client(fake).ticker("AAPL", exchange="NAS").quote()
    assert quote.change == Decimal("-3.00")       # 하락
    assert quote.change_percent == Decimal("-2.03")


def test_overseas_quote_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).ticker("AAPL", exchange="NAS").quote()


def test_overseas_quote_bad_value_fails_closed():
    fake = FakeTransport(response=_resp(_output(last="n/a")))
    with pytest.raises(KISError):
        _client(fake).ticker("AAPL", exchange="NAS").quote()


def test_overseas_ticker_is_overseas_flag():
    fake = FakeTransport(response=_resp(_output()))
    assert _client(fake).ticker("AAPL", exchange="NAS").is_overseas is True
    assert _client(fake).ticker("005930").is_overseas is False


def test_bare_symbol_auto_resolves_exchange():
    from kis_openapi import MasterIndex, MasterRecord
    index = MasterIndex([MasterRecord("AAPL", "NAS", "USD", "stock", "애플", "APPLE", "NASAAPL")])
    fake = FakeTransport(response=_resp(_output()))
    client = KISClient(app_key="k", app_secret="s", transport=fake, master_index=index)
    handle = client.ticker("AAPL")                # exchange 없이 -> 마스터로 NAS 자동 해석
    assert handle.is_overseas is True
    assert handle.exchange == "NAS"
    quote = handle.quote()
    assert quote.market == "NAS"
    assert fake.calls[0]["params"]["EXCD"] == "NAS"


def test_bare_domestic_symbol_stays_domestic_without_master():
    # 6자리 숫자는 마스터를 거치지 않는다(네트워크/인덱스 불필요).
    def exploding_fetch(url):
        raise AssertionError("국내 심볼은 마스터를 받으면 안 된다")

    client = KISClient(app_key="k", app_secret="s", transport=FakeTransport(response=_resp(_output())),
                       master_fetch=exploding_fetch)
    handle = client.ticker("005930")
    assert handle.is_overseas is False
    assert handle.market == "KRX"


def test_overseas_ticker_rejects_domestic_only_methods():
    fake = FakeTransport(response=_resp(_output()))
    handle = _client(fake).ticker("AAPL", exchange="NAS")
    for call in (
        handle.investor_flows,
        handle.broker_activity,
        handle.nav,
        handle.components,
    ):
        with pytest.raises(KISUsageError, match="해외 티커"):
            call()
