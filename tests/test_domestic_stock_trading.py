"""국내주식 매매/잔고(trading) 테스트 -- 계좌 요약(Balance)·보유종목(Position) 파싱,
연속조회 페이지네이션(병합·상한 fail-closed), 환경별 TR, 빈 종목행 skip, 0수량 lot 유지,
fail-closed 수치 파싱, output2 배열/단일 양형, DomesticStock.trading 배선. 네트워크 없이
가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from unittest.mock import Mock, sentinel

import pytest

from kis_openapi.domestic_stock import DomesticStock
from kis_openapi.domestic_stock.trading import (
    Balance,
    BuyableAmount,
    ExecutionReport,
    Order,
    OrderStore,
    Portfolio,
    Position,
    SellableQuantity,
    Trading,
)
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


# --- buyable (매수가능조회) -----------------------------------------------
_BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
_SELLABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-sell"

_BUYABLE_OUTPUT = {
    "ord_psbl_cash": "1000000", "ruse_psbl_amt": "0",
    "nrcvb_buy_amt": "980000", "nrcvb_buy_qty": "13",
    "max_buy_amt": "2000000", "max_buy_qty": "27",
}


def _buyable_resp(output=None):
    body = {"output": output if output is not None else dict(_BUYABLE_OUTPUT)}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


def _sellable_resp(output1=None):
    default = {"pdno": "005930", "prdt_name": "삼성전자", "cblc_qty": "10", "ord_psbl_qty": "8"}
    body = {"output1": output1 if output1 is not None else default}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


def test_buyable_parses_amounts_and_quantities():
    result = _trading(FakeTransport(response=_buyable_resp())).buyable_amount("005930", limit_price=75000)
    assert isinstance(result, BuyableAmount)
    assert result.symbol == "005930"
    assert result.currency == "KRW"
    assert result.orderable_cash == Decimal(1000000)
    assert result.reusable_cash == Decimal(0)
    assert result.cash_buyable_amount == Decimal(980000)
    assert result.cash_buyable_quantity == Decimal(13)
    assert result.max_buyable_amount == Decimal(2000000)
    assert result.max_buyable_quantity == Decimal(27)


def test_buyable_with_limit_price_uses_limit_division():
    fake = FakeTransport(response=_buyable_resp())
    _trading(fake).buyable_amount("005930", limit_price=75000)
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["idempotent"] is True
    assert call["path"] == _BUYABLE_PATH
    assert call["tr_id"] == "TTTC8908R"
    assert call["params"]["PDNO"] == "005930"
    assert call["params"]["ORD_DVSN"] == "00"             # 지정가
    assert call["params"]["ORD_UNPR"] == "75000"


def test_buyable_without_price_uses_market_division():
    fake = FakeTransport(response=_buyable_resp())
    _trading(fake).buyable_amount("005930")
    assert fake.calls[0]["params"]["ORD_DVSN"] == "01"     # 시장가(증거금율 반영)
    assert fake.calls[0]["params"]["ORD_UNPR"] == ""


@pytest.mark.parametrize(("limit_price", "expected"), [(75000, "75000"), (Decimal("7E4"), "70000")])
def test_buyable_formats_limit_price_as_fixed_point(limit_price, expected):
    fake = FakeTransport(response=_buyable_resp())
    _trading(fake).buyable_amount("005930", limit_price=limit_price)
    assert fake.calls[0]["params"]["ORD_UNPR"] == expected   # 지수표기 없이 고정소수점


def test_buyable_amount_only_omits_symbol_and_zeroes_quantities():
    output = dict(_BUYABLE_OUTPUT, nrcvb_buy_qty="", max_buy_qty="")  # 종목 없으면 수량 빔
    fake = FakeTransport(response=_buyable_resp(output))
    result = _trading(fake).buyable_amount()                # 종목 없이 금액만
    assert result.symbol == ""
    assert result.cash_buyable_quantity == Decimal(0)      # 빈 수량 -> 0(docstring 약속)
    assert result.max_buyable_quantity == Decimal(0)
    assert fake.calls[0]["params"]["PDNO"] == ""


def test_buyable_rejects_limit_price_without_symbol():
    fake = FakeTransport(response=_buyable_resp())
    with pytest.raises(KisUsageError):                      # 금액만 조회에 단가는 무의미
        _trading(fake).buyable_amount(limit_price=75000)
    assert fake.calls == []


def test_buyable_uses_demo_tr():
    fake = FakeTransport(response=_buyable_resp())
    _trading(fake, environment="demo").buyable_amount("005930")
    assert fake.calls[0]["tr_id"] == "VTTC8908R"


@pytest.mark.parametrize("limit_price", ["not-a-number", -1, Decimal(0), float("nan"), float("inf")])
def test_buyable_bad_limit_price_raises_before_io(limit_price):
    fake = FakeTransport(response=_buyable_resp())
    with pytest.raises(KisUsageError):                      # 비숫자/0/음수/비유한 -> 전송 전 거부
        _trading(fake).buyable_amount("005930", limit_price=limit_price)
    assert fake.calls == []


def test_buyable_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={})
    with pytest.raises(KisError):
        _trading(FakeTransport(response=resp)).buyable_amount("005930")


def test_buyable_error_response_raises():
    with pytest.raises(KisError):
        _trading(FakeTransport(response=_ERROR)).buyable_amount("005930")


@pytest.mark.parametrize("wire_field", ["ord_psbl_cash", "ruse_psbl_amt", "nrcvb_buy_amt", "max_buy_qty"])
def test_buyable_unparseable_field_fails_closed(wire_field):
    output = dict(_BUYABLE_OUTPUT, **{wire_field: "N/A"})   # 값 있는데 파싱 실패 -> raise
    with pytest.raises(KisError):
        _trading(FakeTransport(response=_buyable_resp(output))).buyable_amount("005930")


# --- sellable (매도가능수량조회, 모의 미지원) ------------------------------
def test_sellable_parses_quantities():
    result = _trading(FakeTransport(response=_sellable_resp())).sellable_quantity("005930")
    assert isinstance(result, SellableQuantity)
    assert result.symbol == "005930"
    assert result.security_name == "삼성전자"
    assert result.quantity == Decimal(10)                 # 잔고
    assert result.sellable_quantity == Decimal(8)         # 매도가능


def test_sellable_sends_get_idempotent_with_pdno_and_fixed_tr():
    fake = FakeTransport(response=_sellable_resp())
    _trading(fake).sellable_quantity("005930")
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["idempotent"] is True
    assert call["path"] == _SELLABLE_PATH
    assert call["tr_id"] == "TTTC8408R"
    assert call["params"]["PDNO"] == "005930"


def test_sellable_not_held_symbol_reads_zero():
    output1 = {"pdno": "005930", "prdt_name": "", "cblc_qty": "", "ord_psbl_qty": ""}
    result = _trading(FakeTransport(response=_sellable_resp(output1))).sellable_quantity("005930")
    assert result.quantity == Decimal(0)                  # 미보유 -> 빈 수량을 0으로
    assert result.sellable_quantity == Decimal(0)


@pytest.mark.parametrize("wire_field", ["cblc_qty", "ord_psbl_qty"])
def test_sellable_unparseable_quantity_fails_closed(wire_field):
    output1 = {"pdno": "005930", "prdt_name": "삼성전자", "cblc_qty": "10", "ord_psbl_qty": "8"}
    output1[wire_field] = "N/A"
    with pytest.raises(KisError):
        _trading(FakeTransport(response=_sellable_resp(output1))).sellable_quantity("005930")


def test_sellable_demo_raises_before_io():
    fake = FakeTransport(response=_sellable_resp())
    with pytest.raises(KisUsageError):
        _trading(fake, environment="demo").sellable_quantity("005930")
    assert fake.calls == []                                # 모의 미지원 -> 전송 전 중단


def test_sellable_missing_output1_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={})
    with pytest.raises(KisError):
        _trading(FakeTransport(response=resp)).sellable_quantity("005930")


# --- 값 의미론(raw 제외 동등성/해시) --------------------------------------
def test_buyable_value_semantics_ignore_raw_and_hashable():
    first = _trading(FakeTransport(response=_buyable_resp())).buyable_amount("005930")
    second = _trading(
        FakeTransport(response=_buyable_resp(dict(_BUYABLE_OUTPUT, extra="x")))
    ).buyable_amount("005930")
    assert first == second
    assert hash(first) == hash(second)
    assert {first, second} == {first}


def test_sellable_value_semantics_ignore_raw_and_hashable():
    base = {"pdno": "005930", "prdt_name": "삼성전자", "cblc_qty": "10", "ord_psbl_qty": "8"}
    first = _trading(FakeTransport(response=_sellable_resp(base))).sellable_quantity("005930")
    second = _trading(FakeTransport(response=_sellable_resp(dict(base, extra="x")))).sellable_quantity("005930")
    different = _trading(
        FakeTransport(response=_sellable_resp(dict(base, ord_psbl_qty="7")))
    ).sellable_quantity("005930")
    assert first == second
    assert first != different
    assert hash(first) == hash(second)
    assert {first, second} == {first}


# --- 주문 실행 위임(안전 코어) ---------------------------------------------
_ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_ORDER_ACCEPTED_RESPONSE = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"ODNO": "0000117057", "ORD_TMD": "121052"}},
)


def _trading_with_store(fake):
    return Trading(fake, cano="12345678", product_code="01", store=OrderStore())


def test_trading_buy_delegates_to_order_engine():
    fake = FakeTransport(response=_ORDER_ACCEPTED_RESPONSE)
    report = _trading_with_store(fake).buy("005930", quantity=10, limit_price=70000)
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000117057"
    assert report.symbol == "005930"
    assert report.side == "buy"
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _ORDER_CASH_PATH
    assert call["idempotent"] is False             # 주문은 타임아웃 재시도 금지


def test_trading_place_delegates_to_order_engine():
    order = Order.limit("005930", side="buy", quantity=10, limit_price=70000)
    fake = FakeTransport(response=_ORDER_ACCEPTED_RESPONSE)
    report = _trading_with_store(fake).place(order)
    assert report.order_id == "0000117057"


def test_trading_sell_delegates_to_order_engine():
    fake = FakeTransport(response=_ORDER_ACCEPTED_RESPONSE)
    report = _trading_with_store(fake).sell("005930", quantity=10, limit_price=70000)
    assert report.side == "sell"


def test_trading_reconcile_delegates_and_replays_completed():
    fake = FakeTransport(response=_ORDER_ACCEPTED_RESPONSE)
    trading = _trading_with_store(fake)
    order = Order.limit("005930", side="buy", quantity=10, limit_price=70000)
    placed = trading.place(order)
    replayed = trading.reconcile(order.client_order_id)   # 완료 리포트 replay(추가 호출 없음)
    assert replayed is not None
    assert replayed.order_id == placed.order_id
    assert len(fake.calls) == 1                    # place 만 -- reconcile 은 로컬 replay


def test_trading_execution_requires_store():
    trading = Trading(FakeTransport(response=_balance_resp()), cano="12345678", product_code="01")
    for place_order in (
        lambda: trading.buy("005930", quantity=10),
        lambda: trading.sell("005930", quantity=10),
        lambda: trading.place(Order.market("005930", side="buy", quantity=10)),
        lambda: trading.reconcile("some-id"),
    ):
        with pytest.raises(KisUsageError):         # store 없이 주문/재조회 -> 안내 예외
            place_order()
    assert trading.balance().deposit == Decimal(1000000)   # 조회는 store 없이도 된다


@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        ("buy", {"quantity": 10, "limit_price": 70000, "time_in_force": "ioc",
                 "exchange": "XKRX", "client_order_id": "BUY-ID"}),
        ("sell", {"quantity": 3, "limit_price": 120000, "time_in_force": "fok",
                  "exchange": "XKRX", "client_order_id": "SELL-ID"}),
    ],
)
def test_trading_forwards_order_arguments_unchanged(method_name, kwargs, monkeypatch):
    trading = _trading_with_store(FakeTransport(response=_ORDER_ACCEPTED_RESPONSE))
    engine = Mock()
    getattr(engine, method_name).return_value = sentinel.report
    monkeypatch.setattr(trading, "_orders", engine)
    result = getattr(trading, method_name)("005930", **kwargs)
    assert result is sentinel.report               # 반환도 그대로 통과
    getattr(engine, method_name).assert_called_once_with("005930", **kwargs)  # 인자 무변경 전달


def test_trading_buy_forwards_market_order_defaults(monkeypatch):
    trading = _trading_with_store(FakeTransport(response=_ORDER_ACCEPTED_RESPONSE))
    engine = Mock()
    engine.buy.return_value = sentinel.report
    monkeypatch.setattr(trading, "_orders", engine)
    trading.buy("005930", quantity=10)             # limit_price 없음 -> 시장가 기본값 전달
    engine.buy.assert_called_once_with(
        "005930", quantity=10, limit_price=None,
        time_in_force="day", exchange="XKRX", client_order_id=None,
    )


def test_trading_place_and_reconcile_forward_unchanged(monkeypatch):
    trading = _trading_with_store(FakeTransport(response=_ORDER_ACCEPTED_RESPONSE))
    engine = Mock()
    engine.place.return_value = sentinel.placed
    engine.reconcile.return_value = sentinel.reconciled
    monkeypatch.setattr(trading, "_orders", engine)
    order = Order.limit("005930", side="buy", quantity=10, limit_price=70000)
    assert trading.place(order) is sentinel.placed
    engine.place.assert_called_once_with(order)     # 같은 Order 그대로
    assert trading.reconcile("ORDER-ID") is sentinel.reconciled
    engine.reconcile.assert_called_once_with("ORDER-ID")


def test_domestic_stock_trading_executes_with_store():
    fake = FakeTransport(response=_ORDER_ACCEPTED_RESPONSE)
    stock = DomesticStock(fake, cano="12345678", product_code="01", store=OrderStore())
    report = stock.trading.buy("005930", quantity=10, limit_price=70000)
    assert report.order_id == "0000117057"


def test_domestic_stock_without_store_reads_but_rejects_writes():
    stock = DomesticStock(FakeTransport(response=_balance_resp()), cano="12345678", product_code="01")
    assert stock.trading.balance().deposit == Decimal(1000000)   # 조회 OK
    with pytest.raises(KisUsageError):                           # 주문은 store 필요
        stock.trading.buy("005930", quantity=10)


def test_domestic_stock_rejects_store_without_account():
    with pytest.raises(KisUsageError):   # 계좌 없이 store 만 -> 락 stranded 방지
        DomesticStock(FakeTransport(response=_balance_resp()), store=OrderStore())


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
