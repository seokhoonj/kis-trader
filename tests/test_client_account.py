"""계좌 조회 (새 API) -- kis.account.domestic.balance/positions/portfolio + stock.buyable/sellable.

잔고 요약·보유종목·포트폴리오·매수가능·매도가능을 네트워크 없이 FakeTransport 로 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import (
    Balance,
    BuyableAmount,
    KISClient,
    Portfolio,
    Position,
    SellableQuantity,
)
from kis_trader.domestic._engine import account as account_module
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
_BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
_SELLABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-sell"


class FakeTransport:
    def __init__(self, *, response=None, by_path=None, raises=None):
        self.response = response
        self.by_path = by_path or {}
        self.raises = raises
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent})
        if path in self.by_path:
            outcome = self.by_path[path]
            if isinstance(outcome, list):
                outcome = outcome.pop(0)
        elif self.raises is not None:
            outcome = self.raises
        else:
            outcome = self.response
        if isinstance(outcome, BaseException):
            raise outcome
        assert outcome is not None, "FakeTransport 에 응답을 줘야 한다"
        return outcome


_SUMMARY = {
    "dnca_tot_amt": "1000000", "nxdy_excc_amt": "1010000", "prvs_rcdl_excc_amt": "1020000",
    "tot_evlu_amt": "1500000", "nass_amt": "2520000", "pchs_amt_smtl_amt": "1400000",
    "evlu_amt_smtl_amt": "1500000", "evlu_pfls_smtl_amt": "100000",
}
_BUYABLE_OUTPUT = {
    "ord_psbl_cash": "1000000", "ruse_psbl_amt": "0",
    "nrcvb_buy_amt": "980000", "nrcvb_buy_qty": "13",
    "max_buy_amt": "2000000", "max_buy_qty": "27",
}


def _holding(pdno, *, name="삼성전자", hldg="10", sellable="10", avg="70000", pchs="700000",
             prpr="71500", evlu="715000", pfls="15000", pfls_rt="2.14"):
    return {"pdno": pdno, "prdt_name": name, "hldg_qty": hldg, "ord_psbl_qty": sellable,
            "pchs_avg_pric": avg, "pchs_amt": pchs, "prpr": prpr, "evlu_amt": evlu,
            "evlu_pfls_amt": pfls, "evlu_pfls_rt": pfls_rt}


def _balance_resp(*, rows=None, summary=None, ctx_nk="", ctx_fk="", tr_cont=""):
    body = {"output1": rows if rows is not None else [],
            "output2": [summary if summary is not None else dict(_SUMMARY)],
            "ctx_area_nk100": ctx_nk, "ctx_area_fk100": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def _buyable_resp(output=None):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output": output if output is not None else dict(_BUYABLE_OUTPUT)})


def _sellable_resp(output1=None):
    default = {"pdno": "005930", "prdt_name": "삼성전자", "cblc_qty": "10", "ord_psbl_qty": "8"}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": output1 if output1 is not None else default})


_ERROR = RawResponse(rt_cd="1", msg_cd="EGW00215", msg1="초당 거래건수 초과", body={})


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


# --- balance ---------------------------------------------------------------
def test_balance_parses_summary():
    balance = _client(FakeTransport(response=_balance_resp())).account.domestic.balance()
    assert isinstance(balance, Balance)
    assert balance.currency == "KRW"
    assert balance.deposit == Decimal(1000000)
    assert balance.settlement_cash_d2 == Decimal(1020000)
    assert balance.net_asset == Decimal(2520000)
    assert balance.market_value == Decimal(1500000)
    assert balance.unrealized_pnl == Decimal(100000)


def test_balance_real_and_demo_tr():
    fake = FakeTransport(response=_balance_resp())
    _client(fake, environment="real").account.domestic.balance()
    assert fake.calls[0]["tr_id"] == "TTTC8434R"
    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["params"]["CANO"] == "12345678"
    assert fake.calls[0]["params"]["ACNT_PRDT_CD"] == "01"
    fake2 = FakeTransport(response=_balance_resp())
    _client(fake2, environment="paper").account.domestic.balance()
    assert fake2.calls[0]["tr_id"] == "VTTC8434R"


def test_balance_summary_as_single_object():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": [], "output2": dict(_SUMMARY)})
    assert _client(FakeTransport(response=resp)).account.domestic.balance().deposit == Decimal(1000000)


def test_balance_missing_summary_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": []})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.domestic.balance()


def test_balance_error_response_raises():
    with pytest.raises(KISError):
        _client(FakeTransport(response=_ERROR)).account.domestic.balance()


def test_balance_unparseable_fails_closed():
    summary = dict(_SUMMARY, dnca_tot_amt="N/A")
    with pytest.raises(KISError):
        _client(FakeTransport(response=_balance_resp(summary=summary))).account.domestic.balance()


def test_balance_requires_account():
    kis = KISClient(app_key="k", app_secret="s", transport=FakeTransport(response=_balance_resp()))
    with pytest.raises(KISUsageError):
        kis.account.domestic.balance()


# --- positions / portfolio -------------------------------------------------
def test_positions_parses_and_skips_padding():
    rows = [_holding("005930"), _holding("", name=""), {"prdt_name": "빈행"}]
    positions = _client(FakeTransport(response=_balance_resp(rows=rows))).account.domestic.positions()
    assert [p.symbol for p in positions] == ["005930"]
    holding = positions[0]
    assert isinstance(holding, Position)
    assert holding.security_name == "삼성전자"
    assert holding.quantity == Decimal(10)
    assert holding.average_purchase_price == Decimal(70000)
    assert holding.market_value == Decimal(715000)


def test_positions_paginate_and_merge():
    page1 = _balance_resp(rows=[_holding("005930")], ctx_nk="NEXT", ctx_fk="FK", tr_cont="M")
    page2 = _balance_resp(rows=[_holding("000660", name="SK하이닉스")])
    fake = FakeTransport(by_path={_BALANCE_PATH: [page1, page2]})
    positions = _client(fake).account.domestic.positions()
    assert [p.symbol for p in positions] == ["005930", "000660"]
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "NEXT"


def test_positions_non_list_output1_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {"pdno": "005930"}, "output2": [dict(_SUMMARY)]})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.domestic.positions()


def test_positions_pagination_cap_fails_closed(monkeypatch):
    monkeypatch.setattr(account_module, "_MAX_BALANCE_PAGES", 3)
    # 매 페이지 연속키가 진전하며 끝나지 않는 상황 -- 상한에서 fail-closed(같은 키 반복은 이제 종료).
    pages = [_balance_resp(rows=[_holding("005930")], ctx_nk=f"N{i}", ctx_fk="FK", tr_cont="M")
             for i in range(4)]
    with pytest.raises(KISError):
        _client(FakeTransport(by_path={_BALANCE_PATH: pages})).account.domestic.positions()


def test_portfolio_returns_balance_and_positions_in_one_walk():
    page1 = _balance_resp(rows=[_holding("005930")], ctx_nk="NEXT", ctx_fk="FK", tr_cont="M")
    page2 = _balance_resp(rows=[_holding("000660", name="SK하이닉스")])
    fake = FakeTransport(by_path={_BALANCE_PATH: [page1, page2]})
    portfolio = _client(fake).account.domestic.portfolio()
    assert isinstance(portfolio, Portfolio)
    assert portfolio.balance.deposit == Decimal(1000000)
    assert [p.symbol for p in portfolio.positions] == ["005930", "000660"]
    assert len(fake.calls) == 2


# --- buyable (ticker) ------------------------------------------------------
def test_ticker_buyable_parses_and_limit_division():
    fake = FakeTransport(response=_buyable_resp())
    result = _client(fake).domestic.stock("005930").buyable(limit_price=75000)
    assert isinstance(result, BuyableAmount)
    assert result.orderable_cash == Decimal(1000000)
    assert result.cash_buyable_quantity == Decimal(13)
    assert result.max_buyable_quantity == Decimal(27)
    call = fake.calls[0]
    assert call["path"] == _BUYABLE_PATH
    assert call["tr_id"] == "TTTC8908R"
    assert call["params"]["PDNO"] == "005930"
    assert call["params"]["ORD_DVSN"] == "00"
    assert call["params"]["ORD_UNPR"] == "75000"


def test_ticker_buyable_market_division_without_price():
    fake = FakeTransport(response=_buyable_resp())
    _client(fake).domestic.stock("005930").buyable()
    assert fake.calls[0]["params"]["ORD_DVSN"] == "01"
    assert fake.calls[0]["params"]["ORD_UNPR"] == ""


def test_ticker_buyable_demo_tr():
    fake = FakeTransport(response=_buyable_resp())
    _client(fake, environment="paper").domestic.stock("005930").buyable()
    assert fake.calls[0]["tr_id"] == "VTTC8908R"


@pytest.mark.parametrize("limit_price", ["nope", -1, Decimal(0), float("nan"), float("inf")])
def test_ticker_buyable_bad_price_rejected_before_io(limit_price):
    fake = FakeTransport(response=_buyable_resp())
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("005930").buyable(limit_price=limit_price)
    assert fake.calls == []


def test_ticker_buyable_amount_field_unparseable_fails_closed():
    output = dict(_BUYABLE_OUTPUT, ord_psbl_cash="N/A")
    with pytest.raises(KISError):
        _client(FakeTransport(response=_buyable_resp(output))).domestic.stock("005930").buyable()


# --- sellable (ticker, 모의 미지원) ----------------------------------------
def test_ticker_sellable_parses():
    result = _client(FakeTransport(response=_sellable_resp())).domestic.stock("005930").sellable()
    assert isinstance(result, SellableQuantity)
    assert result.quantity == Decimal(10)
    assert result.sellable_quantity == Decimal(8)


def test_ticker_sellable_not_held_reads_zero():
    output1 = {"pdno": "005930", "prdt_name": "", "cblc_qty": "", "ord_psbl_qty": ""}
    result = _client(FakeTransport(response=_sellable_resp(output1))).domestic.stock("005930").sellable()
    assert result.quantity == Decimal(0)
    assert result.sellable_quantity == Decimal(0)


def test_ticker_sellable_demo_rejected_before_io():
    fake = FakeTransport(response=_sellable_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").domestic.stock("005930").sellable()
    assert fake.calls == []


def test_ticker_buyable_requires_account():
    kis = KISClient(app_key="k", app_secret="s", transport=FakeTransport(response=_buyable_resp()))
    with pytest.raises(KISUsageError):
        kis.domestic.stock("005930").buyable()


# --- 추가 엣지 ------------------------------------------------------------
def test_positions_use_environment_tr_and_params():
    fake = FakeTransport(response=_balance_resp(rows=[]))
    _client(fake, environment="paper").account.domestic.positions()
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["tr_id"] == "VTTC8434R"
    assert call["params"]["INQR_DVSN"] == "02"
    assert call["params"]["CANO"] == "12345678"


def test_balance_reads_single_page():
    page1 = _balance_resp(
        summary=dict(_SUMMARY, dnca_tot_amt="111"), ctx_nk="NEXT", ctx_fk="FK", tr_cont="M"
    )
    page2 = _balance_resp(summary=dict(_SUMMARY, dnca_tot_amt="222"))
    fake = FakeTransport(by_path={_BALANCE_PATH: [page1, page2]})
    balance = _client(fake).account.domestic.balance()
    assert balance.deposit == Decimal(111)     # 첫 페이지 요약만
    assert len(fake.calls) == 1                 # 페이지네이션 안 함


def test_balance_empty_summary_list_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": [], "output2": []})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.domestic.balance()


def test_balance_field_mapping_d1_d2_not_transposed():
    balance = _client(FakeTransport(response=_balance_resp())).account.domestic.balance()
    assert balance.settlement_cash_d1 == Decimal(1010000)   # nxdy_excc_amt
    assert balance.settlement_cash_d2 == Decimal(1020000)   # prvs_rcdl_excc_amt


def test_positions_keeps_zero_quantity_lot():
    rows = [_holding("005930", hldg="0", sellable="0", evlu="0", pfls="0", pfls_rt="0")]
    positions = _client(FakeTransport(response=_balance_resp(rows=rows))).account.domestic.positions()
    assert positions[0].quantity == Decimal(0)


def test_positions_blank_lot_field_reads_zero_not_raise():
    row = dict(_holding("005930"), evlu_pfls_rt="", pchs_avg_pric="")   # 정산 lot 빈 필드
    positions = _client(FakeTransport(response=_balance_resp(rows=[row]))).account.domestic.positions()
    assert positions[0].unrealized_pnl_percent == Decimal(0)
    assert positions[0].average_purchase_price == Decimal(0)


def test_positions_garbage_lot_field_still_fails_closed():
    row = dict(_holding("005930"), evlu_amt="N/A")   # 값 있는데 파싱 실패
    with pytest.raises(KISError):
        _client(FakeTransport(response=_balance_resp(rows=[row]))).account.domestic.positions()


@pytest.mark.parametrize(("limit_price", "expected"), [(75000, "75000"), (Decimal("7E4"), "70000")])
def test_ticker_buyable_formats_limit_price(limit_price, expected):
    fake = FakeTransport(response=_buyable_resp())
    _client(fake).domestic.stock("005930").buyable(limit_price=limit_price)
    assert fake.calls[0]["params"]["ORD_UNPR"] == expected


def test_sellable_unparseable_quantity_fails_closed():
    output1 = {"pdno": "005930", "prdt_name": "삼성전자", "cblc_qty": "N/A", "ord_psbl_qty": "8"}
    with pytest.raises(KISError):
        _client(FakeTransport(response=_sellable_resp(output1))).domestic.stock("005930").sellable()


def test_fetch_buyable_amount_only_rejects_limit_price():
    fake = FakeTransport(response=_buyable_resp())
    with pytest.raises(KISUsageError):   # symbol 없이 단가 -> 무의미(모듈 레벨 가드)
        account_module.fetch_buyable_amount(
            fake, cano="12345678", product_code="01", environment="real", limit_price=70000
        )


# --- 값 의미론 -------------------------------------------------------------
def test_balance_value_semantics_ignore_raw_and_hashable():
    first = _client(FakeTransport(response=_balance_resp())).account.domestic.balance()
    second = _client(FakeTransport(response=_balance_resp(summary=dict(_SUMMARY, extra="x")))).account.domestic.balance()
    assert first == second
    assert hash(first) == hash(second)
    assert {first, second} == {first}


def test_position_value_semantics_ignore_raw_and_hashable():
    first = _client(FakeTransport(response=_balance_resp(rows=[_holding("005930")]))).account.domestic.positions()[0]
    second = _client(FakeTransport(response=_balance_resp(
        rows=[dict(_holding("005930"), extra="x")]))).account.domestic.positions()[0]
    assert first == second
    assert hash(first) == hash(second)


def test_buyable_sellable_portfolio_value_semantics():
    buy = _client(FakeTransport(response=_buyable_resp())).domestic.stock("005930").buyable()
    buy_other = _client(FakeTransport(response=_buyable_resp(dict(_BUYABLE_OUTPUT, extra="x")))).domestic.stock("005930").buyable()
    assert buy == buy_other and hash(buy) == hash(buy_other)
    sell = _client(FakeTransport(response=_sellable_resp())).domestic.stock("005930").sellable()
    assert hash(sell) == hash(_client(FakeTransport(response=_sellable_resp())).domestic.stock("005930").sellable())
    port = _client(FakeTransport(response=_balance_resp(rows=[_holding("005930")]))).account.domestic.portfolio()
    port_other = _client(FakeTransport(response=_balance_resp(rows=[_holding("005930")]))).account.domestic.portfolio()
    assert port == port_other and hash(port) == hash(port_other)
