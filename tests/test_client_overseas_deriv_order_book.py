"""해외 선물/옵션 핸들 -- kis.overseas.futures/option(srs_cd).order_book().

선물/옵션 호가 URL·TR 라우팅, output2 객체 배열의 5단계 파싱, 공백 패딩, 빈 단계 생략,
표시 잔량 합계와 손상 응답의 fail-closed 처리를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient
from kis_openapi.errors import KISError
from kis_openapi.order_book import OrderBook
from kis_openapi.transport import RawResponse

_FUT = "/uapi/overseas-futureoption/v1/quotations/inquire-asking-price"
_OPT = "/uapi/overseas-futureoption/v1/quotations/opt-asking-price"


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


def _response(*, rows=None):
    if rows is None:
        rows = [
            {
                "bid_qntt": "        35", "bid_num": "        11",
                "bid_price": "         6443.0", "ask_qntt": "        11",
                "ask_num": "         7", "ask_price": "         6443.5",
            },
            {
                "bid_qntt": "       108", "bid_num": "        25",
                "bid_price": "         6442.5", "ask_qntt": "       137",
                "ask_num": "        23", "ask_price": "         6444.0",
            },
            {
                "bid_qntt": "999", "bid_num": "0", "bid_price": "",
                "ask_qntt": "999", "ask_num": "0", "ask_price": "0",
            },
        ]
    body = {
        "output1": {"last_price": "6443.5", "vol": "27383"},
        "output2": rows,
    }
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


def test_overseas_futures_order_book_maps_array_and_routes():
    fake = FakeTransport(response=_response())
    book = _client(fake).overseas.futures("6AM24").order_book()

    assert isinstance(book, OrderBook)
    assert book.symbol == "6AM24"
    assert book.market == "future"
    assert len(book.bids) == 2
    assert len(book.asks) == 2
    assert book.bids[0].price == Decimal("6443.0")
    assert book.bids[0].quantity == 35
    assert book.asks[0].price == Decimal("6443.5")
    assert book.asks[0].quantity == 11
    assert book.bids[1].price == Decimal("6442.5")
    assert book.asks[1].price == Decimal("6444.0")
    assert book.total_bid_quantity == 143
    assert book.total_ask_quantity == 148
    assert book._raw["output1"]["vol"] == "27383"
    call = fake.calls[0]
    assert call["path"] == _FUT
    assert call["tr_id"] == "HHDFC86000000"
    assert call["params"] == {"SRS_CD": "6AM24"}


def test_overseas_option_order_book_routes_to_opt_endpoint():
    fake = FakeTransport(response=_response())
    _client(fake).overseas.option("OTXM24 C22000").order_book()
    assert fake.calls[0]["path"] == _OPT
    assert fake.calls[0]["tr_id"] == "HHDFO86000000"


@pytest.mark.parametrize("output2", [None, {}, "not-a-list"])
def test_overseas_derivative_order_book_missing_array_fails_closed(output2):
    body = {} if output2 is None else {"output2": output2}
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body=body)
    with pytest.raises(KISError):
        _client(FakeTransport(response=response)).overseas.futures("6AM24").order_book()


def test_overseas_derivative_order_book_bad_quantity_fails_closed():
    rows = [{
        "bid_qntt": "n/a", "bid_price": "6443.0",
        "ask_qntt": "11", "ask_price": "6443.5",
    }]
    with pytest.raises(KISError):
        _client(FakeTransport(response=_response(rows=rows))).overseas.futures(
            "6AM24"
        ).order_book()
