"""해외증권 기간별 권리와 기업행사 종합."""

from datetime import date
from decimal import Decimal

from kis_openapi import KISClient, OverseasCorporateAction, OverseasRight
from kis_openapi.transport import RawResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def request(
        self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""
    ):
        self.calls.append({"path": path, "tr_id": tr_id, "params": params, "tr_cont": tr_cont})
        return next(self.responses)


def _client(*responses):
    return KISClient(app_key="k", app_secret="s", transport=FakeTransport(responses))


def _right_response(*, tr_cont="", nk=""):
    row = {"bass_dt": "20240510", "rght_type_cd": "03", "pdno": "AAPL",
           "prdt_name": "Apple", "prdt_type_cd": "512", "std_pdno": "US0378331005",
           "acpl_bass_dt": "20240510", "sbsc_strt_dt": "", "sbsc_end_dt": "",
           "cash_alct_rt": "1", "stck_alct_rt": "", "crcy_cd": "USD", "crcy_cd2": "",
           "crcy_cd3": "", "crcy_cd4": "", "alct_frcr_unpr": "",
           "stkp_dvdn_frcr_amt2": "0.25", "stkp_dvdn_frcr_amt3": "",
           "stkp_dvdn_frcr_amt4": "", "dfnt_yn": "Y"}
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", tr_cont=tr_cont,
                       body={"output": [row], "ctx_area_nk50": nk, "ctx_area_fk50": "fk"})


def test_overseas_period_rights_maps_and_paginates():
    client = _client(_right_response(tr_cont="M", nk="next"), _right_response())
    rights = client.overseas_rights(start="20240501", end="20240531")
    assert isinstance(rights[0], OverseasRight)
    assert rights[0].base_date == date(2024, 5, 10)
    assert rights[0].dividends_per_share[0] == Decimal("0.25")
    assert rights[0].is_final
    assert client.transport.calls[0]["tr_id"] == "CTRGT011R"
    assert client.transport.calls[0]["params"]["RGHT_TYPE_CD"] == "%%"
    assert client.transport.calls[1]["params"]["CTX_AREA_NK50"] == "next"
    assert [call["tr_cont"] for call in client.transport.calls] == ["", "N"]


def test_overseas_corporate_actions_maps_and_routes():
    row = {"anno_dt": "20240501", "ca_title": "Cash Dividend", "div_lock_dt": "20240510",
           "pay_dt": "20240520", "record_dt": "20240511", "validity_dt": "",
           "local_end_dt": "", "lock_dt": "", "delist_dt": "", "redempt_dt": "",
           "early_redempt_dt": "", "effective_dt": ""}
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": [row]})
    client = _client(response)
    actions = client.overseas_corporate_actions("US", "AAPL")
    assert isinstance(actions[0], OverseasCorporateAction)
    assert actions[0].payment_date == date(2024, 5, 20)
    assert client.transport.calls[0] == {
        "path": "/uapi/overseas-price/v1/quotations/rights-by-ice",
        "tr_id": "HHDFS78330900",
        "params": {"NCOD": "US", "SYMB": "AAPL", "ST_YMD": "", "ED_YMD": ""},
        "tr_cont": "",
    }
