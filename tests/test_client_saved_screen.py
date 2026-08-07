"""HTS 서버 저장 조건검색 조회."""

from decimal import Decimal

import pytest

from kis_openapi import KISClient, SavedScreen, SavedScreenStock
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return next(self.responses)


def _client(*responses):
    return KISClient(app_key="k", app_secret="s", transport=FakeTransport(responses))


def _response(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output2": rows})


def _stock_row():
    return {"code": "005930", "name": "삼성전자", "daebi": "5", "price": "71000",
            "chgrate": "0.70", "acml_vol": "100", "trade_amt": "7100", "change": "500",
            "cttr": "110.5", "open": "71500", "high": "72000", "low": "70500",
            "high52": "80000", "low52": "60000", "expprice": "70900", "expchange": "600",
            "expchggrate": "0.84", "expcvol": "20", "chgrate2": "120.0", "expdaebi": "5",
            "recprice": "71500", "uplmtprice": "92900", "dnlmtprice": "50100",
            "stotprice": "420000000"}


def test_saved_screens_and_results_map_and_route():
    client = _client(
        _response([{"user_id": "user", "seq": "0", "grp_nm": "가치", "condition_nm": "저PER"}]),
        _response([_stock_row()]),
    )
    screens = client.saved_screens("user")
    stocks = client.saved_screen_stocks("user", screens[0].sequence)
    assert isinstance(screens[0], SavedScreen)
    assert screens[0].condition_name == "저PER"
    assert isinstance(stocks[0], SavedScreenStock)
    assert stocks[0].change == Decimal(-500)
    assert stocks[0].expected_change_percent == Decimal("-0.84")
    assert client.transport.calls == [
        {"path": "/uapi/domestic-stock/v1/quotations/psearch-title",
         "tr_id": "HHKST03900300", "params": {"user_id": "user"}},
        {"path": "/uapi/domestic-stock/v1/quotations/psearch-result",
         "tr_id": "HHKST03900400", "params": {"user_id": "user", "seq": "0"}},
    ]


@pytest.mark.parametrize("method,args", [("saved_screens", ("",)),
                                          ("saved_screen_stocks", ("user", ""))])
def test_saved_screen_queries_reject_blank_identifiers(method, args):
    with pytest.raises(KISUsageError):
        getattr(_client(), method)(*args)


def test_saved_screen_queries_fail_closed_on_missing_output():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={})
    with pytest.raises(KISError):
        _client(response).saved_screens("user")
