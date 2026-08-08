"""기간별 매매손익 -- kis.trade_profits(start=, end=) (TTTC8715R).

종목별 실현손익(output1)과 기간 총계(output2)를 네트워크 없이 검증한다. 픽스처는 원장
응답예시(inquire-period-trade-profit)의 실값을 쓴다.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_openapi import KISClient, TradeProfit, TradeProfitHistory
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_PATH = "/uapi/domestic-stock/v1/trading/inquire-period-trade-profit"

# 원장 응답예시(inquire-period-trade-profit) 실값.
_ROW = {
    "trad_dt": "20240216", "pdno": "000J2552221D", "prdt_name": "SG 17WR",
    "trad_dvsn_name": "현금", "loan_dt": "", "hldg_qty": "2", "pchs_unpr": "135",
    "buy_qty": "2", "buy_amt": "271", "sll_pric": "0", "sll_qty": "0", "sll_amt": "0",
    "rlzt_pfls": "0", "pfls_rt": "0.00000000", "fee": "0", "tl_tax": "0", "loan_int": "0",
}
_SUMMARY = {
    "sll_qty_smtl": "8", "sll_tr_amt_smtl": "96455", "sll_fee_smtl": "0", "sll_tltx_smtl": "0",
    "sll_excc_amt_smtl": "96455", "buyqty_smtl": "2003", "buy_tr_amt_smtl": "116697331",
    "buy_fee_smtl": "0", "buy_tax_smtl": "0", "buy_excc_amt_smtl": "116697331",
    "tot_qty": "2011", "tot_tr_amt": "116793786", "tot_fee": "0", "tot_tltx": "0",
    "tot_excc_amt": "116793786", "tot_rlzt_pfls": "22991", "loan_int": "0",
    "tot_pftrt": "31.29560057",
}


def _resp(rows=None, summary=None, *, nk="", fk="", tr_cont=""):
    body = {"output1": rows if rows is not None else [_ROW],
            "output2": summary if summary is not None else dict(_SUMMARY),
            "ctx_area_nk100": nk, "ctx_area_fk100": fk}
    return RawResponse(rt_cd="0", msg_cd="KIOK0500", msg1="조회", body=body, tr_cont=tr_cont)


class FakeTransport:
    def __init__(self, *, response=None, pages=None):
        self.response = response
        self.pages = pages
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent, "tr_cont": tr_cont})
        outcome = self.pages.pop(0) if self.pages else self.response
        assert outcome is not None
        return outcome


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def test_trade_profits_parses_rows_and_totals():
    history = _client(FakeTransport(response=_resp())).trade_profits(start="20240201", end="20240229")
    assert isinstance(history, TradeProfitHistory)
    assert history.total_realized_pnl == Decimal(22991)
    assert history.total_return_percent == Decimal("31.29560057")
    assert history.total_buy_amount == Decimal(116697331)
    assert history.total_sell_amount == Decimal(96455)
    assert len(history.trades) == 1
    trade = history.trades[0]
    assert isinstance(trade, TradeProfit)
    assert trade.trade_date == date(2024, 2, 16)
    assert trade.symbol == "000J2552221D"
    assert trade.name == "SG 17WR"
    assert trade.buy_quantity == Decimal(2)
    assert trade.buy_amount == Decimal(271)
    assert trade.realized_pnl == Decimal(0)


def test_trade_profits_tr_method_and_params():
    fake = FakeTransport(response=_resp())
    _client(fake).trade_profits(start="20240201", end="20240229", symbol="005930", sort="oldest")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC8715R"
    assert call["method"] == "GET"
    assert call["path"] == _PATH
    assert call["idempotent"] is True
    assert call["params"]["INQR_STRT_DT"] == "20240201"
    assert call["params"]["INQR_END_DT"] == "20240229"
    assert call["params"]["PDNO"] == "005930"
    assert call["params"]["SORT_DVSN"] == "01"     # oldest


def test_trade_profits_default_sort_recent():
    fake = FakeTransport(response=_resp())
    _client(fake).trade_profits(start="1", end="2")
    assert fake.calls[0]["params"]["SORT_DVSN"] == "00"


def test_trade_profits_unknown_sort_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake).trade_profits(start="1", end="2", sort="weird")
    assert fake.calls == []


def test_trade_profits_demo_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").trade_profits(start="1", end="2")
    assert fake.calls == []


def test_trade_profits_paginates_and_merges():
    page1 = _resp([_ROW], nk="NEXT", fk="FK", tr_cont="M")
    page2 = _resp([dict(_ROW, pdno="005930")], tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    history = _client(fake).trade_profits(start="1", end="2")
    assert [t.symbol for t in history.trades] == ["000J2552221D", "005930"]
    assert fake.calls[1]["tr_cont"] == "N"
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "NEXT"
    # 총계는 첫 페이지에서 확정
    assert history.total_realized_pnl == Decimal(22991)


def test_trade_profits_empty_rows_ok_with_summary():
    history = _client(FakeTransport(response=_resp([]))).trade_profits(start="1", end="2")
    assert history.trades == ()
    assert history.total_realized_pnl == Decimal(22991)


def test_trade_profits_missing_summary_fails_closed():
    body = {"output1": [_ROW], "output2": []}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).trade_profits(start="1", end="2")


def test_trade_profits_non_list_output1_fails_closed():
    body = {"output1": {"pdno": "x"}, "output2": dict(_SUMMARY)}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).trade_profits(start="1", end="2")


def test_trade_profits_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="E", msg1="실패", body={"output1": []}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).trade_profits(start="1", end="2")


def test_trade_profits_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).trade_profits(start="1", end="2")
