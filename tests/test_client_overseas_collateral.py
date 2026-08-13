"""해외주식 담보대출 가능종목 조회."""

from datetime import date
from decimal import Decimal

import pytest

from kis_trader import (
    KISClient,
    OverseasCollateralStock,
    OverseasCollateralStockSearch,
    OverseasCollateralSummary,
)
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def request(
        self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""
    ):
        self.calls.append({"path": path, "tr_id": tr_id, "params": params, "tr_cont": tr_cont})
        return next(self.responses)


def _response(*, tr_cont="", nk=""):
    row = {"pdno": "AMD", "ovrs_item_name": "Advanced Micro Devices", "loan_rt": "50",
           "mgge_mntn_rt": "140", "mgge_ensu_rt": "160", "loan_exec_psbl_yn": "Y",
           "stff_name": "", "erlm_dt": "20240510", "tr_mket_name": "NASDAQ",
           "crcy_cd": "USD", "natn_kor_name": "미국", "ovrs_excg_cd": "NAS"}
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", tr_cont=tr_cont,
                       body={"output1": [row], "output2": {"loan_psbl_item_num": "1"},
                             "ctx_area_fk100": "fk", "ctx_area_nk100": nk})


def test_overseas_collateral_stocks_maps_summary_and_paginates():
    fake = FakeTransport([_response(tr_cont="M", nk="next"), _response()])
    client = KISClient(app_key="k", app_secret="s", transport=fake)
    result = client.overseas.collateral_stocks("AMD", "840", loanable=True)
    assert isinstance(result, OverseasCollateralStockSearch)
    assert isinstance(result.stocks, tuple)
    # PARITY: two pages of one row each -> two OverseasCollateralStock, values preserved.
    assert isinstance(result.stocks[0], OverseasCollateralStock)
    assert [s.symbol for s in result.stocks] == ["AMD", "AMD"]
    assert result.stocks[0].loan_rate == Decimal(50)
    assert result.stocks[0].registered_date == date(2024, 5, 10)
    assert result.stocks[0].is_loanable
    # DELTA: the previously-discarded output2 summary is now reachable and typed.
    assert isinstance(result.summary, OverseasCollateralSummary)
    assert result.summary.loanable_count == 1
    assert fake.calls[0]["tr_id"] == "CTLN4050R"
    assert fake.calls[0]["params"]["LOAN_PSBL_YN"] == "Y"
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "next"
    assert [call["tr_cont"] for call in fake.calls] == ["", "N"]


def test_overseas_collateral_stocks_missing_output2_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", tr_cont="",
                       body={"output1": [], "ctx_area_fk100": "", "ctx_area_nk100": ""})
    fake = FakeTransport([resp])
    client = KISClient(app_key="k", app_secret="s", transport=fake)
    with pytest.raises(KISError):
        client.overseas.collateral_stocks("AMD", "840")
