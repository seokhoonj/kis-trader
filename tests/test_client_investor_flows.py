"""투자자 수급 -- kis.domestic.stock(...).investor_flows().

행위중심 표면(`quotations.inquire_investor` 아님), 개인/외국인/기관 중첩 엔티티 매핑, 순매도(음수)
처리, fail-closed 파싱을 가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_trader import DetailedInvestorHistory, InvestorFlow, KISClient
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse

_INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor"


def _row(**over):
    base = {"stck_bsop_date": "20240102", "stck_clpr": "71500"}
    for who, vals in (("prsn", (100, 40, 60, "7000000", "2800000", "4200000")),
                      ("frgn", (200, 500, -300, "14000000", "35000000", "-21000000")),
                      ("orgn", (50, 20, 30, "3500000", "1400000", "2100000"))):
        bv, sv, nq, bval, sval, nval = vals
        base |= {f"{who}_shnu_vol": str(bv), f"{who}_seln_vol": str(sv), f"{who}_ntby_qty": str(nq),
                 f"{who}_shnu_tr_pbmn": bval, f"{who}_seln_tr_pbmn": sval, f"{who}_ntby_tr_pbmn": nval}
    base |= over
    return base


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def test_investor_flows_maps_nested_activity():
    fake = FakeTransport(response=_resp([_row()]))
    flows = _client(fake).domestic.stock("005930").investor_flows()
    assert len(flows) == 1
    flow = flows[0]
    assert isinstance(flow, InvestorFlow)
    assert flow.symbol == "005930"
    assert flow.trading_date == date(2024, 1, 2)
    assert flow.close == Decimal(71500)
    # 개인 6필드 전수
    assert flow.individual.buy_volume == 100
    assert flow.individual.sell_volume == 40
    assert flow.individual.net_buy_volume == 60
    assert flow.individual.buy_amount == Decimal(7_000_000)
    assert flow.individual.sell_amount == Decimal(2_800_000)
    assert flow.individual.net_buy_amount == Decimal(4_200_000)
    # 기관 6필드 전수
    assert flow.institutional.buy_volume == 50
    assert flow.institutional.sell_volume == 20
    assert flow.institutional.net_buy_volume == 30
    assert flow.institutional.buy_amount == Decimal(3_500_000)
    assert flow.institutional.sell_amount == Decimal(1_400_000)
    assert flow.institutional.net_buy_amount == Decimal(2_100_000)
    assert fake.calls[0]["path"] == _INVESTOR_PATH
    assert fake.calls[0]["tr_id"] == "FHKST01010900"
    assert fake.calls[0]["params"] == {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}


def test_investor_flows_handles_net_selling_negative():
    flow = _client(FakeTransport(response=_resp([_row()]))).domestic.stock("005930").investor_flows()[0]
    assert flow.foreign.net_buy_volume == -300             # 외국인 순매도
    assert flow.foreign.net_buy_amount == Decimal(-21_000_000)


def test_investor_flows_skips_dateless_rows():
    fake = FakeTransport(response=_resp([_row(), {"stck_bsop_date": ""}]))
    assert len(_client(fake).domestic.stock("005930").investor_flows()) == 1


def test_investor_flows_missing_field_fails_closed():
    fake = FakeTransport(response=_resp([_row(frgn_ntby_qty="")]))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").investor_flows()


def test_investor_flows_error_response_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="1", msg_cd="X", msg1="실패", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").investor_flows()


def test_investor_flows_missing_output_block_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):                          # 성공 응답인데 output 없음 -> 빈결과로 오인 금지
        _client(fake).domestic.stock("005930").investor_flows()


_DETAILED_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
_DETAILED_PREFIXES = (
    "frgn", "prsn", "orgn", "scrt", "ivtr", "pe_fund", "bank", "insu", "mrbn",
    "fund", "etc", "etc_orgt", "etc_corp",
)


def _detailed_row(day="20240510"):
    row = {"stck_bsop_date": day, "stck_oprc": "70000", "stck_hgpr": "72000",
           "stck_lwpr": "69500", "stck_clpr": "71500", "prdy_vrss": "500",
           "prdy_vrss_sign": "2", "prdy_ctrt": "0.70", "acml_vol": "1000000",
           "acml_tr_pbmn": "71000"}
    for prefix in _DETAILED_PREFIXES:
        net_key = "ntby_vol" if prefix in {"pe_fund", "etc_orgt", "etc_corp"} else "ntby_qty"
        row |= {f"{prefix}_shnu_vol": "120", f"{prefix}_seln_vol": "100",
                f"{prefix}_{net_key}": "20", f"{prefix}_shnu_tr_pbmn": "12",
                f"{prefix}_seln_tr_pbmn": "10", f"{prefix}_ntby_tr_pbmn": "2"}
    return row


def _detailed_summary():
    return {"stck_prpr": "71500", "prdy_vrss": "500", "prdy_vrss_sign": "2",
            "prdy_ctrt": "0.70", "acml_vol": "1000000", "prdy_vol": "900000",
            "rprs_mrkt_kor_name": "코스피"}


class PagingTransport:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        self.calls.append({"path": path, "tr_id": tr_id, "params": params, "tr_cont": tr_cont})
        return next(self.responses)


def _detailed_response(rows, *, tr_cont=""):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                       body={"output1": _detailed_summary(), "output2": rows}, tr_cont=tr_cont)


def test_detailed_investor_history_maps_all_participants_and_paginates():
    fake = PagingTransport([_detailed_response([_detailed_row()], tr_cont="M"),
                            _detailed_response([_detailed_row("20240509")])])
    history = _client(fake).domestic.stock("005930").detailed_investor_history(as_of="20240510")
    assert isinstance(history, DetailedInvestorHistory)
    assert history.price == Decimal(71500)
    assert len(history.flows) == 2
    flow = history.flows[0]
    assert flow.trading_date == date(2024, 5, 10)
    assert flow.participants["foreign"].buy_volume == 120
    assert flow.participants["private_equity"].net_buy_volume == 20
    assert flow.participants["other_corporation"].net_buy_amount == Decimal(2)
    assert fake.calls[0]["path"] == _DETAILED_PATH
    assert fake.calls[0]["tr_id"] == "FHPTJ04160001"
    assert fake.calls[0]["params"]["FID_ETC_CLS_CODE"] == "1"
    assert [call["tr_cont"] for call in fake.calls] == ["", "N"]


def test_detailed_investor_history_applies_down_sign():
    row, summary = _detailed_row(), _detailed_summary()
    row["prdy_vrss_sign"] = summary["prdy_vrss_sign"] = "5"
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                           body={"output1": summary, "output2": [row]})
    history = _client(PagingTransport([response])).domestic.stock("005930").detailed_investor_history()
    assert history.change == Decimal(-500)
    assert history.flows[0].change_percent == Decimal("-0.70")


@pytest.mark.parametrize("body", [{}, {"output1": _detailed_summary(), "output2": {}}])
def test_detailed_investor_history_missing_blocks_fail_closed(body):
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body=body)
    with pytest.raises(KISError):
        _client(PagingTransport([response])).domestic.stock("005930").detailed_investor_history()
