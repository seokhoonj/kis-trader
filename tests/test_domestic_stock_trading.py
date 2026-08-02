"""국내주식 매매/잔고(trading) 테스트 -- 계좌 요약(Balance)·보유종목(Position) 파싱,
연속조회 페이지네이션(병합·상한 fail-closed), 환경별 TR, 빈 종목행 skip, 0수량 lot 유지,
fail-closed 수치 파싱, output2 배열/단일 양형, DomesticStock.trading 배선. 네트워크 없이
가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi.domestic_stock import DomesticStock
from kis_openapi.domestic_stock.trading import Balance, Portfolio, Position, Trading
from kis_openapi.domestic_stock.trading import facade as facade_module
from kis_openapi.errors import KisError, KisUsageError
from kis_openapi.transport import RawResponse

_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"


class FakeTransport:
    """읽기 전용 가짜 전송. 기본 응답/경로별 순차 응답/예외 지원, 모든 호출 기록."""

    def __init__(self, *, response=None, by_path=None, raises=None):
        self.response = response
        self.by_path = by_path or {}
        self.raises = raises
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
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


# --- 표본 응답 -------------------------------------------------------------
_SUMMARY = {
    "dnca_tot_amt": "1000000", "nxdy_excc_amt": "1010000", "prvs_rcdl_excc_amt": "1020000",
    "tot_evlu_amt": "1500000", "nass_amt": "2520000", "pchs_amt_smtl_amt": "1400000",
    "evlu_amt_smtl_amt": "1500000", "evlu_pfls_smtl_amt": "100000",
}


def _holding(pdno, *, name="삼성전자", hldg="10", sellable="10", avg="70000", pchs="700000",
             prpr="71500", evlu="715000", pfls="15000", pfls_rt="2.14"):
    return {"pdno": pdno, "prdt_name": name, "hldg_qty": hldg, "ord_psbl_qty": sellable,
            "pchs_avg_pric": avg, "pchs_amt": pchs, "prpr": prpr, "evlu_amt": evlu,
            "evlu_pfls_amt": pfls, "evlu_pfls_rt": pfls_rt}


def _balance_resp(*, rows=None, summary=None, ctx_nk="", ctx_fk=""):
    body = {
        "output1": rows if rows is not None else [],
        "output2": [summary if summary is not None else dict(_SUMMARY)],
        "ctx_area_nk100": ctx_nk, "ctx_area_fk100": ctx_fk,
    }
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


_ERROR = RawResponse(rt_cd="1", msg_cd="EGW00215", msg1="초당 거래건수 초과", body={})


def _trading(fake, *, environment="real"):
    return Trading(fake, cano="12345678", product_code="01", environment=environment)


# --- balance ---------------------------------------------------------------
def test_balance_parses_account_summary():
    balance = _trading(FakeTransport(response=_balance_resp())).balance()
    assert isinstance(balance, Balance)
    assert balance.currency == "KRW"
    assert balance.deposit == Decimal(1000000)
    assert balance.settlement_d1 == Decimal(1010000)
    assert balance.settlement_d2 == Decimal(1020000)
    assert balance.total_evaluation == Decimal(1500000)
    assert balance.net_asset == Decimal(2520000)
    assert balance.purchase_amount == Decimal(1400000)
    assert balance.market_value == Decimal(1500000)
    assert balance.unrealized_pnl == Decimal(100000)


def test_balance_accepts_summary_as_single_object():
    body = {"output1": [], "output2": dict(_SUMMARY)}   # 배열 아닌 단일 객체
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)
    balance = _trading(FakeTransport(response=resp)).balance()
    assert balance.deposit == Decimal(1000000)


def test_balance_missing_summary_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": []})
    with pytest.raises(KisError):
        _trading(FakeTransport(response=resp)).balance()


def test_balance_error_response_raises():
    with pytest.raises(KisError):
        _trading(FakeTransport(response=_ERROR)).balance()


def test_balance_unparseable_amount_fails_closed():
    summary = dict(_SUMMARY, dnca_tot_amt="N/A")
    with pytest.raises(KisError):
        _trading(FakeTransport(response=_balance_resp(summary=summary))).balance()


def test_balance_uses_real_tr_and_get():
    fake = FakeTransport(response=_balance_resp())
    _trading(fake, environment="real").balance()
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["idempotent"] is True
    assert call["path"] == _BALANCE_PATH
    assert call["tr_id"] == "TTTC8434R"
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "01"


def test_balance_uses_demo_tr():
    fake = FakeTransport(response=_balance_resp())
    _trading(fake, environment="demo").balance()
    assert fake.calls[0]["tr_id"] == "VTTC8434R"


# --- positions -------------------------------------------------------------
def test_positions_parses_holdings():
    rows = [_holding("005930")]
    positions = _trading(FakeTransport(response=_balance_resp(rows=rows))).positions()
    assert len(positions) == 1
    holding = positions[0]
    assert isinstance(holding, Position)
    assert holding.symbol == "005930"
    assert holding.security_name == "삼성전자"
    assert holding.currency == "KRW"
    assert holding.quantity == Decimal(10)
    assert holding.sellable_quantity == Decimal(10)
    assert holding.average_purchase_price == Decimal(70000)
    assert holding.purchase_amount == Decimal(700000)
    assert holding.current_price == Decimal(71500)
    assert holding.market_value == Decimal(715000)
    assert holding.unrealized_pnl == Decimal(15000)
    assert holding.unrealized_pnl_percent == Decimal("2.14")


def test_positions_skips_empty_pdno_padding_rows():
    rows = [_holding("005930"), _holding("", name=""), {"prdt_name": "빈행"}]
    positions = _trading(FakeTransport(response=_balance_resp(rows=rows))).positions()
    assert [p.symbol for p in positions] == ["005930"]


def test_positions_keeps_zero_quantity_lot():
    rows = [_holding("005930", hldg="0", sellable="0", evlu="0", pfls="0", pfls_rt="0")]
    positions = _trading(FakeTransport(response=_balance_resp(rows=rows))).positions()
    assert positions[0].quantity == Decimal(0)   # 정산 대기 lot 도 유지


def test_positions_paginates_and_merges_pages():
    page1 = _balance_resp(rows=[_holding("005930")], ctx_nk="NEXT", ctx_fk="FK")
    page2 = _balance_resp(rows=[_holding("000660", name="SK하이닉스")])  # ctx_nk="" -> 마지막
    fake = FakeTransport(by_path={_BALANCE_PATH: [page1, page2]})
    positions = _trading(fake).positions()
    assert [p.symbol for p in positions] == ["005930", "000660"]
    assert len(fake.calls) == 2
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "NEXT"   # 다음 페이지에 연속키 전달
    assert fake.calls[1]["params"]["CTX_AREA_FK100"] == "FK"


def test_positions_error_response_raises():
    with pytest.raises(KisError):
        _trading(FakeTransport(response=_ERROR)).positions()


def test_positions_unparseable_quantity_fails_closed():
    rows = [_holding("005930", hldg="N/A")]
    with pytest.raises(KisError):
        _trading(FakeTransport(response=_balance_resp(rows=rows))).positions()


def test_positions_pagination_cap_fails_closed(monkeypatch):
    monkeypatch.setattr(facade_module, "_MAX_BALANCE_PAGES", 3)
    # 매 페이지가 연속키를 계속 내밈 -> 영영 안 끝남 -> 상한에서 fail-closed.
    endless = _balance_resp(rows=[_holding("005930")], ctx_nk="NEXT", ctx_fk="FK")
    with pytest.raises(KisError):
        _trading(FakeTransport(response=endless)).positions()


@pytest.mark.parametrize(("environment", "expected_tr"), [("real", "TTTC8434R"), ("demo", "VTTC8434R")])
def test_positions_uses_environment_tr_and_account_params(environment, expected_tr):
    fake = FakeTransport(response=_balance_resp(rows=[]))
    _trading(fake, environment=environment).positions()
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["idempotent"] is True
    assert call["path"] == _BALANCE_PATH
    assert call["tr_id"] == expected_tr
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "01"
    assert call["params"]["INQR_DVSN"] == "02"


@pytest.mark.parametrize(
    "body",
    [{"output2": [dict(_SUMMARY)]}, {"output1": {"pdno": "005930"}, "output2": [dict(_SUMMARY)]}],
    ids=["missing-output1", "non-list-output1"],
)
def test_positions_malformed_output1_fails_closed(body):
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)
    with pytest.raises(KisError):
        _trading(FakeTransport(response=resp)).positions()


# --- balance 는 첫 페이지만 --------------------------------------------------
def test_balance_reads_only_first_page_summary():
    page1 = _balance_resp(summary=dict(_SUMMARY, dnca_tot_amt="111"), ctx_nk="NEXT", ctx_fk="FK")
    page2 = _balance_resp(summary=dict(_SUMMARY, dnca_tot_amt="222"))
    fake = FakeTransport(by_path={_BALANCE_PATH: [page1, page2]})
    balance = _trading(fake).balance()
    assert balance.deposit == Decimal(111)     # 첫 페이지 요약만
    assert len(fake.calls) == 1                 # 페이지네이션 안 함


def test_balance_empty_summary_list_raises():
    body = {"output1": [], "output2": []}       # 빈 배열 -> 요약 없음
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)
    with pytest.raises(KisError):
        _trading(FakeTransport(response=resp)).balance()


# --- portfolio (한 번의 순회로 둘 다) --------------------------------------
def test_portfolio_returns_balance_and_positions_from_one_walk():
    page1 = _balance_resp(rows=[_holding("005930")], ctx_nk="NEXT", ctx_fk="FK")
    page2 = _balance_resp(rows=[_holding("000660", name="SK하이닉스")])
    fake = FakeTransport(by_path={_BALANCE_PATH: [page1, page2]})
    portfolio = _trading(fake).portfolio()
    assert isinstance(portfolio, Portfolio)
    assert portfolio.balance.deposit == Decimal(1000000)
    assert [p.symbol for p in portfolio.positions] == ["005930", "000660"]
    assert len(fake.calls) == 2                 # positions 페이지 수만큼(요약 별도 콜 없음)


# --- 값 의미론(raw 제외 동등성/해시) --------------------------------------
def test_position_value_semantics_ignore_raw_and_hashable():
    first = _trading(FakeTransport(response=_balance_resp(rows=[_holding("005930")]))).positions()[0]
    second = _trading(
        FakeTransport(response=_balance_resp(rows=[dict(_holding("005930"), extra="x")]))
    ).positions()[0]
    assert first == second                       # raw 무시
    assert hash(first) == hash(second)
    assert {first, second} == {first}


def test_balance_value_semantics_ignore_raw_and_hashable():
    first = _trading(FakeTransport(response=_balance_resp())).balance()
    second = _trading(FakeTransport(response=_balance_resp(summary=dict(_SUMMARY, extra="x")))).balance()
    assert first == second
    assert hash(first) == hash(second)
    assert {first, second} == {first}


# --- facade 배선 -----------------------------------------------------------
def test_domestic_stock_trading_requires_account():
    stock = DomesticStock(FakeTransport(response=_balance_resp()))  # 계좌정보 없음
    with pytest.raises(KisUsageError):
        _ = stock.trading


@pytest.mark.parametrize("account", [{"cano": "12345678"}, {"product_code": "01"}])
def test_domestic_stock_rejects_partial_account(account):
    with pytest.raises(KisUsageError):   # cano/product_code 한쪽만 -> 생성 시점에 거부
        DomesticStock(FakeTransport(response=_balance_resp()), **account)


def test_domestic_stock_exposes_trading_with_account():
    fake = FakeTransport(response=_balance_resp())
    stock = DomesticStock(fake, cano="12345678", product_code="01", environment="demo")
    balance = stock.trading.balance()
    assert balance.deposit == Decimal(1000000)
    assert fake.calls[0]["tr_id"] == "VTTC8434R"   # 환경 전달됨
