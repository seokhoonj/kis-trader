"""해외 잔고/손익 리포트 -- overseas_present_balance (CTRP6504R) / overseas_settlement_balance
(CTRP6010R) / overseas_trade_profits (TTTS3039R).

체결기준(present)은 output1 만 원장 예시로 확증, 결제기준(settlement)은 3블록 전부 확증, 기간손익
(trade_profits)은 예시가 비어 레이아웃 기준. 값 확증 여부와 무관하게 매핑·TR·파라미터·fail-closed 검증.
FakeTransport 로 네트워크 없이.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_trader import (
    KISClient,
    OverseasPresentBalance,
    OverseasSettlementBalance,
    OverseasTradeProfitHistory,
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
    def __init__(self, *, response=None, pages=None):
        self.response = response
        self.pages = pages          # tr_cont 연속조회 흉내(순차 응답)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent, "tr_cont": tr_cont})
        if self.pages is not None:
            return self.pages.pop(0)
        assert self.response is not None
        return self.response


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def _resp(body, *, tr_cont=""):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


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
    assert bal.evaluation_return_percent == Decimal("5.0")   # evlu_erng_rt1


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


@pytest.mark.parametrize("inquiry,code", [("all", "00"), ("general", "01"), ("ministock", "02")])
def test_present_balance_maps_inquiry_to_inqr_dvsn_cd(inquiry, code):
    # 조회구분(all/general/ministock) -> INQR_DVSN_CD(00/01/02). KIS 공식: 전체/일반해외주식/미니스탁.
    fake = FakeTransport(response=_resp({"output1": [], "output2": [], "output3": _SUM3}))
    _client(fake).account.overseas.present_balance(inquiry=inquiry)
    assert fake.calls[0]["params"]["INQR_DVSN_CD"] == code


def test_present_balance_rejects_unknown_inquiry_before_wire():
    # 미지원 inquiry 는 와이어 접촉 전에 거부한다(fail-closed) -- nation 과 동일 규율.
    fake = FakeTransport(response=_resp({"output1": [], "output2": [], "output3": _SUM3}))
    with pytest.raises(KISUsageError):
        _client(fake).account.overseas.present_balance(inquiry="00")  # raw 코드는 이제 거부
    assert fake.calls == []


def test_present_balance_demo_uses_demo_tr():
    fake = FakeTransport(response=_resp({"output3": _SUM3}))
    bal = _client(fake, environment="paper").account.overseas.present_balance()
    assert fake.calls[0]["tr_id"] == "VTRP6504R"
    assert bal.positions == ()          # 모의는 요약만
    assert bal.total_asset_amount == Decimal(5000000)


def test_present_balance_accumulates_across_pages():
    # tr_cont=M 이면 다음 페이지를 tr_cont=N 으로 받아 보유종목(output1)·통화별 예수금(output2)을
    # 모두 누적한다. 요약(output3)은 계좌 단위라 한 번만. 단발 조회로 끊으면 다중 페이지가 잘린다.
    page1 = _resp({"output1": [_POS], "output2": [_CRCY], "output3": _SUM3}, tr_cont="M")
    pos2 = dict(_POS, pdno="MSFT")
    crcy2 = dict(_CRCY, crcy_cd="HKD")
    page2 = _resp({"output1": [pos2], "output2": [crcy2], "output3": {}}, tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    bal = _client(fake).account.overseas.present_balance()
    assert [p.symbol for p in bal.positions] == ["AAPL", "MSFT"]
    assert [c.currency for c in bal.currencies] == ["USD", "HKD"]
    assert bal.total_asset_amount == Decimal(5000000)       # output3(첫 페이지) 유지
    assert fake.calls[1]["tr_cont"] == "N"                  # 연속조회 헤더
    assert len(fake.calls) == 2


def test_settlement_balance_accumulates_across_pages():
    page1 = _resp({"output1": [_POS], "output2": [_CRCY], "output3": _SUM3}, tr_cont="F")
    page2 = _resp({"output1": [dict(_POS, pdno="MSFT")], "output2": [], "output3": {}}, tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    bal = _client(fake).account.overseas.settlement_balance(basis_date="20250523")
    assert [p.symbol for p in bal.positions] == ["AAPL", "MSFT"]
    assert bal.total_deposit == Decimal(300000)
    assert fake.calls[1]["tr_cont"] == "N"
    assert len(fake.calls) == 2


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
    assert bal.evaluation_return_percent == Decimal("5.0")   # evlu_erng_rt1


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
def test_trade_profits_parses():
    body = {"output1": [_PROFIT_ROW], "output2": _PROFIT_SUM}
    pnl = _client(FakeTransport(response=_resp(body))).account.overseas.trade_profits(
        start="20250501", end="20250523")
    assert isinstance(pnl, OverseasTradeProfitHistory)
    row = pnl.trades[0]
    assert row.trade_day == date(2025, 5, 23)
    assert row.symbol == "AAPL"
    assert row.sold_quantity == Decimal(5)
    assert row.realized_pnl == Decimal("49.00")
    assert pnl.total_realized_pnl == Decimal("49.00")
    assert row.return_percent == Decimal("6.53")             # pftrt
    assert pnl.total_return_percent == Decimal("6.53")       # tot_pftrt
    assert pnl.base_date == date(2025, 5, 23)


def test_trade_profits_tr_and_params():
    fake = FakeTransport(response=_resp({"output1": [_PROFIT_ROW], "output2": _PROFIT_SUM}))
    _client(fake).account.overseas.trade_profits(
        start="20250501", end="20250523", exchange="NASD", currency="USD")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTS3039R"
    assert call["path"] == _PROFIT
    assert call["params"]["INQR_STRT_DT"] == "20250501"
    assert call["params"]["INQR_END_DT"] == "20250523"
    assert call["params"]["OVRS_EXCG_CD"] == "NASD"
    assert call["params"]["CRCY_CD"] == "USD"
    assert call["params"]["WCRC_FRCR_DVSN_CD"] == "01"   # 외화기준 default


def test_trade_profits_empty_ok():
    body = {"output1": [], "output2": {}}
    pnl = _client(FakeTransport(response=_resp(body))).account.overseas.trade_profits(
        start="20250501", end="20250523")
    assert pnl.trades == ()
    assert pnl.total_realized_pnl == Decimal(0)


def test_trade_profits_non_list_output1_fails_closed():
    body = {"output1": {"ovrs_pdno": "x"}, "output2": _PROFIT_SUM}
    with pytest.raises(KISError):
        _client(FakeTransport(response=_resp(body))).account.overseas.trade_profits(
            start="20250501", end="20250523")


def test_trade_profits_demo_rejected():
    fake = FakeTransport(response=_resp({"output1": [_PROFIT_ROW], "output2": _PROFIT_SUM}))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.overseas.trade_profits(start="20250501", end="20250523")
    assert fake.calls == []


def test_reports_require_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp({"output3": _SUM3})), account=None).account.overseas.present_balance()


_BALANCE_FIELDS = {
    "present": {
        "pchs_amt_smtl_amt": "total_purchase_amount", "evlu_amt_smtl_amt": "total_evaluation_amount",
        "tot_evlu_pfls_amt": "total_unrealized_pnl", "tot_asst_amt": "total_asset_amount",
        "evlu_erng_rt1": "evaluation_return_percent",
    },
    "settlement": {
        "pchs_amt_smtl_amt": "total_purchase_amount", "tot_evlu_pfls_amt": "total_unrealized_pnl",
        "evlu_erng_rt1": "evaluation_return_percent", "tot_dncl_amt": "total_deposit",
        "wcrc_evlu_amt_smtl": "total_won_evaluation", "tot_asst_amt2": "total_asset_amount",
        "tot_loan_amt": "total_loan_amount",
    },
}
_OMITTED = object()


def _balance_report(fake, endpoint):
    account = _client(fake).account.overseas
    if endpoint == "present":
        return account.present_balance()
    return account.settlement_balance(basis_date="20250523")


def _zero_summary(endpoint, value="0"):
    return dict.fromkeys(_BALANCE_FIELDS[endpoint], value)


@pytest.mark.parametrize("endpoint", ["present", "settlement"])
@pytest.mark.parametrize("summary", [_OMITTED, None, "", {}, [], [{}]])
@pytest.mark.parametrize("contents", ["position", "cash_only", "empty"])
def test_balance_absent_summary_is_unknown_even_without_positions(endpoint, summary, contents):
    body = {"output1": [_POS] if contents == "position" else [],
            "output2": [_CRCY] if contents == "cash_only" else []}
    if summary is not _OMITTED:
        body["output3"] = summary
    with pytest.raises(KISError, match="output3"):
        _balance_report(FakeTransport(response=_resp(body)), endpoint)


@pytest.mark.parametrize("endpoint, field", [(endpoint, field) for endpoint in _BALANCE_FIELDS
                                             for field in _BALANCE_FIELDS[endpoint]])
@pytest.mark.parametrize("bad_value", [_OMITTED, None, "", "   ", "invalid", "NaN", "Infinity", "-Infinity", True, {}, []])
def test_balance_each_required_total_must_be_known_finite(endpoint, field, bad_value):
    summary = _zero_summary(endpoint)
    if bad_value is _OMITTED:
        summary.pop(field)
    else:
        summary[field] = bad_value
    with pytest.raises(KISError):
        _balance_report(FakeTransport(response=_resp({"output3": summary})), endpoint)


@pytest.mark.parametrize("endpoint", ["present", "settlement"])
@pytest.mark.parametrize("value", [0, "0", "0.00"])
@pytest.mark.parametrize("shape", ["mapping", "list", "tuple"])
def test_balance_explicit_zero_summary_only_remains_valid(endpoint, value, shape):
    summary = _zero_summary(endpoint, value)
    block = summary if shape == "mapping" else [summary] if shape == "list" else (summary,)
    result = _balance_report(FakeTransport(response=_resp({"output3": block})), endpoint)
    for attr in _BALANCE_FIELDS[endpoint].values():
        assert getattr(result, attr) == Decimal(0)
    assert result.positions == result.currencies == ()
    assert dict(result._raw) == summary


@pytest.mark.parametrize("endpoint", ["present", "settlement"])
def test_balance_negative_finite_pnl_and_return_preserved(endpoint):
    summary = _zero_summary(endpoint)
    summary.update(tot_evlu_pfls_amt="-25.50", evlu_erng_rt1="-1.25")
    result = _balance_report(FakeTransport(response=_resp({"output3": summary})), endpoint)
    assert result.total_unrealized_pnl == Decimal("-25.50")
    assert result.evaluation_return_percent == Decimal("-1.25")


@pytest.mark.parametrize("endpoint", ["present", "settlement"])
@pytest.mark.parametrize("malformed", ["not-object", 7, False, [7], [_SUM3, _SUM3], [_SUM3, 7]])
@pytest.mark.parametrize("later", [False, True])
def test_balance_rejects_malformed_summary_on_any_page(endpoint, malformed, later):
    pages = []
    if later:
        pages.append(_resp({"output3": _zero_summary(endpoint)}, tr_cont="M"))
    pages.append(_resp({"output3": malformed}))
    fake = FakeTransport(pages=pages)
    with pytest.raises(KISError, match="output3"):
        _balance_report(fake, endpoint)
    assert len(fake.calls) == (2 if later else 1)


@pytest.mark.parametrize("endpoint", ["present", "settlement"])
def test_balance_summary_can_arrive_only_on_last_page(endpoint):
    pages = [_resp({"output1": [_POS]}, tr_cont="F"),
             _resp({"output2": [_CRCY], "output3": [{}]}, tr_cont="M"),
             _resp({"output3": _zero_summary(endpoint)})]
    fake = FakeTransport(pages=pages)
    result = _balance_report(fake, endpoint)
    assert len(fake.calls) == 3
    assert fake.calls[1]["tr_cont"] == fake.calls[2]["tr_cont"] == "N"
    assert len(result.positions) == len(result.currencies) == 1
    assert result.total_purchase_amount == 0


@pytest.mark.parametrize("endpoint", ["present", "settlement"])
@pytest.mark.parametrize("later", [_OMITTED, None, "", {}, [], [{}]])
def test_balance_later_absence_does_not_overwrite_first_summary(endpoint, later):
    body = {"output1": [_POS], "output2": [_CRCY]}
    if later is not _OMITTED:
        body["output3"] = later
    fake = FakeTransport(pages=[_resp({"output3": _zero_summary(endpoint)}, tr_cont="F"), _resp(body)])
    result = _balance_report(fake, endpoint)
    assert len(fake.calls) == 2
    assert result.total_purchase_amount == 0
    assert len(result.positions) == len(result.currencies) == 1


@pytest.mark.parametrize("endpoint", ["present", "settlement"])
def test_balance_partial_first_summary_not_laundered_by_complete_later_summary(endpoint):
    fake = FakeTransport(pages=[_resp({"output3": {"metadata": "nonempty"}}, tr_cont="M"),
                               _resp({"output3": _zero_summary(endpoint)})])
    with pytest.raises(KISError):
        _balance_report(fake, endpoint)
    assert len(fake.calls) == 2


@pytest.mark.parametrize("endpoint", ["present", "settlement"])
def test_balance_later_page_failure_never_returns_partial_success(endpoint):
    failure = RawResponse(rt_cd="1", msg_cd="failure", msg1="synthetic failure", body={})
    fake = FakeTransport(pages=[_resp({"output3": _zero_summary(endpoint)}, tr_cont="F"), failure])
    with pytest.raises(KISError):
        _balance_report(fake, endpoint)
    assert len(fake.calls) == 2
