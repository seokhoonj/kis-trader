"""해외선물옵션(08) 계좌 조회 -- kis.account -> OverseasDerivativesAccount.

예수금현황/미결제(보유)/주문가능을 네트워크 없이 FakeTransport 로 검증한다. 모든 조회는 실전
전용이라 모의(paper)면 와이어 이전에 fail-closed 한다. 금액·수량은 조회 통화의 Decimal(원화 아님).
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient
from kis_trader.errors import KISError, KISUsageError
from kis_trader.overseas.derivative_account import OverseasDerivativesAccount
from kis_trader.overseas.entities.derivative_account import (
    OverseasDerivativeDailyOrder,
    OverseasDerivativeDeposit,
    OverseasDerivativeFill,
    OverseasDerivativeFillHistory,
    OverseasDerivativeMargin,
    OverseasDerivativeOrder,
    OverseasDerivativeOrderable,
    OverseasDerivativePNL,
    OverseasDerivativePNLHistory,
    OverseasDerivativePosition,
    OverseasDerivativeTransaction,
)
from kis_trader.transport import RawResponse

_DEPOSIT_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-deposit"
_POSITIONS_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-unpd"
_ORDERABLE_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-psamount"
_MARGIN_PATH = "/uapi/overseas-futureoption/v1/trading/margin-detail"
_TODAY_ORDERS_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-ccld"
_DAILY_FILLS_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-daily-ccld"
_DAILY_ORDERS_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-daily-order"
_PERIOD_PNL_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-period-ccld"
_PERIOD_TRANS_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-period-trans"


class FakeTransport:
    def __init__(self, *, response=None, by_path=None, raises=None, pages=None):
        self.response = response
        self.by_path = by_path or {}
        self.raises = raises
        self.pages = pages          # 경로무관 순차 응답(연속조회 흉내)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent, "tr_cont": tr_cont})
        if self.pages:
            outcome = self.pages.pop(0)
        elif path in self.by_path:
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


def _client(transport, *, environment="real", account="12345678-08"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


_DEPOSIT = {
    "crcy_cd": "USD",
    "fm_dnca_rmnd": "100000.50", "fm_tot_asst_evlu_amt": "125000.75",
    "fm_fuop_evlu_pfls_amt": "1500.25", "fm_lqd_pfls_amt": "300.00",
    "fm_brkg_mgn_amt": "20000.00", "fm_mntn_mgn_amt": "18000.00",
    "fm_add_mgn_amt": "0", "fm_risk_rt": "45.30",
    "fm_ord_psbl_amt": "80000.00", "fm_drwg_psbl_amt": "75000.00",
    "fm_rcvb_amt": "0", "fm_nxdy_dncl_amt": "100000.50",
    "fm_opt_evlu_amt": "500.00", "fm_fee": "12.50",
}


def _deposit_resp(*, output=None):
    body = {"output": output if output is not None else dict(_DEPOSIT)}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="D")


def test_deposit_paper_fails_closed():
    fake = FakeTransport(response=_deposit_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.deposit()
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_deposit_parses_and_routes():
    fake = FakeTransport(response=_deposit_resp())
    kis = _client(fake)
    assert isinstance(kis.account, OverseasDerivativesAccount)
    dep = kis.account.deposit(currency="USD", date="20240216")
    assert isinstance(dep, OverseasDerivativeDeposit)
    assert dep.currency == "USD"                                    # crcy_cd
    assert dep.cash_balance == Decimal("100000.50")                # fm_dnca_rmnd
    assert dep.total_asset == Decimal("125000.75")                 # fm_tot_asst_evlu_amt
    assert dep.unrealized_pnl == Decimal("1500.25")                # fm_fuop_evlu_pfls_amt
    assert dep.realized_pnl == Decimal("300.00")                   # fm_lqd_pfls_amt
    assert dep.brokerage_margin == Decimal("20000.00")            # fm_brkg_mgn_amt
    assert dep.risk_rate == Decimal("45.30")                      # fm_risk_rt
    assert dep.orderable_amount == Decimal("80000.00")           # fm_ord_psbl_amt
    assert dep.fee == Decimal("12.50")                           # fm_fee
    call = fake.calls[0]
    assert call["tr_id"] == "OTFM1411R"
    assert call["path"] == _DEPOSIT_PATH
    assert call["params"]["CRCY_CD"] == "USD"
    assert call["params"]["INQR_DT"] == "20240216"
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "08"


def test_deposit_default_currency_and_date():
    fake = FakeTransport(response=_deposit_resp())
    _client(fake).account.deposit()
    call = fake.calls[0]
    assert call["params"]["CRCY_CD"] == "USD"                       # 기본 통화
    assert len(call["params"]["INQR_DT"]) == 8                      # 오늘(YYYYMMDD)
    assert call["params"]["INQR_DT"].isdigit()


def test_deposit_bad_date_fails_closed():
    fake = FakeTransport(response=_deposit_resp())
    with pytest.raises(KISUsageError):
        _client(fake).account.deposit(date="2024-02-16")
    assert fake.calls == []  # 날짜 검증도 와이어 이전


def test_deposit_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={}, tr_cont="D")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.deposit()


def test_deposit_non_mapping_output_raises():
    for output in ([], 42):  # 단일블록 조회는 output 이 매핑이어야 -- 리스트/스칼라는 손상
        with pytest.raises(KISError):
            _client(FakeTransport(response=_deposit_resp(output=output))).account.deposit()


def test_deposit_blank_amounts_read_as_zero():
    output = dict(_DEPOSIT, fm_add_mgn_amt="", fm_rcvb_amt="  ")
    dep = _client(FakeTransport(response=_deposit_resp(output=output))).account.deposit()
    assert dep.additional_margin == Decimal(0)
    assert dep.receivable == Decimal(0)


def test_overseas_derivative_deposit_entity_importable():
    from kis_trader import OverseasDerivativeDeposit as Exported

    assert Exported is OverseasDerivativeDeposit


# --- 미결제내역(보유) OTFM1412R -------------------------------------------

def _position(pdno="6EU24", *, prdt="FX", crcy="USD", side="02", qty="3", avg="1.0850",
              now="1.0865", pnl="1875.00", opt="0", opt_pnl="0", lqd="3", ecis="N"):
    return {"ovrs_futr_fx_pdno": pdno, "prdt_type_cd": prdt, "crcy_cd": crcy,
            "sll_buy_dvsn_cd": side, "fm_ustl_qty": qty, "fm_ccld_avg_pric": avg,
            "fm_now_pric": now, "fm_evlu_pfls_amt": pnl, "fm_opt_evlu_amt": opt,
            "fm_otp_evlu_pfls_amt": opt_pnl, "fm_lqd_psbl_qty": lqd, "ecis_rsvn_ord_yn": ecis}


def _positions_resp(*, rows=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output": rows if rows is not None else [],
            "ctx_area_nk100": ctx_nk, "ctx_area_fk100": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_positions_paper_fails_closed():
    fake = FakeTransport(response=_positions_resp(rows=[_position()]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.positions()
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_positions_parses_and_routes():
    fake = FakeTransport(response=_positions_resp(rows=[_position()]))
    pos = _client(fake).account.positions()
    assert isinstance(pos, list)
    assert isinstance(pos[0], OverseasDerivativePosition)
    assert pos[0].symbol == "6EU24"                    # ovrs_futr_fx_pdno
    assert pos[0].product_type == "FX"                 # prdt_type_cd
    assert pos[0].currency == "USD"                    # crcy_cd
    assert pos[0].side == "buy"                         # sll_buy_dvsn_cd 02 -> buy
    assert pos[0].quantity == Decimal(3)             # fm_ustl_qty
    assert pos[0].average_price == Decimal("1.0850")   # fm_ccld_avg_pric
    assert pos[0].current_price == Decimal("1.0865")   # fm_now_pric
    assert pos[0].unrealized_pnl == Decimal("1875.00")  # fm_evlu_pfls_amt
    assert pos[0].liquidatable_quantity == Decimal(3)  # fm_lqd_psbl_qty
    assert pos[0].exercise_reserved == "N"             # ecis_rsvn_ord_yn
    call = fake.calls[0]
    assert call["tr_id"] == "OTFM1412R"
    assert call["path"].endswith("inquire-unpd")
    assert call["params"]["FUOP_DVSN"] == "00"
    assert call["params"]["CTX_AREA_FK100"] == ""
    assert call["params"]["CTX_AREA_NK100"] == ""
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "08"


def test_positions_sell_side():
    fake = FakeTransport(response=_positions_resp(rows=[_position(side="01")]))
    pos = _client(fake).account.positions()
    assert pos[0].side == "sell"                       # sll_buy_dvsn_cd 01 -> sell


def test_positions_custom_fuop():
    fake = FakeTransport(response=_positions_resp(rows=[_position()]))
    _client(fake).account.positions(fuop="01")
    assert fake.calls[0]["params"]["FUOP_DVSN"] == "01"


def test_positions_paginates():
    page1 = _positions_resp(rows=[_position("6EU24")], ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _positions_resp(rows=[_position("6JU24")], tr_cont="D")
    fake = FakeTransport(by_path={_POSITIONS_PATH: [page1, page2]})
    pos = _client(fake).account.positions()
    assert [p.symbol for p in pos] == ["6EU24", "6JU24"]
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK100"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"             # 연속조회 헤더


def test_positions_skips_blank_symbol_row():
    rows = [_position("6EU24"), _position("")]
    pos = _client(FakeTransport(response=_positions_resp(rows=rows))).account.positions()
    assert [p.symbol for p in pos] == ["6EU24"]


def test_positions_non_mapping_row_raises():
    with pytest.raises(KISError):
        _client(FakeTransport(response=_positions_resp(rows=[None]))).account.positions()


def test_positions_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"ctx_area_nk100": "", "ctx_area_fk100": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.positions()


def test_positions_non_list_output_raises():
    with pytest.raises(KISError):  # 목록 조회는 output 이 배열이어야 -- 매핑은 손상
        _client(FakeTransport(response=_positions_resp(rows={}))).account.positions()


def test_positions_page_cap_fails_closed():
    # 매 페이지 연속키가 진전하며 끝나지 않는 상황 -- 상한에서 fail-closed(같은 키 반복은 종료로 본다).
    pages = [_positions_resp(rows=[_position()], ctx_nk=f"N{i}", ctx_fk="FK", tr_cont="F")
             for i in range(101)]
    with pytest.raises(KISError):
        _client(FakeTransport(pages=pages)).account.positions()


def test_overseas_derivative_position_entity_importable():
    from kis_trader import OverseasDerivativePosition as Exported

    assert Exported is OverseasDerivativePosition


# --- 주문가능수량 OTFM3304R -----------------------------------------------

_ORDERABLE = {
    "ovrs_futr_fx_pdno": "6EU24", "crcy_cd": "USD",
    "fm_ustl_qty": "3", "fm_lqd_psbl_qty": "3",
    "fm_new_ord_psbl_qty": "10", "fm_tot_ord_psbl_qty": "13",
    "fm_mkpr_tot_ord_psbl_qty": "13",
}


def _orderable_resp(*, output=None):
    body = {"output": output if output is not None else dict(_ORDERABLE)}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="D")


def test_orderable_paper_fails_closed():
    fake = FakeTransport(response=_orderable_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.orderable("6EU24", "buy")
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_orderable_parses_and_routes():
    fake = FakeTransport(response=_orderable_resp())
    ord_ = _client(fake).account.orderable("6EU24", "buy", price="1.0850")
    assert isinstance(ord_, OverseasDerivativeOrderable)
    assert ord_.symbol == "6EU24"                             # ovrs_futr_fx_pdno
    assert ord_.currency == "USD"                             # crcy_cd
    assert ord_.open_quantity == Decimal(3)                   # fm_ustl_qty
    assert ord_.liquidatable_quantity == Decimal(3)          # fm_lqd_psbl_qty
    assert ord_.new_orderable_quantity == Decimal(10)        # fm_new_ord_psbl_qty
    assert ord_.total_orderable_quantity == Decimal(13)      # fm_tot_ord_psbl_qty
    assert ord_.market_orderable_quantity == Decimal(13)     # fm_mkpr_tot_ord_psbl_qty
    call = fake.calls[0]
    assert call["tr_id"] == "OTFM3304R"
    assert call["path"] == _ORDERABLE_PATH
    assert call["params"]["OVRS_FUTR_FX_PDNO"] == "6EU24"
    assert call["params"]["SLL_BUY_DVSN_CD"] == "02"          # buy -> 02
    assert call["params"]["FM_ORD_PRIC"] == "1.0850"
    assert call["params"]["ECIS_RSVN_ORD_YN"] == "N"
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "08"


def test_orderable_sell_side_and_market_price():
    fake = FakeTransport(response=_orderable_resp())
    _client(fake).account.orderable("6EU24", "sell")
    call = fake.calls[0]
    assert call["params"]["SLL_BUY_DVSN_CD"] == "01"          # sell -> 01
    assert call["params"]["FM_ORD_PRIC"] == "0"              # price 없음 -> 시장가 "0"


def test_orderable_exercise_reserved_flag():
    fake = FakeTransport(response=_orderable_resp())
    _client(fake).account.orderable("6EU24", "buy", exercise_reserved=True)
    assert fake.calls[0]["params"]["ECIS_RSVN_ORD_YN"] == "Y"


def test_orderable_bad_price_fails_closed():
    fake = FakeTransport(response=_orderable_resp())
    with pytest.raises(KISUsageError):
        _client(fake).account.orderable("6EU24", "buy", price="-1")
    assert fake.calls == []  # 단가 검증도 와이어 이전


def test_orderable_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={}, tr_cont="D")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.orderable("6EU24", "buy")


def test_orderable_missing_new_orderable_qty_raises():
    output = dict(_ORDERABLE)
    del output["fm_new_ord_psbl_qty"]
    with pytest.raises(KISError):
        _client(FakeTransport(response=_orderable_resp(output=output))).account.orderable("6EU24", "buy")


def test_orderable_non_mapping_output_raises():
    for output in ([], 42):  # 단일블록 조회는 output 이 매핑이어야 -- 리스트/스칼라는 손상
        with pytest.raises(KISError):
            _client(FakeTransport(response=_orderable_resp(output=output))).account.orderable(
                "6EU24", "buy"
            )


def test_overseas_derivative_orderable_entity_importable():
    from kis_trader import OverseasDerivativeOrderable as Exported

    assert Exported is OverseasDerivativeOrderable


# --- 증거금상세 OTFM3115R -------------------------------------------------

_MARGIN = {
    "crcy_cd": "USD",
    "fm_ord_psbl_amt": "80000.00", "fm_brkg_mgn_amt": "20000.00",
    "fm_excc_brkg_mgn_amt": "19500.00", "fm_ustl_mgn_amt": "17000.00",
    "fm_mntn_mgn_amt": "18000.00", "fm_ord_mgn_amt": "1000.00",
    "fm_add_mgn_amt": "0", "acnt_net_risk_mgna_aply_yn": "Y",
    # SPAN/EUREX 상세 필드는 헤드라인이 아니라 _raw 로만 노출된다.
    "fm_span_mgn_amt": "16000.00", "fm_eurx_mgn_amt": "500.00",
}


def _margin_resp(*, output=None):
    body = {"output": output if output is not None else dict(_MARGIN)}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="D")


def test_margin_detail_paper_fails_closed():
    fake = FakeTransport(response=_margin_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.margin_detail()
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_margin_detail_parses_and_routes():
    fake = FakeTransport(response=_margin_resp())
    margin = _client(fake).account.margin_detail(currency="USD", date="20240216")
    assert isinstance(margin, OverseasDerivativeMargin)
    assert margin.currency == "USD"                                  # crcy_cd
    assert margin.orderable_amount == Decimal("80000.00")           # fm_ord_psbl_amt
    assert margin.brokerage_margin == Decimal("20000.00")          # fm_brkg_mgn_amt
    assert margin.settlement_brokerage_margin == Decimal("19500.00")  # fm_excc_brkg_mgn_amt
    assert margin.open_margin == Decimal("17000.00")              # fm_ustl_mgn_amt
    assert margin.maintenance_margin == Decimal("18000.00")       # fm_mntn_mgn_amt
    assert margin.order_margin == Decimal("1000.00")             # fm_ord_mgn_amt
    assert margin.additional_margin == Decimal(0)               # fm_add_mgn_amt
    assert margin.net_risk_applied == "Y"                       # acnt_net_risk_mgna_aply_yn
    assert margin._raw["fm_span_mgn_amt"] == "16000.00"          # 상세 필드는 _raw 로
    call = fake.calls[0]
    assert call["tr_id"] == "OTFM3115R"
    assert call["path"] == _MARGIN_PATH
    assert call["params"]["CRCY_CD"] == "USD"
    assert call["params"]["INQR_DT"] == "20240216"
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "08"


def test_margin_detail_default_currency_and_date():
    fake = FakeTransport(response=_margin_resp())
    _client(fake).account.margin_detail()
    call = fake.calls[0]
    assert call["params"]["CRCY_CD"] == "USD"                        # 기본 통화
    assert len(call["params"]["INQR_DT"]) == 8                       # 오늘(YYYYMMDD)
    assert call["params"]["INQR_DT"].isdigit()


def test_margin_detail_bad_date_fails_closed():
    fake = FakeTransport(response=_margin_resp())
    with pytest.raises(KISUsageError):
        _client(fake).account.margin_detail(date="2024-02-16")
    assert fake.calls == []  # 날짜 검증도 와이어 이전


def test_margin_detail_blank_amounts_read_as_zero():
    output = dict(_MARGIN, fm_add_mgn_amt="", fm_ord_mgn_amt="  ")
    margin = _client(FakeTransport(response=_margin_resp(output=output))).account.margin_detail()
    assert margin.additional_margin == Decimal(0)
    assert margin.order_margin == Decimal(0)


def test_margin_detail_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={}, tr_cont="D")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.margin_detail()


def test_margin_detail_non_mapping_output_raises():
    for output in ([], 42):  # 단일블록 조회는 output 이 매핑이어야 -- 리스트/스칼라는 손상
        with pytest.raises(KISError):
            _client(FakeTransport(response=_margin_resp(output=output))).account.margin_detail()


def test_overseas_derivative_margin_entity_importable():
    from kis_trader import OverseasDerivativeMargin as Exported

    assert Exported is OverseasDerivativeMargin


# --- 당일 주문내역 OTFM3116R ----------------------------------------------

def _order(odno="0001", *, orgn="0000", pdno="6BZ22", side="02", stat="02",
           ord_dt="20240216", ord_qty="2", ord_pric="1.2650", ccld_qty="0",
           ccld_pric="0", rmn_qty="2", new_lqd="01", fuop="01"):
    return {"ord_dt": ord_dt, "odno": odno, "orgn_odno": orgn,
            "ovrs_futr_fx_pdno": pdno, "sll_buy_dvsn_cd": side, "ord_stat_cd": stat,
            "fm_ord_qty": ord_qty, "fm_ord_pric": ord_pric, "fm_ccld_qty": ccld_qty,
            "fm_ccld_pric": ccld_pric, "fm_ord_rmn_qty": rmn_qty,
            "new_lqd_dvsn_cd": new_lqd, "fuop_dvsn": fuop}


def _today_orders_resp(*, rows=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output": rows if rows is not None else [],
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_today_orders_paper_fails_closed():
    fake = FakeTransport(response=_today_orders_resp(rows=[_order()]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.today_orders()
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_today_orders_parses_and_routes():
    from datetime import date

    fake = FakeTransport(response=_today_orders_resp(rows=[_order()]))
    orders = _client(fake).account.today_orders()
    assert isinstance(orders, list)
    assert isinstance(orders[0], OverseasDerivativeOrder)
    assert orders[0].order_date == date(2024, 2, 16)     # ord_dt
    assert orders[0].order_id == "0001"                   # odno
    assert orders[0].original_order_id == "0000"          # orgn_odno
    assert orders[0].symbol == "6BZ22"                    # ovrs_futr_fx_pdno
    assert orders[0].side == "buy"                         # sll_buy_dvsn_cd 02 -> buy
    assert orders[0].status == "02"                       # ord_stat_cd
    assert orders[0].order_quantity == Decimal(2)         # fm_ord_qty
    assert orders[0].order_price == Decimal("1.2650")     # fm_ord_pric
    assert orders[0].filled_quantity == Decimal(0)        # fm_ccld_qty
    assert orders[0].remaining_quantity == Decimal(2)     # fm_ord_rmn_qty
    assert orders[0].new_liquidation == "01"              # new_lqd_dvsn_cd
    assert orders[0].fuop == "01"                         # fuop_dvsn
    call = fake.calls[0]
    assert call["tr_id"] == "OTFM3116R"
    assert call["path"].endswith("inquire-ccld")
    assert call["params"]["CCLD_NCCS_DVSN"] == "00"       # 전체 체결+미체결
    assert call["params"]["SLL_BUY_DVSN_CD"] == "00"      # 전체
    assert call["params"]["FUOP_DVSN"] == "00"            # 전체
    assert call["params"]["CTX_AREA_FK200"] == ""
    assert call["params"]["CTX_AREA_NK200"] == ""
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "08"


def test_today_orders_sell_side():
    fake = FakeTransport(response=_today_orders_resp(rows=[_order(side="01")]))
    orders = _client(fake).account.today_orders()
    assert orders[0].side == "sell"                       # sll_buy_dvsn_cd 01 -> sell


def test_today_orders_paginates():
    page1 = _today_orders_resp(rows=[_order(odno="0001", pdno="6BZ22")],
                               ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _today_orders_resp(rows=[_order(odno="0002", pdno="6EU24")], tr_cont="D")
    fake = FakeTransport(by_path={_TODAY_ORDERS_PATH: [page1, page2]})
    orders = _client(fake).account.today_orders()
    assert [o.order_id for o in orders] == ["0001", "0002"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"                # 연속조회 헤더


def test_today_orders_skips_blank_order_id_row():
    rows = [_order(odno="0001"), _order(odno="")]
    orders = _client(FakeTransport(response=_today_orders_resp(rows=rows))).account.today_orders()
    assert [o.order_id for o in orders] == ["0001"]


def test_today_orders_non_mapping_row_raises():
    with pytest.raises(KISError):
        _client(FakeTransport(response=_today_orders_resp(rows=[None]))).account.today_orders()


def test_today_orders_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"ctx_area_nk200": "", "ctx_area_fk200": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.today_orders()


def test_today_orders_non_list_output_raises():
    with pytest.raises(KISError):  # 목록 조회는 output 이 배열이어야 -- 매핑은 손상
        _client(FakeTransport(response=_today_orders_resp(rows={}))).account.today_orders()


def test_overseas_derivative_order_entity_importable():
    from kis_trader import OverseasDerivativeOrder as Exported

    assert Exported is OverseasDerivativeOrder


# --- 일별 체결내역 OTFM3122R ----------------------------------------------

def _fill(ccno="C0001", *, dt="20240216", pdno="6BZ22", side="02", ccld_qty="2",
          ccld_amt="253000.00", crcy="USD", fee="12.50", ord_dt="20240216",
          odno="0001", mdia="MTS"):
    return {"dt": dt, "ccno": ccno, "ovrs_futr_fx_pdno": pdno, "sll_buy_dvsn_cd": side,
            "fm_ccld_qty": ccld_qty, "fm_ccld_amt": ccld_amt, "crcy_cd": crcy,
            "fm_fee": fee, "ord_dt": ord_dt, "odno": odno, "ord_mdia_dvsn_name": mdia}


_FILL_TOTALS = {
    "fm_tot_ccld_qty": "5", "fm_tot_futr_agrm_amt": "500000.00",
    "fm_tot_opt_agrm_amt": "0", "fm_fee_smtl": "31.25",
}


def _daily_fills_resp(*, rows=None, totals=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output1": rows if rows is not None else [],
            "output2": totals if totals is not None else dict(_FILL_TOTALS),
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_daily_fills_paper_fails_closed():
    fake = FakeTransport(response=_daily_fills_resp(rows=[_fill()]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.daily_fills(start="20240201", end="20240216")
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_daily_fills_bad_date_fails_closed():
    fake = FakeTransport(response=_daily_fills_resp(rows=[_fill()]))
    with pytest.raises(KISUsageError):
        _client(fake).account.daily_fills(start="2024-02-01", end="20240216")
    assert fake.calls == []  # 날짜 검증도 와이어 이전


def test_daily_fills_parses_and_routes():
    from datetime import date

    fake = FakeTransport(response=_daily_fills_resp(rows=[_fill()]))
    hist = _client(fake).account.daily_fills(start="20240201", end="20240216")
    assert isinstance(hist, OverseasDerivativeFillHistory)
    assert hist.total_filled_quantity == Decimal(5)                     # fm_tot_ccld_qty
    assert hist.total_futures_agreement_amount == Decimal("500000.00")  # fm_tot_futr_agrm_amt
    assert hist.total_options_agreement_amount == Decimal(0)            # fm_tot_opt_agrm_amt
    assert hist.total_fee == Decimal("31.25")                           # fm_fee_smtl
    assert len(hist.fills) == 1
    fill = hist.fills[0]
    assert isinstance(fill, OverseasDerivativeFill)
    assert fill.date == date(2024, 2, 16)                       # dt
    assert fill.fill_number == "C0001"                          # ccno
    assert fill.symbol == "6BZ22"                               # ovrs_futr_fx_pdno
    assert fill.side == "buy"                                    # sll_buy_dvsn_cd 02 -> buy
    assert fill.filled_quantity == Decimal(2)                   # fm_ccld_qty
    assert fill.filled_amount == Decimal("253000.00")          # fm_ccld_amt
    assert fill.currency == "USD"                               # crcy_cd
    assert fill.fee == Decimal("12.50")                        # fm_fee
    assert fill.order_date == date(2024, 2, 16)                # ord_dt
    assert fill.order_id == "0001"                              # odno
    assert fill.order_medium == "MTS"                          # ord_mdia_dvsn_name
    call = fake.calls[0]
    assert call["tr_id"] == "OTFM3122R"
    assert call["path"].endswith("inquire-daily-ccld")
    assert call["params"]["STRT_DT"] == "20240201"
    assert call["params"]["END_DT"] == "20240216"
    assert call["params"]["FUOP_DVSN_CD"] == "00"
    assert call["params"]["FM_PDGR_CD"] == ""
    assert call["params"]["CRCY_CD"] == "%%%"                  # 전체 통화
    assert call["params"]["FM_ITEM_FTNG_YN"] == "N"
    assert call["params"]["SLL_BUY_DVSN_CD"] == "%%"           # 전체
    assert call["params"]["CTX_AREA_FK200"] == ""
    assert call["params"]["CTX_AREA_NK200"] == ""
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "08"


def test_daily_fills_sell_side():
    fake = FakeTransport(response=_daily_fills_resp(rows=[_fill(side="01")]))
    hist = _client(fake).account.daily_fills(start="20240201", end="20240216")
    assert hist.fills[0].side == "sell"                        # sll_buy_dvsn_cd 01 -> sell


def test_daily_fills_paginates_and_reads_totals_first_page():
    page1 = _daily_fills_resp(rows=[_fill(ccno="C0001", odno="0001")],
                              ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    # 후속 페이지의 output2 는 무시된다(합계는 첫 페이지에서 완결).
    page2 = _daily_fills_resp(rows=[_fill(ccno="C0002", odno="0002")],
                              totals={"fm_tot_ccld_qty": "999"}, tr_cont="D")
    fake = FakeTransport(by_path={_DAILY_FILLS_PATH: [page1, page2]})
    hist = _client(fake).account.daily_fills(start="20240201", end="20240216")
    assert [f.fill_number for f in hist.fills] == ["C0001", "C0002"]
    assert hist.total_filled_quantity == Decimal(5)            # 첫 페이지 output2
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"                     # 연속조회 헤더


def test_daily_fills_skips_blank_identifier_row():
    rows = [_fill(ccno="C0001", odno="0001"), _fill(ccno="", odno="")]
    hist = _client(FakeTransport(response=_daily_fills_resp(rows=rows))).account.daily_fills(
        start="20240201", end="20240216"
    )
    assert [f.fill_number for f in hist.fills] == ["C0001"]


def test_daily_fills_missing_output1_raises():
    body = {"output2": dict(_FILL_TOTALS), "ctx_area_nk200": "", "ctx_area_fk200": ""}
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="D")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.daily_fills(start="20240201", end="20240216")


def test_daily_fills_missing_output2_raises():
    body = {"output1": [_fill()], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="D")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.daily_fills(start="20240201", end="20240216")


def test_daily_fills_invalid_output1_block_raises():
    with pytest.raises(KISError):  # output1 은 체결내역 배열이어야 -- 매핑은 손상
        _client(FakeTransport(response=_daily_fills_resp(rows={}))).account.daily_fills(
            start="20240201", end="20240216"
        )


def test_daily_fills_invalid_output2_block_raises():
    with pytest.raises(KISError):  # output2 가 비매핑이면 합계 요약 부재로 손상
        _client(FakeTransport(response=_daily_fills_resp(totals=42))).account.daily_fills(
            start="20240201", end="20240216"
        )


def test_daily_fills_non_mapping_row_raises():
    with pytest.raises(KISError):  # output1=[None] 등 손상 행 -> fail-closed
        _client(FakeTransport(response=_daily_fills_resp(rows=[None]))).account.daily_fills(
            start="20240201", end="20240216"
        )


def test_overseas_derivative_fill_history_entity_importable():
    from kis_trader import OverseasDerivativeFill as ExportedFill
    from kis_trader import OverseasDerivativeFillHistory as ExportedHistory

    assert ExportedFill is OverseasDerivativeFill
    assert ExportedHistory is OverseasDerivativeFillHistory


# --- 일별 주문내역 OTFM3120R ----------------------------------------------

def _daily_order(odno="0001", *, orgn="0000", dt="20240216", ord_dt="20240216",
                 pdno="6BZ22", rvse="00", side="02", ord_qty="2", ord_pric="1.2650",
                 ccld_qty="2", ccld_pric="1.2648", rmn_qty="0", rjct="",
                 trad_end="20240216"):
    return {"dt": dt, "ord_dt": ord_dt, "odno": odno, "orgn_odno": orgn,
            "ovrs_futr_fx_pdno": pdno, "rvse_cncl_dvsn_cd": rvse,
            "sll_buy_dvsn_cd": side, "fm_ord_qty": ord_qty, "fm_ord_pric": ord_pric,
            "fm_ccld_qty": ccld_qty, "fm_ccld_pric": ccld_pric,
            "fm_ord_rmn_qty": rmn_qty, "rjct_rson_name": rjct, "trad_end_dt": trad_end}


def _daily_orders_resp(*, rows=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output": rows if rows is not None else [],
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_daily_orders_paper_fails_closed():
    fake = FakeTransport(response=_daily_orders_resp(rows=[_daily_order()]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.daily_orders(start="20240201", end="20240216")
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_daily_orders_bad_date_fails_closed():
    fake = FakeTransport(response=_daily_orders_resp(rows=[_daily_order()]))
    with pytest.raises(KISUsageError):
        _client(fake).account.daily_orders(start="2024-02-01", end="20240216")
    assert fake.calls == []  # 날짜 검증도 와이어 이전


def test_daily_orders_parses_and_routes():
    from datetime import date

    fake = FakeTransport(response=_daily_orders_resp(rows=[_daily_order()]))
    orders = _client(fake).account.daily_orders(start="20240201", end="20240216")
    assert isinstance(orders, list)
    assert isinstance(orders[0], OverseasDerivativeDailyOrder)
    assert orders[0].date == date(2024, 2, 16)                # dt
    assert orders[0].order_date == date(2024, 2, 16)          # ord_dt
    assert orders[0].order_id == "0001"                       # odno
    assert orders[0].original_order_id == "0000"              # orgn_odno
    assert orders[0].symbol == "6BZ22"                        # ovrs_futr_fx_pdno
    assert orders[0].revise_cancel_type == "00"              # rvse_cncl_dvsn_cd
    assert orders[0].side == "buy"                             # sll_buy_dvsn_cd 02 -> buy
    assert orders[0].order_quantity == Decimal(2)             # fm_ord_qty
    assert orders[0].order_price == Decimal("1.2650")         # fm_ord_pric
    assert orders[0].filled_quantity == Decimal(2)            # fm_ccld_qty
    assert orders[0].filled_price == Decimal("1.2648")        # fm_ccld_pric
    assert orders[0].remaining_quantity == Decimal(0)         # fm_ord_rmn_qty
    assert orders[0].reject_reason == ""                     # rjct_rson_name
    assert orders[0].trade_end_date == date(2024, 2, 16)      # trad_end_dt
    call = fake.calls[0]
    assert call["tr_id"] == "OTFM3120R"
    assert call["path"].endswith("inquire-daily-order")
    assert call["params"]["STRT_DT"] == "20240201"
    assert call["params"]["END_DT"] == "20240216"
    assert call["params"]["FM_PDGR_CD"] == ""
    assert call["params"]["CCLD_NCCS_DVSN"] == "00"
    assert call["params"]["SLL_BUY_DVSN_CD"] == "%%"          # 전체
    assert call["params"]["FUOP_DVSN"] == "00"
    assert call["params"]["CTX_AREA_FK200"] == ""
    assert call["params"]["CTX_AREA_NK200"] == ""
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "08"


def test_daily_orders_sell_side():
    fake = FakeTransport(response=_daily_orders_resp(rows=[_daily_order(side="01")]))
    orders = _client(fake).account.daily_orders(start="20240201", end="20240216")
    assert orders[0].side == "sell"                           # sll_buy_dvsn_cd 01 -> sell


def test_daily_orders_paginates():
    page1 = _daily_orders_resp(rows=[_daily_order(odno="0001", pdno="6BZ22")],
                               ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _daily_orders_resp(rows=[_daily_order(odno="0002", pdno="6EU24")], tr_cont="D")
    fake = FakeTransport(by_path={_DAILY_ORDERS_PATH: [page1, page2]})
    orders = _client(fake).account.daily_orders(start="20240201", end="20240216")
    assert [o.order_id for o in orders] == ["0001", "0002"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"                    # 연속조회 헤더


def test_daily_orders_skips_blank_order_id_row():
    rows = [_daily_order(odno="0001"), _daily_order(odno="")]
    orders = _client(FakeTransport(response=_daily_orders_resp(rows=rows))).account.daily_orders(
        start="20240201", end="20240216"
    )
    assert [o.order_id for o in orders] == ["0001"]


def test_daily_orders_non_mapping_row_raises():
    with pytest.raises(KISError):
        _client(FakeTransport(response=_daily_orders_resp(rows=[None]))).account.daily_orders(
            start="20240201", end="20240216"
        )


def test_daily_orders_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"ctx_area_nk200": "", "ctx_area_fk200": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.daily_orders(start="20240201", end="20240216")


def test_daily_orders_non_list_output_raises():
    with pytest.raises(KISError):  # 목록 조회는 output 이 배열이어야 -- 매핑은 손상
        _client(FakeTransport(response=_daily_orders_resp(rows={}))).account.daily_orders(
            start="20240201", end="20240216"
        )


def test_overseas_derivative_daily_order_entity_importable():
    from kis_trader import OverseasDerivativeDailyOrder as Exported

    assert Exported is OverseasDerivativeDailyOrder


# --- 기간 손익 OTFM3118R --------------------------------------------------

def _pnl(*, crcy="USD", pdno="", buy_qty="5", sll_qty="5", lqd_pnl="300.00",
         fee="31.25", net_pnl="268.75", ustl_buy="2", ustl_sll="0",
         ustl_pnl="150.00", ustl_agrm="500000.00"):
    return {"crcy_cd": crcy, "ovrs_futr_fx_pdno": pdno, "fm_buy_qty": buy_qty,
            "fm_sll_qty": sll_qty, "fm_lqd_pfls_amt": lqd_pnl, "fm_fee": fee,
            "fm_net_pfls_amt": net_pnl, "fm_ustl_buy_qty": ustl_buy,
            "fm_ustl_sll_qty": ustl_sll, "fm_ustl_evlu_pfls_amt": ustl_pnl,
            "fm_ustl_agrm_amt": ustl_agrm}


def _period_pnl_resp(*, by_currency=None, by_symbol=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output1": by_currency if by_currency is not None else [_pnl()],
            "output2": by_symbol if by_symbol is not None else [_pnl(pdno="6BZ22")],
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_period_pnl_paper_fails_closed():
    fake = FakeTransport(response=_period_pnl_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.period_pnl(start="20240201", end="20240216")
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_period_pnl_bad_date_fails_closed():
    fake = FakeTransport(response=_period_pnl_resp())
    with pytest.raises(KISUsageError):
        _client(fake).account.period_pnl(start="20240201", end="2024-02-16")
    assert fake.calls == []  # 날짜 검증도 와이어 이전


def test_period_pnl_parses_and_routes():
    fake = FakeTransport(response=_period_pnl_resp())
    hist = _client(fake).account.period_pnl(start="20240201", end="20240216")
    assert isinstance(hist, OverseasDerivativePNLHistory)
    assert len(hist.by_currency) == 1
    assert len(hist.by_symbol) == 1
    ccy = hist.by_currency[0]
    assert isinstance(ccy, OverseasDerivativePNL)
    assert ccy.currency == "USD"                              # crcy_cd
    assert ccy.symbol == ""                                   # per-currency block
    assert ccy.buy_quantity == Decimal(5)                     # fm_buy_qty
    assert ccy.sell_quantity == Decimal(5)                    # fm_sll_qty
    assert ccy.realized_pnl == Decimal("300.00")             # fm_lqd_pfls_amt
    assert ccy.fee == Decimal("31.25")                       # fm_fee
    assert ccy.net_pnl == Decimal("268.75")                  # fm_net_pfls_amt
    assert ccy.open_buy_quantity == Decimal(2)               # fm_ustl_buy_qty
    assert ccy.open_sell_quantity == Decimal(0)              # fm_ustl_sll_qty
    assert ccy.unrealized_pnl == Decimal("150.00")           # fm_ustl_evlu_pfls_amt
    assert ccy.open_agreement_amount == Decimal("500000.00")  # fm_ustl_agrm_amt
    sym = hist.by_symbol[0]
    assert sym.symbol == "6BZ22"                              # per-symbol block
    call = fake.calls[0]
    assert call["tr_id"] == "OTFM3118R"
    assert call["path"].endswith("inquire-period-ccld")
    assert call["params"]["INQR_TERM_FROM_DT"] == "20240201"
    assert call["params"]["INQR_TERM_TO_DT"] == "20240216"
    assert call["params"]["CRCY_CD"] == "%%%"                 # 전체 통화
    assert call["params"]["WHOL_TRSL_YN"] == "N"
    assert call["params"]["FUOP_DVSN"] == "00"
    assert call["params"]["CTX_AREA_FK200"] == ""
    assert call["params"]["CTX_AREA_NK200"] == ""
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "08"


def test_period_pnl_paginates_accumulating_both_blocks():
    page1 = _period_pnl_resp(by_currency=[_pnl(crcy="USD")],
                             by_symbol=[_pnl(pdno="6BZ22")],
                             ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _period_pnl_resp(by_currency=[_pnl(crcy="EUR")],
                             by_symbol=[_pnl(pdno="6EU24")], tr_cont="D")
    fake = FakeTransport(by_path={_PERIOD_PNL_PATH: [page1, page2]})
    hist = _client(fake).account.period_pnl(start="20240201", end="20240216")
    assert [p.currency for p in hist.by_currency] == ["USD", "EUR"]
    assert [p.symbol for p in hist.by_symbol] == ["6BZ22", "6EU24"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"                    # 연속조회 헤더


def test_period_pnl_skips_all_blank_row():
    by_currency = [_pnl(crcy="USD"), _pnl(crcy="", pdno="")]
    hist = _client(FakeTransport(response=_period_pnl_resp(by_currency=by_currency))).account.period_pnl(
        start="20240201", end="20240216"
    )
    assert [p.currency for p in hist.by_currency] == ["USD"]


@pytest.mark.parametrize("block", [{"by_currency": [None]}, {"by_symbol": [None]}])
def test_period_pnl_non_mapping_row_raises(block):
    with pytest.raises(KISError):  # output1/output2 어느 쪽의 [None] 이든 -> fail-closed
        _client(FakeTransport(response=_period_pnl_resp(**block))).account.period_pnl(
            start="20240201", end="20240216"
        )


def test_period_pnl_missing_output1_raises():
    body = {"output2": [_pnl(pdno="6BZ22")], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="D")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.period_pnl(start="20240201", end="20240216")


def test_period_pnl_missing_output2_raises():
    body = {"output1": [_pnl()], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="D")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.period_pnl(start="20240201", end="20240216")


def test_period_pnl_invalid_output1_block_raises():
    with pytest.raises(KISError):  # output1 은 손익 배열이어야 -- 매핑은 손상
        _client(FakeTransport(response=_period_pnl_resp(by_currency={}))).account.period_pnl(
            start="20240201", end="20240216"
        )


def test_period_pnl_invalid_output2_block_raises():
    with pytest.raises(KISError):  # output2 는 손익 배열이어야 -- 매핑은 손상
        _client(FakeTransport(response=_period_pnl_resp(by_symbol={}))).account.period_pnl(
            start="20240201", end="20240216"
        )


def test_overseas_derivative_pnl_history_entity_importable():
    from kis_trader import OverseasDerivativePNL as ExportedPnl
    from kis_trader import OverseasDerivativePNLHistory as ExportedHistory

    assert ExportedPnl is OverseasDerivativePNL
    assert ExportedHistory is OverseasDerivativePNLHistory


# --- 기간 입출금내역 OTFM3114R --------------------------------------------

def _transaction(seq="0001", *, bass_dt="20240216", tr_type="입금", crcy="USD",
                 item="원화입금", iofw="10000.00", fee="0", tax="0",
                 sttl="0", bf_dncl="90000.00", dncl="100000.00",
                 rcvb_occr="0", rcvb_pybk="0", rmks="ATM"):
    return {"bass_dt": bass_dt, "fm_ldgr_inog_seq": seq, "acnt_tr_type_name": tr_type,
            "crcy_cd": crcy, "tr_itm_name": item, "fm_iofw_amt": iofw, "fm_fee": fee,
            "fm_tax_amt": tax, "fm_sttl_amt": sttl, "fm_bf_dncl_amt": bf_dncl,
            "fm_dncl_amt": dncl, "fm_rcvb_occr_amt": rcvb_occr,
            "fm_rcvb_pybk_amt": rcvb_pybk, "rmks_text": rmks}


def _period_trans_resp(*, rows=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output": rows if rows is not None else [],
            "ctx_area_nk100": ctx_nk, "ctx_area_fk100": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_period_trans_paper_fails_closed():
    fake = FakeTransport(response=_period_trans_resp(rows=[_transaction()]))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.transactions(start="20240201", end="20240216")
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_period_trans_bad_date_fails_closed():
    fake = FakeTransport(response=_period_trans_resp(rows=[_transaction()]))
    with pytest.raises(KISUsageError):
        _client(fake).account.transactions(start="20240201", end="2024-02-16")
    assert fake.calls == []  # 날짜 검증도 와이어 이전


def test_period_trans_parses_and_routes():
    from datetime import date

    fake = FakeTransport(response=_period_trans_resp(rows=[_transaction()]))
    trans = _client(fake).account.transactions(start="20240201", end="20240216")
    assert isinstance(trans, list)
    assert isinstance(trans[0], OverseasDerivativeTransaction)
    assert trans[0].date == date(2024, 2, 16)                # bass_dt
    assert trans[0].ledger_sequence == "0001"                # fm_ldgr_inog_seq
    assert trans[0].transaction_type == "입금"               # acnt_tr_type_name
    assert trans[0].currency == "USD"                        # crcy_cd
    assert trans[0].item_name == "원화입금"                  # tr_itm_name
    assert trans[0].amount == Decimal("10000.00")           # fm_iofw_amt
    assert trans[0].fee == Decimal(0)                        # fm_fee
    assert trans[0].tax == Decimal(0)                        # fm_tax_amt
    assert trans[0].settlement_amount == Decimal(0)          # fm_sttl_amt
    assert trans[0].prior_deposit == Decimal("90000.00")     # fm_bf_dncl_amt
    assert trans[0].deposit == Decimal("100000.00")          # fm_dncl_amt
    assert trans[0].receivable_incurred == Decimal(0)        # fm_rcvb_occr_amt
    assert trans[0].receivable_repaid == Decimal(0)          # fm_rcvb_pybk_amt
    assert trans[0].remarks == "ATM"                         # rmks_text
    call = fake.calls[0]
    assert call["tr_id"] == "OTFM3114R"
    assert call["path"].endswith("inquire-period-trans")
    assert call["params"]["INQR_TERM_FROM_DT"] == "20240201"
    assert call["params"]["INQR_TERM_TO_DT"] == "20240216"
    assert call["params"]["ACNT_TR_TYPE_CD"] == "%%"          # 전체
    assert call["params"]["CRCY_CD"] == "%%%"                 # 전체 통화
    assert call["params"]["PWD_CHK_YN"] == "N"                # 비밀번호 확인 안 함
    assert call["params"]["CTX_AREA_FK100"] == ""
    assert call["params"]["CTX_AREA_NK100"] == ""
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "08"


def test_period_trans_paginates():
    page1 = _period_trans_resp(rows=[_transaction(seq="0001")],
                               ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _period_trans_resp(rows=[_transaction(seq="0002")], tr_cont="D")
    fake = FakeTransport(by_path={_PERIOD_TRANS_PATH: [page1, page2]})
    trans = _client(fake).account.transactions(start="20240201", end="20240216")
    assert [t.ledger_sequence for t in trans] == ["0001", "0002"]
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK100"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"                    # 연속조회 헤더


def test_period_trans_skips_blank_identifier_row():
    rows = [_transaction(seq="0001"), _transaction(seq="", bass_dt="")]
    trans = _client(FakeTransport(response=_period_trans_resp(rows=rows))).account.transactions(
        start="20240201", end="20240216"
    )
    assert [t.ledger_sequence for t in trans] == ["0001"]


def test_period_trans_non_mapping_row_raises():
    with pytest.raises(KISError):
        _client(FakeTransport(response=_period_trans_resp(rows=[None]))).account.transactions(
            start="20240201", end="20240216"
        )


def test_period_trans_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"ctx_area_nk100": "", "ctx_area_fk100": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.transactions(start="20240201", end="20240216")


def test_transactions_non_list_output_raises():
    with pytest.raises(KISError):  # 목록 조회는 output 이 배열이어야 -- 매핑은 손상
        _client(FakeTransport(response=_period_trans_resp(rows={}))).account.transactions(
            start="20240201", end="20240216"
        )


def test_overseas_derivative_transaction_entity_importable():
    from kis_trader import OverseasDerivativeTransaction as Exported

    assert Exported is OverseasDerivativeTransaction
