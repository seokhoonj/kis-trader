"""기간별 일별 매매손익 합산 -- kis.domestic.account.daily_profits(start=, end=) (TTTC8708R).

하루 단위 실현손익(output1)과 기간 총계(output2)를 검증한다. 픽스처는 원장 응답예시
(inquire-period-profit) 실값을 쓴다. 이 응답엔 총수익률(tot_pftrt)이 없다.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_openapi import DailyProfit, DailyProfitHistory, KISClient
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_PATH = "/uapi/domestic-stock/v1/trading/inquire-period-profit"

_ROW = {
    "trad_dt": "20240220", "buy_amt": "116697331", "sll_amt": "96455", "rlzt_pfls": "22991",
    "fee": "0", "loan_int": "0", "tl_tax": "0", "pfls_rt": "31.29560057",
    "sll_qty1": "8", "buy_qty1": "2003",
}
_SUMMARY = {
    "sll_qty_smtl": "8", "sll_tr_amt_smtl": "96455", "sll_fee_smtl": "0", "sll_tltx_smtl": "0",
    "sll_excc_amt_smtl": "96455", "buy_qty_smtl": "2003", "buy_tr_amt_smtl": "116697331",
    "buy_fee_smtl": "0", "buy_tax_smtl": "0", "buy_excc_amt_smtl": "116697331",
    "tot_qty": "2011", "tot_tr_amt": "116793786", "tot_fee": "0", "tot_tltx": "0",
    "tot_excc_amt": "116793786", "tot_rlzt_pfls": "22991", "loan_int": "0",
}


def _resp(rows=None, summary=None, *, nk="", fk="", tr_cont=""):
    body = {"output1": rows if rows is not None else [_ROW],
            "output2": summary if summary is not None else dict(_SUMMARY),
            "ctx_area_nk100": nk, "ctx_area_fk100": fk}
    return RawResponse(rt_cd="0", msg_cd="KIOK0510", msg1="조회", body=body, tr_cont=tr_cont)


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


def test_daily_profits_parses_rows_and_totals():
    history = _client(FakeTransport(response=_resp())).domestic.account.daily_profits(start="20240201", end="20240229")
    assert isinstance(history, DailyProfitHistory)
    assert history.total_realized_pnl == Decimal(22991)
    assert history.total_buy_amount == Decimal(116697331)
    assert history.total_sell_amount == Decimal(96455)
    assert len(history.days) == 1
    day = history.days[0]
    assert isinstance(day, DailyProfit)
    assert day.trade_date == date(2024, 2, 20)
    assert day.buy_amount == Decimal(116697331)
    assert day.realized_pnl == Decimal(22991)
    assert day.return_percent == Decimal("31.29560057")
    assert day.buy_quantity == Decimal(2003)
    assert day.sell_quantity == Decimal(8)


def test_daily_profits_tr_and_params():
    fake = FakeTransport(response=_resp())
    _client(fake).domestic.account.daily_profits(start="20240201", end="20240229", sort="oldest")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC8708R"
    assert call["path"] == _PATH
    assert call["method"] == "GET"
    assert call["idempotent"] is True
    assert call["params"]["INQR_STRT_DT"] == "20240201"
    assert call["params"]["INQR_DVSN"] == "00"
    assert call["params"]["SORT_DVSN"] == "01"


def test_daily_profits_demo_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").domestic.account.daily_profits(start="1", end="2")
    assert fake.calls == []


def test_daily_profits_unknown_sort_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake).domestic.account.daily_profits(start="1", end="2", sort="x")
    assert fake.calls == []


def test_daily_profits_paginates_and_merges():
    page1 = _resp([_ROW], nk="NEXT", tr_cont="M")
    page2 = _resp([dict(_ROW, trad_dt="20240221")], tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    history = _client(fake).domestic.account.daily_profits(start="1", end="2")
    assert [d.trade_date for d in history.days] == [date(2024, 2, 20), date(2024, 2, 21)]
    assert fake.calls[1]["tr_cont"] == "N"


def test_daily_profits_missing_summary_fails_closed():
    body = {"output1": [_ROW], "output2": []}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).domestic.account.daily_profits(start="1", end="2")


def test_daily_profits_non_list_output1_fails_closed():
    body = {"output1": {"trad_dt": "x"}, "output2": dict(_SUMMARY)}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).domestic.account.daily_profits(start="1", end="2")


def test_daily_profits_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).domestic.account.daily_profits(start="1", end="2")
