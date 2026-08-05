"""종목 기본정보 -- kis.ticker(code).info()."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, StockInfo
from kis_openapi.errors import KISError
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


def _client(t):
    return KISClient(app_key="k", app_secret="s", transport=t)


def test_stock_info_maps():
    out = {"prdt_name": "삼성전자", "prdt_abrv_name": "삼성전자", "prdt_eng_name": "SAMSUNG ELEC",
           "lstg_stqt": "5969782550", "cpta": "897514000000", "papr": "100", "issu_pric": "0",
           "idx_bztp_lcls_cd_name": "전기전자", "idx_bztp_mcls_cd_name": "반도체",
           "idx_bztp_scls_cd_name": "메모리", "kospi200_item_yn": "Y", "stck_kind_cd": "0",
           "scts_mket_lstg_dt": "19750611"}
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": out}))
    info = _client(fake).ticker("005930").info()
    assert isinstance(info, StockInfo)
    assert info.name == "삼성전자"
    assert info.listed_shares == 5969782550
    assert info.capital == Decimal(897514000000)
    assert info.par_value == Decimal(100)
    assert info.sector_large == "전기전자"
    assert info.is_kospi200 is True
    assert f"{info.listing_date:%Y%m%d}" == "19750611"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/search-stock-info"
    assert call["tr_id"] == "CTPF1002R"
    assert call["params"]["PRDT_TYPE_CD"] == "300"
    assert call["params"]["PDNO"] == "005930"


def test_stock_info_optional_none():
    out = {"prdt_name": "x", "prdt_abrv_name": "x", "prdt_eng_name": "x", "lstg_stqt": "",
           "cpta": "", "papr": "", "issu_pric": "", "idx_bztp_lcls_cd_name": "",
           "idx_bztp_mcls_cd_name": "", "idx_bztp_scls_cd_name": "", "kospi200_item_yn": "N",
           "stck_kind_cd": "", "scts_mket_lstg_dt": "", "kosdaq_mket_lstg_dt": ""}
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": out}))
    info = _client(fake).ticker("005930").info()
    assert info.listed_shares is None
    assert info.listing_date is None
    assert info.is_kospi200 is False


def test_stock_info_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").info()
