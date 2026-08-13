"""해외주식 호가창 -- kis.overseas.stock(symbol, exchange=...).order_book().

inquire-asking-price 엔드포인트, output1(총잔량)+output2(단계) 파싱, 미국 다단계/그 외 1단계,
빈(0) 단계 skip, fail-closed 를 검증한다. 도메스틱과 같은 OrderBook 타입.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, OrderBook
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse

_OVERSEAS_ASKING = "/uapi/overseas-price/v1/quotations/inquire-asking-price"


def _levels(**over):
    """빈 10단계에서 시작해 지정 단계만 채운다(pbidN/paskN/vbidN/vaskN)."""
    out = {}
    for step in range(1, 11):
        out[f"pbid{step}"] = "0"
        out[f"pask{step}"] = "0"
        out[f"vbid{step}"] = "0"
        out[f"vask{step}"] = "0"
    out.update(over)
    return out


def _resp(*, output1, output2):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": output1, "output2": output2})


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


def test_overseas_order_book_us_multiple_levels():
    output2 = _levels(
        pbid1="146.90", vbid1="100", pbid2="146.80", vbid2="250",
        pask1="147.00", vask1="120", pask2="147.10", vask2="300",
    )
    fake = FakeTransport(response=_resp(output1={"bvol": "350", "avol": "420"}, output2=output2))
    book = _client(fake).overseas.stock("AAPL", exchange="NAS").order_book()
    assert isinstance(book, OrderBook)
    assert book.market == "NAS"
    assert [(lvl.price, lvl.quantity) for lvl in book.bids] == [
        (Decimal("146.90"), 100), (Decimal("146.80"), 250)]
    assert [(lvl.price, lvl.quantity) for lvl in book.asks] == [
        (Decimal("147.00"), 120), (Decimal("147.10"), 300)]
    assert book.total_bid_quantity == 350
    assert book.total_ask_quantity == 420
    call = fake.calls[0]
    assert call["path"] == _OVERSEAS_ASKING
    assert call["tr_id"] == "HHDFS76200100"
    assert call["params"]["EXCD"] == "NAS"
    assert call["params"]["SYMB"] == "AAPL"


def test_overseas_order_book_non_us_single_level():
    # 미국 외: 1단계만 채워지고 나머지는 0 -> 한 단계만.
    output2 = _levels(pbid1="61000", vbid1="10", pask1="61050", vask1="12")
    fake = FakeTransport(response=_resp(output1={"bvol": "10", "avol": "12"}, output2=output2))
    book = _client(fake).overseas.stock("0700", exchange="HKS").order_book()
    assert len(book.bids) == 1
    assert len(book.asks) == 1
    assert book.asks[0].price == Decimal(61050)


def test_overseas_order_book_missing_output2_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output1": {"bvol": "1", "avol": "1"}}))
    with pytest.raises(KISError):
        _client(fake).overseas.stock("AAPL", exchange="NAS").order_book()
