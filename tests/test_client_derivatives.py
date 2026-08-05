"""선물/옵션 핸들 -- kis.futures(code).quote() / kis.option(code).quote().

시장구분 F/O 라우팅, output1 파싱(미결제약정·베이시스·이론가·괴리율), 전일대비 부호 복원,
optional 필드(None), fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import DerivativesQuote, KisClient, OrderBook
from kis_openapi.errors import KisError
from kis_openapi.transport import RawResponse

_PRICE = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
_ASKING = "/uapi/domestic-futureoption/v1/quotations/inquire-asking-price"


def _output(*, last="335.20", oprc="334.10", hgpr="336.00", lwpr="333.50", clpr="333.00",
            vrss="2.20", sign="2", ctrt="0.66", vol="120000", oi="380000", thpr="335.05",
            basis="0.15", dprt="-0.04"):
    return {"hts_kor_isnm": "K200 F 202409", "futs_prpr": last, "futs_oprc": oprc,
            "futs_hgpr": hgpr, "futs_lwpr": lwpr, "futs_prdy_clpr": clpr, "futs_prdy_vrss": vrss,
            "prdy_vrss_sign": sign, "futs_prdy_ctrt": ctrt, "acml_vol": vol,
            "hts_otst_stpl_qty": oi, "hts_thpr": thpr, "basis": basis, "dprt": dprt}


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
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": output})


def _client(transport):
    return KisClient(app_key="k", app_secret="s", transport=transport)


def test_futures_quote_maps_fields_and_market():
    fake = FakeTransport(response=_resp(_output()))
    quote = _client(fake).futures("101W09").quote()
    assert isinstance(quote, DerivativesQuote)
    assert quote.code == "101W09"
    assert quote.last == Decimal("335.20")
    assert quote.previous_close == Decimal("333.00")
    assert quote.change == Decimal("2.20")
    assert quote.change_percent == Decimal("0.66")
    assert quote.volume == 120000
    assert quote.open_interest == 380000               # 미결제약정
    assert quote.theoretical_price == Decimal("335.05")
    assert quote.basis == Decimal("0.15")
    assert quote.premium == Decimal("-0.04")
    call = fake.calls[0]
    assert call["path"] == _PRICE
    assert call["tr_id"] == "FHMIF10000000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "F"   # 지수선물
    assert call["params"]["FID_INPUT_ISCD"] == "101W09"


def test_option_quote_uses_o_market():
    fake = FakeTransport(response=_resp(_output()))
    _client(fake).option("201W09335").quote()
    assert fake.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"] == "O"   # 지수옵션


def test_derivatives_quote_negative_change():
    fake = FakeTransport(response=_resp(_output(vrss="1.50", sign="5", ctrt="0.45")))
    quote = _client(fake).futures("101W09").quote()
    assert quote.change == Decimal("-1.50")            # 하락 -> 음수
    assert quote.change_percent == Decimal("-0.45")


def test_derivatives_quote_optional_fields_none():
    fake = FakeTransport(response=_resp(_output(thpr="", basis="", dprt="")))
    quote = _client(fake).futures("101W09").quote()
    assert quote.theoretical_price is None
    assert quote.basis is None
    assert quote.premium is None
    assert quote.open_interest == 380000               # 핵심 필드는 여전히 파싱


def test_derivatives_quote_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KisError):
        _client(fake).futures("101W09").quote()


def test_derivatives_quote_bad_value_fails_closed():
    fake = FakeTransport(response=_resp(_output(last="n/a")))
    with pytest.raises(KisError):
        _client(fake).futures("101W09").quote()


# --- order_book (호가 사다리는 output2) -------------------------------------
def _book(**over):
    out = {
        "futs_askp1": "364.40", "futs_askp2": "364.45", "futs_askp3": "0",
        "futs_askp4": "0", "futs_askp5": "0",
        "askp_rsqn1": "35", "askp_rsqn2": "47", "askp_rsqn3": "0",
        "askp_rsqn4": "0", "askp_rsqn5": "0",
        "futs_bidp1": "364.35", "futs_bidp2": "364.30", "futs_bidp3": "364.25",
        "futs_bidp4": "0", "futs_bidp5": "0",
        "bidp_rsqn1": "22", "bidp_rsqn2": "70", "bidp_rsqn3": "68",
        "bidp_rsqn4": "0", "bidp_rsqn5": "0",
        "total_askp_rsqn": "7140", "total_bidp_rsqn": "9319",
    }
    out.update(over)
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {"hts_kor_isnm": "F 202409"}, "output2": out})


def test_futures_order_book_maps_output2_and_market():
    fake = FakeTransport(response=_book())
    book = _client(fake).futures("101W09").order_book()
    assert isinstance(book, OrderBook)
    assert book.symbol == "101W09"
    assert book.market == "F"
    assert [(lvl.price, lvl.quantity) for lvl in book.asks] == [
        (Decimal("364.40"), 35), (Decimal("364.45"), 47),
    ]                                                          # 0-가격 단계 skip
    assert [(lvl.price, lvl.quantity) for lvl in book.bids] == [
        (Decimal("364.35"), 22), (Decimal("364.30"), 70), (Decimal("364.25"), 68),
    ]
    assert book.total_ask_quantity == 7140
    assert book.total_bid_quantity == 9319
    call = fake.calls[0]
    assert call["path"] == _ASKING
    assert call["tr_id"] == "FHMIF10010000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "F"


def test_option_order_book_uses_o_market():
    fake = FakeTransport(response=_book())
    _client(fake).option("201W09335").order_book()
    assert fake.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"] == "O"


def test_derivatives_order_book_missing_output2_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                       body={"output1": {"hts_kor_isnm": "F"}})    # output2 없음
    fake = FakeTransport(response=resp)
    with pytest.raises(KisError):
        _client(fake).futures("101W09").order_book()
