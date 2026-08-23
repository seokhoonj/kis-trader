"""해외 잔고/손익 리포트 -- overseas_present_balance (CTRP6504R) / overseas_settlement_balance
(CTRP6010R) / overseas_period_profit (TTTS3039R).

체결기준(present)은 output1 만 원장 예시로 확증, 결제기준(settlement)은 3블록 전부 확증, 기간손익
(period_profit)은 예시가 비어 레이아웃 기준. 값 확증 여부와 무관하게 매핑·TR·파라미터·fail-closed 검증.
FakeTransport 로 네트워크 없이.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_trader import (
    KISClient,
    OverseasPeriodProfit,
    OverseasPresentBalance,
    OverseasSettlementBalance,
)
from kis_trader.errors import KISError, KISUsageError
from kis_trader.money import Money
from kis_trader.transport import RawResponse

_PRESENT = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
_SETTLE = "/uapi/overseas-stock/v1/trading/inquire-paymt-stdr-balance"
_PROFIT = "/uapi/overseas-stock/v1/trading/inquire-period-profit"

_POS = {
    "pdno": "AAPL", "prdt_name": "애플", "cblc_qty13": "10", "ord_psbl_qty1": "10",
    "avg_unpr3": "150.00", "ovrs_now_pric1": "155.00", "frcr_pchs_amt": "1500.00",
    "frcr_evlu_amt2": "1550.00", "evlu_pfls_amt2": "50.00", "evlu_pfls_rt1": "3.33",
    "loan_rmnd": "0", "mgge_qty": "2", "ovrs_excg_cd": "NASD", "tr_mket_name": "나스닥",
    "natn_kor_name": "미국", "buy_crcy_cd": "USD", "bass_exrt": "1350.5",
}
_CRCY = {"crcy_cd": "USD", "crcy_cd_name": "미국달러", "frcr_dncl_amt_2": "200.00",
         "frst_bltn_exrt": "1350.5"}
_SUM3 = {
    "pchs_amt_smtl_amt": "2000000", "evlu_amt_smtl_amt": "2100000",
    "tot_evlu_pfls_amt": "100000", "tot_asst_amt": "5000000", "tot_asst_amt2": "5000000",
    "evlu_erng_rt1": "5.0", "tot_dncl_amt": "300000", "wcrc_evlu_amt_smtl": "2100000",
    "tot_loan_amt": "0",
}
_PROFIT_ROW = {
    "trad_day": "20250523", "ovrs_pdno": "AAPL", "ovrs_item_name": "애플",
    "slcl_qty": "5", "pchs_avg_pric": "150.00", "frcr_pchs_amt1": "750.00",
    "avg_sll_unpr": "160.00", "frcr_sll_amt_smtl1": "800.00", "stck_sll_tlex": "1.00",
    "ovrs_rlzt_pfls_amt": "49.00", "pftrt": "6.53", "exrt": "1350.5",
    "ovrs_excg_cd": "NASD", "frst_bltn_exrt": "1349.0",
}
_PROFIT_SUM = {
    "stck_sll_amt_smtl": "800.00", "stck_buy_amt_smtl": "750.00", "smtl_fee1": "1.00",
    "excc_dfrm_amt": "799.00", "ovrs_rlzt_pfls_tot_amt": "49.00", "tot_pftrt": "6.53",
    "bass_dt": "20250523", "exrt": "1350.5",
}


class FakeTransport:
    def __init__(self, *, response=None):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent})
        assert self.response is not None
        return self.response


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def _resp(body):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="")


# --- 체결기준현재잔고 (CTRP6504R) -----------------------------------------
def test_present_balance_parses():
    body = {"output1": [_POS], "output2": [_CRCY], "output3": _SUM3}
    bal = _client(FakeTransport(response=_resp(body))).account.overseas.present_balance()
    assert isinstance(bal, OverseasPresentBalance)
    p = bal.positions[0]
    assert p.symbol == "AAPL"
    assert p.balance_quantity == Decimal(10)
    assert p.market_value == Money(Decimal("1550.00"), "USD")
    assert p.current_price == Money(Decimal("155.00"), "USD")
    assert p.currency == "USD"
    assert bal.currencies[0].deposit == Money(Decimal("200.00"), "USD")
    assert bal.total_unrealized_pnl == Decimal(100000)
    assert bal.total_asset_amount == Decimal(5000000)


def test_present_balance_rejects_unknown_nation_before_wire():
    # 미지원 nation 은 조용히 전체(000)로 넓히지 않고 와이어 접촉 전에 거부한다(fail-closed).
    fake = FakeTransport(response=_resp({"output1": [], "output2": [], "output3": _SUM3}))
    with pytest.raises(KISUsageError):
        _client(fake).account.overseas.present_balance(nation="usa")
    assert fake.calls == []


def test_present_balance_tr_env_and_params():
    fake = FakeTransport(response=_resp({"output1": [_POS], "output2": [_CRCY], "output3": _SUM3}))
    _client(fake).account.overseas.present_balance(won_basis=False, nation="US")
    call = fake.calls[0]
    assert call["tr_id"] == "CTRP6504R"
    assert call["path"] == _PRESENT
    assert call["params"]["WCRC_FRCR_DVSN_CD"] == "02"   # 외화기준
    assert call["params"]["NATN_CD"] == "840"            # 미국


def test_present_balance_demo_uses_demo_tr():
    fake = FakeTransport(response=_resp({"output3": _SUM3}))
    bal = _client(fake, environment="paper").account.overseas.present_balance()
    assert fake.calls[0]["tr_id"] == "VTRP6504R"
    assert bal.positions == ()          # 모의는 요약만
    assert bal.total_asset_amount == Decimal(5000000)


def test_present_balance_summary_as_single_list():
    body = {"output1": [_POS], "output2": [_CRCY], "output3": [_SUM3]}
    bal = _client(FakeTransport(response=_resp(body))).account.overseas.present_balance()
    assert bal.total_purchase_amount == Decimal(2000000)


# --- 결제기준잔고 (CTRP6010R) ---------------------------------------------
def test_settlement_balance_parses():
    body = {"output1": [_POS], "output2": [_CRCY], "output3": _SUM3}
    bal = _client(FakeTransport(response=_resp(body))).account.overseas.settlement_balance(basis_date="20250523")
    assert isinstance(bal, OverseasSettlementBalance)
    p = bal.positions[0]
    assert p.collateral_quantity == Decimal(2)
    assert p.loan_balance == Money(Decimal(0), "USD")
    assert bal.total_deposit == Decimal(300000)
    assert bal.total_asset_amount == Decimal(5000000)      # tot_asst_amt2
    assert bal.total_won_evaluation == Decimal(2100000)


def test_settlement_balance_tr_and_params():
    fake = FakeTransport(response=_resp({"output1": [_POS], "output2": [_CRCY], "output3": _SUM3}))
    _client(fake).account.overseas.settlement_balance(basis_date="20250523", won_basis=False)
    call = fake.calls[0]
    assert call["tr_id"] == "CTRP6010R"
    assert call["path"] == _SETTLE
    assert call["params"]["BASS_DT"] == "20250523"
    assert call["params"]["WCRC_FRCR_DVSN_CD"] == "02"


def test_settlement_balance_demo_rejected():
    fake = FakeTransport(response=_resp({"output3": _SUM3}))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.overseas.settlement_balance(basis_date="20250523")
    assert fake.calls == []


# --- 기간손익 (TTTS3039R) --------------------------------------------------
def test_period_profit_parses():
    body = {"output1": [_PROFIT_ROW], "output2": _PROFIT_SUM}
    pnl = _client(FakeTransport(response=_resp(body))).account.overseas.period_profit(
        start="20250501", end="20250523")
    assert isinstance(pnl, OverseasPeriodProfit)
    row = pnl.rows[0]
    assert row.trade_day == date(2025, 5, 23)
    assert row.symbol == "AAPL"
    assert row.sold_quantity == Decimal(5)
    assert row.realized_pnl == Decimal("49.00")
    assert pnl.total_realized_pnl == Decimal("49.00")
    assert pnl.basis_date == date(2025, 5, 23)


def test_period_profit_tr_and_params():
    fake = FakeTransport(response=_resp({"output1": [_PROFIT_ROW], "output2": _PROFIT_SUM}))
    _client(fake).account.overseas.period_profit(
        start="20250501", end="20250523", exchange="NASD", currency="USD")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTS3039R"
    assert call["path"] == _PROFIT
    assert call["params"]["INQR_STRT_DT"] == "20250501"
    assert call["params"]["INQR_END_DT"] == "20250523"
    assert call["params"]["OVRS_EXCG_CD"] == "NASD"
    assert call["params"]["CRCY_CD"] == "USD"
    assert call["params"]["WCRC_FRCR_DVSN_CD"] == "01"   # 외화기준 default


def test_period_profit_empty_ok():
    body = {"output1": [], "output2": {}}
    pnl = _client(FakeTransport(response=_resp(body))).account.overseas.period_profit(
        start="20250501", end="20250523")
    assert pnl.rows == ()
    assert pnl.total_realized_pnl == Decimal(0)


def test_period_profit_non_list_output1_fails_closed():
    body = {"output1": {"ovrs_pdno": "x"}, "output2": _PROFIT_SUM}
    with pytest.raises(KISError):
        _client(FakeTransport(response=_resp(body))).account.overseas.period_profit(
            start="20250501", end="20250523")


def test_period_profit_demo_rejected():
    fake = FakeTransport(response=_resp({"output1": [_PROFIT_ROW], "output2": _PROFIT_SUM}))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.overseas.period_profit(start="20250501", end="20250523")
    assert fake.calls == []


def test_reports_require_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp({"output3": _SUM3})), account=None).account.overseas.present_balance()
