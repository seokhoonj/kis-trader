"""국내선물옵션(03) 계좌 조회 -- kis.account -> DomesticDerivativesAccount.balance().

선물옵션 잔고(보유내역 output1 + 계좌 요약 output2)를 네트워크 없이 FakeTransport 로 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient
from kis_trader.domestic.derivative_account import DomesticDerivativesAccount
from kis_trader.domestic.entities.derivative_account import DerivativeBalance, DerivativeDeposit
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_BALANCE_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-balance"
_DEPOSIT_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-deposit"


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
                               "params": params, "idempotent": idempotent, "tr_cont": tr_cont})
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
    "tot_dncl_amt": "50000000", "dnca_cash": "48000000", "mgna_tota": "20000000",
    "ord_psbl_cash": "30000000", "ord_psbl_tota": "31000000",
    "evlu_pfls_amt_smtl": "12345", "trad_pfls_amt_smtl": "6789",
    "futr_evlu_pfls_amt": "12345", "opt_evlu_pfls_amt": "0",
    "futr_trad_pfls_amt": "6789", "opt_trad_pfls_amt": "0",
    "prsm_dpast_amt": "51000000",
}


def _position(shtn="101W09", *, pdno="KR4101RC0000", name="코스피200 F 202509", side="매수",
              qty="3", excc="410.50", avg="408.25", pchs="122475000", evlu="123150000",
              pnl="12345", trad="6789", lqd="3"):
    return {"shtn_pdno": shtn, "pdno": pdno, "prdt_name": name, "sll_buy_dvsn_name": side,
            "cblc_qty": qty, "excc_unpr": excc, "ccld_avg_unpr1": avg, "pchs_amt": pchs,
            "evlu_amt": evlu, "evlu_pfls_amt": pnl, "trad_pfls_amt": trad, "lqd_psbl_qty": lqd}


def _balance_resp(*, rows=None, summary=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output1": rows if rows is not None else [],
            "output2": summary if summary is not None else dict(_SUMMARY),
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def _client(transport, *, environment="paper", account="12345678-03"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def test_derivative_balance_parses_and_routes():
    fake = FakeTransport(response=_balance_resp(rows=[_position()]))
    kis = _client(fake)
    assert isinstance(kis.account, DomesticDerivativesAccount)
    bal = kis.account.balance()
    assert isinstance(bal, DerivativeBalance)
    assert bal.positions[0].symbol == "101W09"          # shtn_pdno, NOT pdno
    assert bal.positions[0].isin == "KR4101RC0000"      # pdno
    assert bal.positions[0].unrealized_pnl == Decimal(12345)
    assert bal.positions[0].quantity == Decimal(3)
    assert bal.total_unrealized_pnl == Decimal(12345)  # evlu_pfls_amt_smtl
    assert bal.account_value == Decimal(51000000)      # prsm_dpast_amt
    assert bal.total_margin == Decimal(20000000)       # mgna_tota
    call = fake.calls[0]
    assert call["tr_id"] == "VTFO6118R"
    assert call["path"].endswith("inquire-balance")
    assert call["params"]["MGNA_DVSN"] == "01"
    assert call["params"]["EXCC_STAT_CD"] == "1"
    assert call["params"]["CTX_AREA_FK200"] == ""
    assert call["params"]["CTX_AREA_NK200"] == ""
    assert call["params"]["CANO"] == "12345678"


def test_derivative_balance_real_tr():
    fake = FakeTransport(response=_balance_resp(rows=[_position()]))
    _client(fake, environment="real").account.balance()
    assert fake.calls[0]["tr_id"] == "CTFO6118R"


def test_derivative_balance_paginates():
    page1 = _balance_resp(rows=[_position("101W09")], ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _balance_resp(rows=[_position("201X12", pdno="KR4201RC0000")], tr_cont="D")
    fake = FakeTransport(by_path={_BALANCE_PATH: [page1, page2]})
    bal = _client(fake).account.balance()
    assert [p.symbol for p in bal.positions] == ["101W09", "201X12"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"    # 연속조회 헤더


def test_derivative_balance_missing_output2_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": [], "ctx_area_nk200": "", "ctx_area_fk200": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.balance()


def test_derivative_balance_skips_blank_symbol_row():
    rows = [_position("101W09"), _position("", pdno="")]
    bal = _client(FakeTransport(response=_balance_resp(rows=rows))).account.balance()
    assert [p.symbol for p in bal.positions] == ["101W09"]


def test_derivative_balance_page_cap_fails_closed():
    # 연속조회가 끝나지 않는(항상 tr_cont="F") 응답 -- 페이지 상한에서 부분 결과로 자르지 않고 예외.
    never_ends = _balance_resp(rows=[_position("101W09")], ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    fake = FakeTransport(response=never_ends)
    with pytest.raises(KISError):
        _client(fake).account.balance()


def test_derivative_balance_output1_non_list_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {"shtn_pdno": "101W09"}, "output2": dict(_SUMMARY),
                             "ctx_area_nk200": "", "ctx_area_fk200": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.balance()


def test_derivative_balance_output1_absent_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output2": dict(_SUMMARY),
                             "ctx_area_nk200": "", "ctx_area_fk200": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.balance()


def test_derivative_balance_summary_list_form():
    # output2 를 길이 1 배열로 줘도 단일 객체와 동일하게 파싱한다.
    bal = _client(
        FakeTransport(response=_balance_resp(rows=[_position()], summary=[dict(_SUMMARY)]))
    ).account.balance()
    assert bal.total_margin == Decimal(20000000)       # mgna_tota


def test_derivative_account_entities_importable():
    from kis_trader import DerivativeBalance, DerivativeDeposit, DerivativePosition

    assert DerivativeBalance is not None
    assert DerivativeDeposit is not None
    assert DerivativePosition is not None


_DEPOSIT = {
    "dnca_tota": "50000000", "ord_psbl_cash": "30000000", "ord_psbl_tota": "31000000",
    "brkg_mgna_cash": "18000000", "brkg_mgna_sbst": "2000000", "mtnc_rt": "418.23000000",
    "evlu_pfls_smtl": "12345", "trad_pfls_smtl": "6789",
    "futr_evlu_pfls_amt": "12345", "opt_evlu_pfls_amt": "0",
    "futr_trad_pfls": "6789", "opt_trad_pfls_amt": "0",
    "prsm_dpast_amt": "51000000", "rcva": "0",
}


def _deposit_resp(*, output=None):
    body = {"output": output if output is not None else dict(_DEPOSIT)}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="D")


def test_derivative_deposit_paper_fails_closed():
    fake = FakeTransport(response=_deposit_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.deposit()
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_derivative_deposit_parses_and_routes():
    fake = FakeTransport(response=_deposit_resp())
    dep = _client(fake, environment="real").account.deposit()
    assert isinstance(dep, DerivativeDeposit)
    assert dep.total_deposit == Decimal(50000000)              # dnca_tota
    assert dep.orderable_cash == Decimal(30000000)             # ord_psbl_cash
    assert dep.maintenance_ratio == Decimal("418.23000000")    # mtnc_rt
    assert dep.account_value == Decimal(51000000)              # prsm_dpast_amt
    assert dep.receivable == Decimal(0)                        # rcva
    assert dep.brokerage_margin_cash == Decimal(18000000)      # brkg_mgna_cash
    assert dep.brokerage_margin_substitute == Decimal(2000000)  # brkg_mgna_sbst
    assert dep.futures_realized_pnl == Decimal(6789)           # futr_trad_pfls (no _amt)
    call = fake.calls[0]
    assert call["tr_id"] == "CTRP6550R"
    assert call["path"].endswith("inquire-deposit")
    assert call["params"] == {"CANO": "12345678", "ACNT_PRDT_CD": "03"}


def test_derivative_deposit_missing_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={}, tr_cont="D")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp), environment="real").account.deposit()


_VALUATION_PL_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-balance-valuation-pl"


def _valuation_position(shtn="101W09", *, pdno="KR4101RC0000", name="코스피200 F 202509",
                        side="매수", qty="3", excc="410.50", avg="408.25", idx="411.20",
                        pchs="122475000", evlu="123150000", pnl="12345", trad="6789", lqd="3"):
    return {"shtn_pdno": shtn, "pdno": pdno, "prdt_name": name, "sll_buy_dvsn_name": side,
            "cblc_qty1": qty, "excc_unpr": excc, "ccld_avg_unpr1": avg, "idx_clpr": idx,
            "pchs_amt": pchs, "evlu_amt": evlu, "evlu_pfls_amt": pnl, "trad_pfls_amt": trad,
            "lqd_psbl_qty": lqd}


def _valuation_resp(*, rows=None, summary=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output1": rows if rows is not None else [],
            "output2": summary if summary is not None else dict(_SUMMARY),
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_valuation_pl_parses_and_routes():
    from kis_trader.domestic.entities.derivative_account import DerivativeValuationBalance

    fake = FakeTransport(response=_valuation_resp(rows=[_valuation_position()]))
    val = _client(fake, environment="real").account.valuation_pl()
    assert isinstance(val, DerivativeValuationBalance)
    assert val.positions[0].symbol == "101W09"          # shtn_pdno
    assert val.positions[0].quantity == Decimal(3)      # cblc_qty1
    assert val.positions[0].unrealized_pnl == Decimal(12345)  # evlu_pfls_amt
    assert val.total_unrealized_pnl == Decimal(12345)   # evlu_pfls_amt_smtl
    assert val.account_value == Decimal(51000000)       # prsm_dpast_amt
    assert val.total_margin == Decimal(20000000)        # mgna_tota
    call = fake.calls[0]
    assert call["tr_id"] == "CTFO6159R"
    assert call["path"].endswith("inquire-balance-valuation-pl")
    assert call["params"]["MGNA_DVSN"] == "01"
    assert call["params"]["EXCC_STAT_CD"] == "1"
    assert call["params"]["CTX_AREA_FK200"] == ""
    assert call["params"]["CTX_AREA_NK200"] == ""


def test_valuation_pl_paper_fails_closed():
    fake = FakeTransport(response=_valuation_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.valuation_pl()
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_valuation_pl_paginates():
    page1 = _valuation_resp(rows=[_valuation_position("101W09")],
                            ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _valuation_resp(rows=[_valuation_position("201X12", pdno="KR4201RC0000")], tr_cont="D")
    fake = FakeTransport(by_path={_VALUATION_PL_PATH: [page1, page2]})
    val = _client(fake, environment="real").account.valuation_pl()
    assert [p.symbol for p in val.positions] == ["101W09", "201X12"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"    # 연속조회 헤더


def test_valuation_pl_missing_output2_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": [], "ctx_area_nk200": "", "ctx_area_fk200": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp), environment="real").account.valuation_pl()


def test_derivative_valuation_entities_importable():
    from kis_trader import DerivativeValuationBalance, DerivativeValuationPosition

    assert DerivativeValuationBalance is not None
    assert DerivativeValuationPosition is not None


_SETTLEMENT_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-balance-settlement-pl"

_SETTLEMENT_SUMMARY = {
    "nxdy_dnca": "48000000", "mmga_cash": "18000000", "mmga_tota": "20000000",
    "brkg_mgna_cash": "18000000", "brkg_mgna_tota": "20000000",
    "dnca_cash": "48000000", "dnca_sbst": "2000000",
    "opt_buy_chgs": "1000000", "opt_sll_chgs": "500000", "opt_lqd_evlu_amt": "750000",
    "fee": "12345", "thdt_dfpa": "6789", "rnwl_dfpa": "1111",
}


def _settlement_position(pdno="KR4101RC0000", *, name="코스피200 F 202509", trade="매수",
                         bfdy="2", new="1", offset="0", cblc="3", cblc_amt="123150000",
                         trad="6789", evlu="123150000", pnl="12345"):
    return {"pdno": pdno, "prdt_name": name, "trad_dvsn_name": trade,
            "bfdy_cblc_qty": bfdy, "new_qty": new, "mnpl_rpch_qty": offset,
            "cblc_qty": cblc, "cblc_amt": cblc_amt, "trad_pfls_amt": trad,
            "evlu_amt": evlu, "evlu_pfls_amt": pnl}


def _settlement_resp(*, rows=None, summary=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output1": rows if rows is not None else [],
            "output2": summary if summary is not None else dict(_SETTLEMENT_SUMMARY),
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_settlement_pl_parses_and_routes():
    from kis_trader.domestic.entities.derivative_account import DerivativeSettlementBalance

    fake = FakeTransport(response=_settlement_resp(rows=[_settlement_position()]))
    stl = _client(fake, environment="real").account.settlement_pl("20240216")
    assert isinstance(stl, DerivativeSettlementBalance)
    assert stl.positions[0].symbol == "KR4101RC0000"        # pdno
    assert stl.positions[0].quantity == Decimal(3)          # cblc_qty
    assert stl.positions[0].prior_quantity == Decimal(2)    # bfdy_cblc_qty
    assert stl.positions[0].unrealized_pnl == Decimal(12345)  # evlu_pfls_amt
    assert stl.next_day_deposit == Decimal(48000000)        # nxdy_dnca
    assert stl.today_settlement_diff == Decimal(6789)       # thdt_dfpa
    assert stl.fee == Decimal(12345)                        # fee
    call = fake.calls[0]
    assert call["tr_id"] == "CTFO6117R"
    assert call["path"].endswith("inquire-balance-settlement-pl")
    assert call["params"]["INQR_DT"] == "20240216"
    assert call["params"]["CTX_AREA_FK200"] == ""
    assert call["params"]["CTX_AREA_NK200"] == ""
    assert call["params"]["CANO"] == "12345678"


def test_settlement_pl_paper_fails_closed():
    fake = FakeTransport(response=_settlement_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.settlement_pl("20240216")
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_settlement_pl_bad_date_fails_closed():
    fake = FakeTransport(response=_settlement_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="real").account.settlement_pl("2024-02-16")
    assert fake.calls == []  # 날짜 검증도 와이어 이전


def test_settlement_pl_paginates():
    page1 = _settlement_resp(rows=[_settlement_position("KR4101RC0000")],
                             ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _settlement_resp(rows=[_settlement_position("KR4201RC0000")], tr_cont="D")
    fake = FakeTransport(by_path={_SETTLEMENT_PATH: [page1, page2]})
    stl = _client(fake, environment="real").account.settlement_pl("20240216")
    assert [p.symbol for p in stl.positions] == ["KR4101RC0000", "KR4201RC0000"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"    # 연속조회 헤더


def test_settlement_pl_skips_blank_symbol_row():
    rows = [_settlement_position("KR4101RC0000"), _settlement_position("")]
    stl = _client(
        FakeTransport(response=_settlement_resp(rows=rows)), environment="real"
    ).account.settlement_pl("20240216")
    assert [p.symbol for p in stl.positions] == ["KR4101RC0000"]


def test_settlement_pl_missing_output2_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": [], "ctx_area_nk200": "", "ctx_area_fk200": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp), environment="real").account.settlement_pl("20240216")


def test_derivative_settlement_entities_importable():
    from kis_trader import DerivativeSettlementBalance, DerivativeSettlementPosition

    assert DerivativeSettlementBalance is not None
    assert DerivativeSettlementPosition is not None


_FILLS_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-ccnl-bstime"

_FILLS_SUMMARY = {
    "tot_ccld_qty_smtl": "5", "tot_ccld_amt_smtl": "205000000",
    "fee_adjt": "100", "fee_smtl": "12345",
}


def _fill(odno="0000012345", *, pdno="KR4101RC0000", name="코스피200 F 202509",
          tr_type="매수", last="20240220", idx="1", qty="3", amt="123150000",
          fee="6789", ccld_btwn="0919"):
    return {"pdno": pdno, "prdt_name": name, "odno": odno, "tr_type_name": tr_type,
            "last_sttldt": last, "ccld_idx": idx, "ccld_qty": qty, "trad_amt": amt,
            "fee": fee, "ccld_btwn": ccld_btwn}


def _fills_resp(*, rows=None, summary=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output1": rows if rows is not None else [],
            "output2": summary if summary is not None else dict(_FILLS_SUMMARY),
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_base_date_fills_parses_and_routes():
    from datetime import date as _date

    from kis_trader.domestic.entities.derivative_account import DerivativeFillHistory

    fake = FakeTransport(response=_fills_resp(rows=[_fill()]))
    hist = _client(fake, environment="real").account.base_date_fills("20240220")
    assert isinstance(hist, DerivativeFillHistory)
    assert hist.fills[0].symbol == "KR4101RC0000"       # pdno
    assert hist.fills[0].order_id == "0000012345"       # odno
    assert hist.fills[0].transaction_type == "매수"      # tr_type_name
    assert hist.fills[0].final_settlement_date == _date(2024, 2, 20)  # last_sttldt
    assert hist.fills[0].filled_quantity == Decimal(3)    # ccld_qty
    assert hist.fills[0].fill_time == "0919"            # ccld_btwn, raw str
    assert hist.total_filled_quantity == Decimal(5)     # tot_ccld_qty_smtl
    assert hist.total_fee == Decimal(12345)             # fee_smtl
    call = fake.calls[0]
    assert call["tr_id"] == "CTFO5139R"
    assert call["path"].endswith("inquire-ccnl-bstime")
    assert call["params"]["ORD_DT"] == "20240220"
    assert call["params"]["FUOP_TR_STRT_TMD"] == "000000"
    assert call["params"]["FUOP_TR_END_TMD"] == "240000"
    assert call["params"]["CTX_AREA_FK200"] == ""
    assert call["params"]["CTX_AREA_NK200"] == ""
    assert call["params"]["CANO"] == "12345678"


def test_base_date_fills_custom_time_window():
    fake = FakeTransport(response=_fills_resp(rows=[_fill()]))
    _client(fake, environment="real").account.base_date_fills(
        "20240220", start_time="090000", end_time="153000"
    )
    assert fake.calls[0]["params"]["FUOP_TR_STRT_TMD"] == "090000"
    assert fake.calls[0]["params"]["FUOP_TR_END_TMD"] == "153000"


def test_base_date_fills_paper_fails_closed():
    fake = FakeTransport(response=_fills_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.base_date_fills("20240220")
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_base_date_fills_bad_date_fails_closed():
    fake = FakeTransport(response=_fills_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="real").account.base_date_fills("2024-02-20")
    assert fake.calls == []


def test_base_date_fills_paginates():
    page1 = _fills_resp(rows=[_fill("0000012345")], ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _fills_resp(rows=[_fill("0000067890")], tr_cont="D")
    fake = FakeTransport(by_path={_FILLS_PATH: [page1, page2]})
    hist = _client(fake, environment="real").account.base_date_fills("20240220")
    assert [f.order_id for f in hist.fills] == ["0000012345", "0000067890"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"    # 연속조회 헤더


def test_base_date_fills_skips_blank_order_id_row():
    rows = [_fill("0000012345"), _fill("")]
    hist = _client(
        FakeTransport(response=_fills_resp(rows=rows)), environment="real"
    ).account.base_date_fills("20240220")
    assert [f.order_id for f in hist.fills] == ["0000012345"]


def test_base_date_fills_missing_output2_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": [], "ctx_area_nk200": "", "ctx_area_fk200": ""})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp), environment="real").account.base_date_fills("20240220")


def test_derivative_fill_entities_importable():
    from kis_trader import DerivativeFill, DerivativeFillHistory

    assert DerivativeFill is not None
    assert DerivativeFillHistory is not None


_COMMISSIONS_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-daily-amount-fee"

_COMMISSIONS_SUMMARY = {
    "fee_smtl": "45678", "agrm_amt_smtl": "410000000",
    "sll_fee": "20000", "buy_fee": "25678",
    "futr_fee_smtl": "30000", "opt_fee_smtl": "15678",
    "trad_pfls_smtl": "6789",
}


def _commission(ord_dt="20240216", *, pdno="KR4101RC0000", name="코스피200 F 202509",
                sll_agrm="200000000", sll_fee="20000", buy_agrm="210000000", buy_fee="25678",
                tot_fee="45678", trad="6789"):
    return {"ord_dt": ord_dt, "pdno": pdno, "item_name": name,
            "sll_agrm_amt": sll_agrm, "sll_fee": sll_fee,
            "buy_agrm_amt": buy_agrm, "buy_fee": buy_fee,
            "tot_fee_smtl": tot_fee, "trad_pfls": trad}


def _commissions_resp(*, rows=None, summary=None, ctx_nk="", ctx_fk="", tr_cont="D"):
    body = {"output1": rows if rows is not None else [],
            "output2": summary if summary is not None else dict(_COMMISSIONS_SUMMARY),
            "ctx_area_nk200": ctx_nk, "ctx_area_fk200": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_commissions_parses_and_routes():
    from datetime import date as _date

    from kis_trader.domestic.entities.derivative_account import DerivativeCommissionHistory

    fake = FakeTransport(response=_commissions_resp(rows=[_commission()]))
    hist = _client(fake, environment="real").account.commissions("20240201", "20240229")
    assert isinstance(hist, DerivativeCommissionHistory)
    assert hist.commissions[0].order_date == _date(2024, 2, 16)  # ord_dt
    assert hist.commissions[0].symbol == "KR4101RC0000"          # pdno
    assert hist.commissions[0].sell_fee == Decimal(20000)        # sll_fee
    assert hist.commissions[0].total_fee == Decimal(45678)       # tot_fee_smtl
    assert hist.total_fee == Decimal(45678)                      # fee_smtl
    assert hist.futures_fee == Decimal(30000)             # futr_fee_smtl
    assert hist.total_realized_pnl == Decimal(6789)       # trad_pfls_smtl
    call = fake.calls[0]
    assert call["tr_id"] == "CTFO6119R"
    assert call["path"].endswith("inquire-daily-amount-fee")
    assert call["params"]["INQR_STRT_DAY"] == "20240201"
    assert call["params"]["INQR_END_DAY"] == "20240229"
    assert call["params"]["CTX_AREA_FK200"] == ""
    assert call["params"]["CTX_AREA_NK200"] == ""
    assert call["params"]["CANO"] == "12345678"


def test_commissions_paper_fails_closed():
    fake = FakeTransport(response=_commissions_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.commissions("20240201", "20240229")
    assert fake.calls == []  # 가드는 와이어 이전 -- 호출 없음


def test_commissions_bad_start_date_fails_closed():
    fake = FakeTransport(response=_commissions_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="real").account.commissions("2024-02-01", "20240229")
    assert fake.calls == []


def test_commissions_bad_end_date_fails_closed():
    fake = FakeTransport(response=_commissions_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="real").account.commissions("20240201", "2024/02/29")
    assert fake.calls == []


def test_commissions_paginates():
    page1 = _commissions_resp(rows=[_commission("20240216")],
                              ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    page2 = _commissions_resp(rows=[_commission("20240217")], tr_cont="D")
    fake = FakeTransport(by_path={_COMMISSIONS_PATH: [page1, page2]})
    hist = _client(fake, environment="real").account.commissions("20240201", "20240229")
    assert len(hist.commissions) == 2
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"
    assert fake.calls[1]["params"]["CTX_AREA_FK200"] == "FK"
    assert fake.calls[1]["tr_cont"] == "N"    # 연속조회 헤더


def test_commissions_skips_blank_row():
    rows = [_commission("20240216"), _commission("", pdno="")]
    hist = _client(
        FakeTransport(response=_commissions_resp(rows=rows)), environment="real"
    ).account.commissions("20240201", "20240229")
    assert len(hist.commissions) == 1


def test_commissions_missing_output2_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": [], "ctx_area_nk200": "", "ctx_area_fk200": ""})
    with pytest.raises(KISError):
        _client(
            FakeTransport(response=resp), environment="real"
        ).account.commissions("20240201", "20240229")


def test_derivative_commission_entities_importable():
    from kis_trader import DerivativeCommission, DerivativeCommissionHistory

    assert DerivativeCommission is not None
    assert DerivativeCommissionHistory is not None


# --- 비매핑 행 fail-closed (output1=[None]) ---------------------------------
def test_derivative_balance_non_mapping_row_raises():
    with pytest.raises(KISError):
        _client(FakeTransport(response=_balance_resp(rows=[None]))).account.balance()


def test_valuation_pl_non_mapping_row_raises():
    with pytest.raises(KISError):
        _client(
            FakeTransport(response=_valuation_resp(rows=[None])), environment="real"
        ).account.valuation_pl()


def test_settlement_pl_non_mapping_row_raises():
    with pytest.raises(KISError):
        _client(
            FakeTransport(response=_settlement_resp(rows=[None])), environment="real"
        ).account.settlement_pl("20240216")


def test_base_date_fills_non_mapping_row_raises():
    with pytest.raises(KISError):
        _client(
            FakeTransport(response=_fills_resp(rows=[None])), environment="real"
        ).account.base_date_fills("20240220")


def test_commissions_non_mapping_row_raises():
    with pytest.raises(KISError):
        _client(
            FakeTransport(response=_commissions_resp(rows=[None])), environment="real"
        ).account.commissions("20240201", "20240229")


# --- 페이지 상한 fail-closed (연속조회가 끝나지 않는 tr_cont="F"/"M") --------
def test_valuation_pl_page_cap_fails_closed():
    never_ends = _valuation_resp(rows=[_valuation_position()], ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    with pytest.raises(KISError):
        _client(FakeTransport(response=never_ends), environment="real").account.valuation_pl()


def test_settlement_pl_page_cap_fails_closed():
    never_ends = _settlement_resp(rows=[_settlement_position()], ctx_nk="NEXT", ctx_fk="FK", tr_cont="M")
    with pytest.raises(KISError):
        _client(FakeTransport(response=never_ends), environment="real").account.settlement_pl("20240216")


def test_base_date_fills_page_cap_fails_closed():
    never_ends = _fills_resp(rows=[_fill()], ctx_nk="NEXT", ctx_fk="FK", tr_cont="F")
    with pytest.raises(KISError):
        _client(FakeTransport(response=never_ends), environment="real").account.base_date_fills("20240220")


def test_commissions_page_cap_fails_closed():
    never_ends = _commissions_resp(rows=[_commission()], ctx_nk="NEXT", ctx_fk="FK", tr_cont="M")
    with pytest.raises(KISError):
        _client(FakeTransport(response=never_ends), environment="real").account.commissions("20240201", "20240229")
