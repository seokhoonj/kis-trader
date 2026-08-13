"""해외주식 일별거래내역 -- kis.overseas.account.transactions(start=, end=) (CTOS4001R).

체결 거래 행(output1)을 통화 태그된 Money 로, 연속조회·필터·fail-closed 를 네트워크 없이
검증한다. 픽스처는 원장 응답예시(inquire-period-trans) 실값을 쓴다.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_openapi import KISClient, Money, OverseasTransaction
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_PATH = "/uapi/overseas-stock/v1/trading/inquire-period-trans"

# 원장 응답예시(inquire-period-trans) output1 실값(매도 1건).
_ROW = {
    "trad_dt": "20240116", "sttl_dt": "20240118", "sll_buy_dvsn_cd": "01",
    "sll_buy_dvsn_name": "매도", "pdno": "AAPL", "ovrs_item_name": "애플",
    "ccld_qty": "1", "amt_unit_ccld_qty": "1.00000000", "ft_ccld_unpr2": "2.94000000",
    "ovrs_stck_ccld_unpr": "0.00000000", "tr_frcr_amt2": "2.940000", "tr_amt": "0",
    "frcr_excc_amt_1": "2.940000", "wcrc_excc_amt": "0", "dmst_frcr_fee1": "0.00000",
    "frcr_fee1": "0.010000", "dmst_wcrc_fee": "3", "ovrs_wcrc_fee": "5", "crcy_cd": "USD",
    "std_pdno": "US0378331005", "erlm_exrt": "0.00000000", "loan_dvsn_cd": "01",
    "loan_dvsn_name": "현금",
}


def _resp(rows=None, *, nk="", fk="", tr_cont=""):
    body = {"output1": rows if rows is not None else [_ROW],
            "output2": {"frcr_buy_amt_smtl": "0", "frcr_sll_amt_smtl": "2.94"},
            "ctx_area_nk100": nk, "ctx_area_fk100": fk}
    return RawResponse(rt_cd="0", msg_cd="KIOK0460", msg1="조회", body=body, tr_cont=tr_cont)


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


def test_transactions_parse_ledger_row():
    txs = _client(FakeTransport(response=_resp())).overseas.account.transactions(
        start="20240101", end="20240528"
    )
    assert len(txs) == 1
    tx = txs[0]
    assert isinstance(tx, OverseasTransaction)
    assert tx.trade_date == date(2024, 1, 16)
    assert tx.settlement_date == date(2024, 1, 18)
    assert tx.side == "sell"
    assert tx.symbol == "AAPL"
    assert tx.name == "애플"
    assert tx.quantity == Decimal(1)
    assert tx.price == Money(Decimal("2.94000000"), "USD")
    assert tx.trade_amount == Money(Decimal("2.940000"), "USD")
    assert tx.foreign_fee == Money(Decimal("0.010000"), "USD")
    assert tx.domestic_won_fee == Decimal(3)
    assert tx.overseas_won_fee == Decimal(5)
    assert tx.currency == "USD"
    assert tx.loan_type == "현금"


def test_transactions_tr_method_and_params():
    fake = FakeTransport(response=_resp())
    _client(fake).overseas.account.transactions(start="20240101", end="20240528", symbol="AAPL", side="buy")
    call = fake.calls[0]
    assert call["tr_id"] == "CTOS4001R"
    assert call["method"] == "GET"
    assert call["path"] == _PATH
    assert call["idempotent"] is True
    assert call["params"]["ERLM_STRT_DT"] == "20240101"
    assert call["params"]["ERLM_END_DT"] == "20240528"
    assert call["params"]["PDNO"] == "AAPL"
    assert call["params"]["SLL_BUY_DVSN_CD"] == "02"    # buy


def test_transactions_side_filter_codes():
    fake = FakeTransport(response=_resp())
    _client(fake).overseas.account.transactions(start="1", end="2")   # default all
    assert fake.calls[0]["params"]["SLL_BUY_DVSN_CD"] == "00"


def test_transactions_unknown_side_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake).overseas.account.transactions(start="1", end="2", side="hold")
    assert fake.calls == []


def test_transactions_demo_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").overseas.account.transactions(start="1", end="2")
    assert fake.calls == []


def test_transactions_paginates_and_merges():
    page1 = _resp([_ROW], nk="NEXT", fk="FK", tr_cont="M")
    page2 = _resp([dict(_ROW, pdno="MSFT")], tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    txs = _client(fake).overseas.account.transactions(start="1", end="2")
    assert [t.symbol for t in txs] == ["AAPL", "MSFT"]
    assert fake.calls[1]["tr_cont"] == "N"
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "NEXT"


def test_transactions_empty_is_ok():
    txs = _client(FakeTransport(response=_resp([]))).overseas.account.transactions(start="1", end="2")
    assert txs == []


def test_transactions_skips_padding_row():
    txs = _client(FakeTransport(response=_resp([dict(_ROW, pdno=""), _ROW]))).overseas.account.transactions(
        start="1", end="2"
    )
    assert len(txs) == 1


def test_transactions_non_list_output_fails_closed():
    body = {"output1": {"pdno": "x"}, "ctx_area_nk100": "", "ctx_area_fk100": ""}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).overseas.account.transactions(start="1", end="2")


def test_transactions_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="E", msg1="실패", body={"output1": []}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).overseas.account.transactions(start="1", end="2")


def test_transactions_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).overseas.account.transactions(start="1", end="2")
