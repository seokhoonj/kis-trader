"""해외주식 담보대출 가능종목 조회."""

from datetime import date
from decimal import Decimal

from kis_openapi import KISClient, OverseasCollateralStock
from kis_openapi.transport import RawResponse


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


def test_overseas_collateral_stocks_maps_and_paginates():
    fake = FakeTransport([_response(tr_cont="M", nk="next"), _response()])
    client = KISClient(app_key="k", app_secret="s", transport=fake)
    stocks = client.overseas.collateral_stocks("AMD", "840", loanable=True)
    assert isinstance(stocks[0], OverseasCollateralStock)
    assert stocks[0].loan_rate == Decimal(50)
    assert stocks[0].registered_date == date(2024, 5, 10)
    assert stocks[0].is_loanable
    assert fake.calls[0]["tr_id"] == "CTLN4050R"
    assert fake.calls[0]["params"]["LOAN_PSBL_YN"] == "Y"
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "next"
    assert [call["tr_cont"] for call in fake.calls] == ["", "N"]
