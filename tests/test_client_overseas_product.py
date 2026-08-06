"""해외 종목 상품기본정보 조회."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse


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


def _response(output):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": output})


APPLE = {
    "std_pdno": "US0378331005",
    "prdt_eng_name": "APPLE INC",
    "prdt_name": "애플",
    "ovrs_item_name": "",
    "natn_name": "미국",
    "ovrs_excg_cd": "NASD",
    "ovrs_excg_name": "나스닥",
    "tr_crcy_cd": "USD",
    "crcy_name": "미국달러",
    "ovrs_papr": "0.00000",
    "lstg_stck_num": "15441900000",
    "buy_unit_qty": "1",
    "sll_unit_qty": "1",
    "sedol_no": "2046251",
    "blbg_tckr_text": "AAPL US",
    "lstg_yn": "Y",
    "lstg_abol_item_yn": "N",
    "tax_levy_yn": "N",
}


def test_overseas_product_info_maps_real_apple_example():
    fake = FakeTransport(response=_response(APPLE))
    info = _client(fake).overseas_product_info("NAS", "AAPL")

    call = fake.calls[0]
    assert call["path"] == "/uapi/overseas-price/v1/quotations/search-info"
    assert call["tr_id"] == "CTPF1702R"
    assert call["params"]["PRDT_TYPE_CD"] == "512"
    assert call["params"]["PDNO"] == "AAPL"
    assert info.isin == "US0378331005"
    assert info.english_name == "APPLE INC"
    assert info.name == "애플"
    assert info.exchange_code == "NASD"
    assert info.currency == "USD"
    assert info.par_value == Decimal("0.00000")
    assert info.listed_shares == 15441900000
    assert info.sedol == "2046251"
    assert info.is_listed is True
    assert info.is_delisted is False
    assert info.taxable is False


def test_overseas_product_info_rejects_unknown_exchange():
    fake = FakeTransport(response=_response(APPLE))
    with pytest.raises(KISUsageError):
        _client(fake).overseas_product_info("XXX", "AAPL")
    assert fake.calls == []


def test_overseas_product_info_missing_output_fails_closed():
    fake = FakeTransport(
        response=RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={})
    )
    with pytest.raises(KISError):
        _client(fake).overseas_product_info("NAS", "AAPL")
