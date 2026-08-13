"""주식·파생·채권·해외주식 공통 상품기본조회."""

from __future__ import annotations

from datetime import date

import pytest

from kis_trader import KISClient, ProductInfo
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _client(response):
    return KISClient(app_key="k", app_secret="s", transport=FakeTransport(response))


def _row():
    return {
        "pdno": "005930", "prdt_type_cd": "300", "prdt_name": "삼성전자",
        "prdt_name120": "삼성전자 보통주", "prdt_abrv_name": "삼성전자",
        "prdt_eng_name": "Samsung Electronics", "prdt_eng_name120": "Samsung Electronics Co Ltd",
        "prdt_eng_abrv_name": "SamsungElec", "std_pdno": "KR7005930003",
        "shtn_pdno": "A005930", "prdt_sale_stat_cd": "01", "prdt_risk_grad_cd": "2",
        "prdt_clsf_cd": "01", "prdt_clsf_name": "주식", "sale_strt_dt": "19750611",
        "sale_end_dt": "", "wrap_asst_type_cd": "01", "ivst_prdt_type_cd": "01",
        "ivst_prdt_type_cd_name": "주식", "frst_erlm_dt": "19750611",
    }


def test_product_info_maps_common_fields_and_params():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": _row()})
    client = _client(response)
    info = client.domestic.product_info("005930")
    assert isinstance(info, ProductInfo)
    assert info.symbol == "005930"
    assert info.standard_symbol == "KR7005930003"
    assert info.sale_start_date == date(1975, 6, 11)
    assert info.sale_end_date is None
    assert info.first_registered_date == date(1975, 6, 11)
    assert client.transport.calls[0] == {
        "path": "/uapi/domestic-stock/v1/quotations/search-info",
        "tr_id": "CTPF1604R",
        "params": {"PDNO": "005930", "PRDT_TYPE_CD": "300"},
    }


@pytest.mark.parametrize("symbol,product_type", [("", "300"), ("005930", "")])
def test_product_info_rejects_blank_identifiers(symbol, product_type):
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": _row()})
    with pytest.raises(KISUsageError):
        _client(response).domestic.product_info(symbol, product_type=product_type)


def test_product_info_missing_output_fails_closed():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={})
    with pytest.raises(KISError):
        _client(response).domestic.product_info("005930")
