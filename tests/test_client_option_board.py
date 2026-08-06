"""KOSPI200 옵션 콜/풋 전광판 조회."""

from decimal import Decimal

import pytest

from kis_openapi import KISClient, OptionBoard
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _row(**overrides):
    row = {
        "acpr": "480.00",
        "optn_shrn_iscd": "201V05480",
        "optn_prpr": "0.01",
        "optn_prdy_vrss": "0.00",
        "prdy_vrss_sign": "3",
        "optn_prdy_ctrt": "0.00",
        "optn_bidp": "0.00",
        "optn_askp": "0.01",
        "acml_vol": "34",
        "hts_otst_stpl_qty": "642",
        "delta_val": "0.0000",
        "gama": "0.0000",
        "vega": "0.0000",
        "theta": "-0.0000",
        "rho": "0.0000",
        "hts_ints_vltl": "31.5614",
        "hts_thpr": "0.00",
        "tmvl_val": "0.01",
        "invl_val": "0.00",
        "atm_cls_name": "OTM",
    }
    row.update(overrides)
    return row


def _response(*, include_puts=True):
    body = {"output1": [_row()]}
    if include_puts:
        body["output2"] = [_row(optn_shrn_iscd="301V05480", atm_cls_name="ITM")]
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


def _client(fake):
    return KISClient(app_key="k", app_secret="s", transport=fake)


def test_option_board_maps_rows_and_default_params():
    fake = FakeTransport(_response())
    board = _client(fake).option_board("202405")
    assert isinstance(board, OptionBoard)
    assert board.calls[0].strike == Decimal("480.00")
    assert board.calls[0].price == Decimal("0.01")
    assert board.calls[0].code == "201V05480"
    assert board.calls[0].gamma == Decimal("0.0000")
    assert board.calls[0].bid == Decimal("0.00")
    assert len(board.puts) >= 1
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-futureoption/v1/quotations/display-board-callput"
    assert call["tr_id"] == "FHPIF05030100"
    assert call["params"]["FID_MTRT_CNT"] == "202405"
    assert call["params"]["FID_COND_MRKT_CLS_CODE"] == ""


def test_option_board_maps_mini_kospi200_underlying():
    fake = FakeTransport(_response())
    _client(fake).option_board("202405", underlying="MINI_KOSPI200")
    assert fake.calls[0]["params"]["FID_COND_MRKT_CLS_CODE"] == "MKI"


def test_option_board_rejects_unknown_underlying():
    fake = FakeTransport(_response())
    with pytest.raises(KISUsageError, match="KOSPI200.*MINI_KOSPI200.*KOSDAQ150"):
        _client(fake).option_board("202405", underlying="UNKNOWN")
    assert fake.calls == []


def test_option_board_missing_output2_fails_closed():
    fake = FakeTransport(_response(include_puts=False))
    with pytest.raises(KISError):
        _client(fake).option_board("202405")
