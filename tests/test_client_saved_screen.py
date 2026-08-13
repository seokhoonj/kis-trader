"""HTS 서버 저장 조건검색 조회."""

from datetime import date, time
from decimal import Decimal

import pytest

from kis_openapi import (
    KISClient,
    SavedScreen,
    SavedScreenStock,
    Watchlist,
    WatchlistGroup,
)
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
    screens = client.domestic.saved_screens("user")
    stocks = client.domestic.saved_screen_stocks("user", screens[0].sequence)
    assert isinstance(screens[0], SavedScreen)
    assert screens[0].condition_name == "저PER"
    assert isinstance(stocks[0], SavedScreenStock)
    assert stocks[0].current_price == Decimal(71000)
    assert stocks[0].price_change == Decimal(-500)
    assert stocks[0].cumulative_volume == 100
    assert stocks[0].open_price == Decimal(71500)
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
        getattr(_client().domestic, method)(*args)


def test_saved_screen_queries_fail_closed_on_missing_output():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={})
    with pytest.raises(KISError):
        _client(response).domestic.saved_screens("user")


def test_watchlist_groups_and_stocks_map_and_route():
    groups_response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output2": {
        "date": "20240510", "trnm_hour": "091500", "data_rank": "1",
        "inter_grp_code": "001", "inter_grp_name": "반도체", "ask_cnt": "1",
    }})
    stocks_response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={
        "output1": {"data_rank": "1", "inter_grp_name": "반도체"},
        "output2": [{"fid_mrkt_cls_code": "J", "data_rank": "1", "exch_code": "KRX",
                     "jong_code": "005930", "color_code": "1", "memo": "핵심",
                     "hts_kor_isnm": "삼성전자", "fxdt_ntby_qty": "100",
                     "cntg_unpr": "71000", "cntg_cls_code": "2"}],
    })
    client = _client(groups_response, stocks_response)
    groups = client.domestic.watchlist_groups("user")
    watchlist = client.domestic.watchlist("user", groups[0].code)
    assert isinstance(groups[0], WatchlistGroup)
    assert groups[0].date == date(2024, 5, 10)
    assert groups[0].transmitted_at == time(9, 15, 0)
    assert groups[0].rank == 1
    assert groups[0].requested_count == 1
    assert isinstance(watchlist, Watchlist)
    assert watchlist.rank == 1
    assert watchlist.stocks[0].rank == 1
    assert watchlist.name == "반도체"
    assert watchlist.stocks[0].symbol == "005930"
    assert watchlist.stocks[0].execution_price == Decimal(71000)
    assert client.transport.calls[0]["tr_id"] == "HHKCM113004C7"
    assert client.transport.calls[0]["params"] == {
        "TYPE": "1", "FID_ETC_CLS_CODE": "00", "USER_ID": "user"
    }
    assert client.transport.calls[1]["tr_id"] == "HHKCM113004C6"
    assert client.transport.calls[1]["params"]["FID_ETC_CLS_CODE"] == "4"


@pytest.mark.parametrize("method,args", [("watchlist_groups", ("",)),
                                          ("watchlist", ("user", ""))])
def test_watchlist_queries_reject_blank_identifiers(method, args):
    with pytest.raises(KISUsageError):
        getattr(_client().domestic, method)(*args)


@pytest.mark.parametrize("body", [{}, {"output1": {}, "output2": "bad"}])
def test_watchlist_queries_fail_closed(body):
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body=body)
    with pytest.raises(KISError):
        _client(response).domestic.watchlist("user", "001")
