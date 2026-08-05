"""채권 핸들 -- kis.bond(code).quote().

시장구분 B 라우팅, output 파싱(가격·시고저·전일대비·수익률), 전일대비 부호 복원,
optional 수익률(None), fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import BondQuote, KisClient
from kis_openapi.errors import KisError
from kis_openapi.transport import RawResponse

_PRICE = "/uapi/domestic-bond/v1/quotations/inquire-price"


def _output(*, prpr="10250.0", oprc="10240.0", hgpr="10260.0", lwpr="10235.0",
            clpr="10230.0", vrss="20.0", sign="2", ctrt="0.20", vol="1500",
            ernn="3.85"):
    return {"hts_kor_isnm": "국고03750-3312", "bond_prpr": prpr, "bond_oprc": oprc,
            "bond_hgpr": hgpr, "bond_lwpr": lwpr, "bond_prdy_clpr": clpr,
            "bond_prdy_vrss": vrss, "prdy_vrss_sign": sign, "prdy_ctrt": ctrt,
            "acml_vol": vol, "ernn_rate": ernn}


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
    return KisClient(app_key="k", app_secret="s", transport=transport)


def test_bond_quote_maps_fields_and_market():
    fake = FakeTransport(response=_resp(_output()))
    quote = _client(fake).bond("KR2033022D33").quote()
    assert isinstance(quote, BondQuote)
    assert quote.code == "KR2033022D33"
    assert quote.price == Decimal("10250.0")
    assert quote.open == Decimal("10240.0")
    assert quote.high == Decimal("10260.0")
    assert quote.low == Decimal("10235.0")
    assert quote.previous_close == Decimal("10230.0")
    assert quote.change == Decimal("20.0")
    assert quote.change_percent == Decimal("0.20")
    assert quote.volume == 1500
    assert quote.yield_rate == Decimal("3.85")             # 수익률(%)
    call = fake.calls[0]
    assert call["path"] == _PRICE
    assert call["tr_id"] == "FHKBJ773400C0"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "B"   # 채권
    assert call["params"]["FID_INPUT_ISCD"] == "KR2033022D33"


def test_bond_quote_negative_change():
    fake = FakeTransport(response=_resp(_output(vrss="15.0", sign="5", ctrt="0.15")))
    quote = _client(fake).bond("KR2033022D33").quote()
    assert quote.change == Decimal("-15.0")                 # 하락 -> 음수
    assert quote.change_percent == Decimal("-0.15")


def test_bond_quote_optional_yield_none():
    fake = FakeTransport(response=_resp(_output(ernn="")))
    quote = _client(fake).bond("KR2033022D33").quote()
    assert quote.yield_rate is None
    assert quote.price == Decimal("10250.0")               # 핵심 필드는 여전히 파싱


def test_bond_quote_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KisError):
        _client(fake).bond("KR2033022D33").quote()


def test_bond_quote_bad_value_fails_closed():
    fake = FakeTransport(response=_resp(_output(prpr="n/a")))
    with pytest.raises(KisError):
        _client(fake).bond("KR2033022D33").quote()
