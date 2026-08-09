"""시장 전체 분석 -- kis.domestic.market.investor_flows()."""
from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_openapi import (
    BrokerOpinion,
    CreditEligibleStock,
    InterestRateQuote,
    InvestorNetBuyStock,
    KISClient,
    LendableStock,
    MarketFunds,
    MarketInvestorFlow,
    MarketInvestorSnapshot,
    ProgramInvestorTrade,
)
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _client(t):
    return KISClient(app_key="k", app_secret="s", transport=t)


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": rows})


def _flow_row(**over):
    row = {"stck_bsop_date": "20240510", "bstp_nmix_prpr": "2700.50",
           "bstp_nmix_prdy_vrss": "15.0", "prdy_vrss_sign": "2", "bstp_nmix_prdy_ctrt": "0.56",
           "frgn_ntby_qty": "1200000", "prsn_ntby_qty": "-500000", "orgn_ntby_qty": "-700000",
           "scrt_ntby_qty": "100"}
    row.update(over)
    return row


def test_market_investor_flows_maps_signed_and_anchor_params():
    fake = FakeTransport(response=_resp([_flow_row()]))
    flows = _client(fake).domestic.market.investor_flows(market="KOSPI", as_of="20240510")
    assert isinstance(flows[0], MarketInvestorFlow)
    f = flows[0]
    assert f.market == "KOSPI"
    assert f.index_value == Decimal("2700.50")
    assert f.index_change == Decimal("15.0")             # sign 2 -> 상승
    assert f.foreign_net == 1200000
    assert f.individual_net == -500000                   # pre-signed
    assert f.institutional_net == -700000
    assert f._raw["scrt_ntby_qty"] == "100"              # 세부 주체는 _raw
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market"
    assert call["tr_id"] == "FHPTJ04040000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "U"
    assert call["params"]["FID_INPUT_ISCD"] == "0001"    # KOSPI
    assert call["params"]["FID_INPUT_ISCD_1"] == "KSP"
    # 앵커 엔드포인트: DATE_1 == DATE_2 == as_of (원장: DATE_2 는 DATE_1 과 동일날짜).
    assert call["params"]["FID_INPUT_DATE_1"] == "20240510"
    assert call["params"]["FID_INPUT_DATE_2"] == "20240510"


def test_market_investor_flows_index_down_sign():
    fake = FakeTransport(response=_resp([_flow_row(prdy_vrss_sign="5", bstp_nmix_prdy_ctrt="0.56")]))
    f = _client(fake).domestic.market.investor_flows(as_of="20240510")[0]
    assert f.index_change == Decimal("-15.0")            # sign 5 -> 하락
    assert f.index_change_percent == Decimal("-0.56")


def test_market_investor_flows_kosdaq_code():
    fake = FakeTransport(response=_resp([]))
    _client(fake).domestic.market.investor_flows(market="KOSDAQ", as_of="20240131")
    assert fake.calls[0]["params"]["FID_INPUT_ISCD"] == "1001"
    assert fake.calls[0]["params"]["FID_INPUT_ISCD_1"] == "KSQ"


def test_market_investor_flows_rejects_bad_market():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.investor_flows(market="NYSE")


def test_market_investor_flows_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.market.investor_flows()


_SNAPSHOT_PREFIXES = (
    "frgn", "prsn", "orgn", "scrt", "ivtr", "pe_fund", "bank", "insu", "mrbn",
    "fund", "etc_orgt", "etc_corp",
)


def _snapshot_row():
    row = {}
    for prefix in _SNAPSHOT_PREFIXES:
        net_key = "ntby_vol" if prefix in {"pe_fund", "etc_orgt", "etc_corp"} else "ntby_qty"
        row |= {f"{prefix}_shnu_vol": "120", f"{prefix}_seln_vol": "100",
                f"{prefix}_{net_key}": "20", f"{prefix}_shnu_tr_pbmn": "12",
                f"{prefix}_seln_tr_pbmn": "10", f"{prefix}_ntby_tr_pbmn": "2"}
    return row


def test_market_investor_snapshot_maps_participants_and_params():
    fake = FakeTransport(response=_resp(_snapshot_row()))
    snapshot = _client(fake).domestic.market.investor_snapshot(
        market_code="KSP", industry_code="0001"
    )
    assert isinstance(snapshot, MarketInvestorSnapshot)
    assert snapshot.market_code == "KSP"
    assert snapshot.industry_code == "0001"
    assert snapshot.participants["foreign"].buy_volume == 120
    assert snapshot.participants["private_equity"].net_buy_volume == 20
    assert snapshot.participants["other_corporation"].net_buy_value == Decimal(2)
    assert fake.calls[0] == {
        "path": "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market",
        "tr_id": "FHPTJ04030000",
        "params": {"FID_INPUT_ISCD": "KSP", "FID_INPUT_ISCD_2": "0001"},
    }


@pytest.mark.parametrize("market_code,industry_code", [("", "0001"), ("KSP", "")])
def test_market_investor_snapshot_rejects_blank_codes(market_code, industry_code):
    fake = FakeTransport(response=_resp(_snapshot_row()))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.investor_snapshot(
            market_code=market_code, industry_code=industry_code
        )


def test_market_investor_snapshot_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.market.investor_snapshot(market_code="KSP", industry_code="0001")


def _net_buy_row():
    row = {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "ntby_qty": "100",
           "stck_prpr": "71500", "prdy_vrss_sign": "2", "prdy_vrss": "500",
           "prdy_ctrt": "0.70", "acml_vol": "1000000"}
    for prefix in ("frgn", "orgn", "ivtr", "bank", "insu", "mrbn", "fund",
                   "etc_orgt", "etc_corp"):
        quantity_key = "ntby_vol" if prefix.startswith("etc_") else "ntby_qty"
        row[f"{prefix}_{quantity_key}"] = "10"
        row[f"{prefix}_ntby_tr_pbmn"] = "1"
    return row


def test_investor_net_buy_stocks_maps_and_routes():
    fake = FakeTransport(response=_resp([_net_buy_row()]))
    stocks = _client(fake).domestic.market.investor_net_buy_stocks(
        market="KOSPI", basis="amount", direction="sell", investor="foreign"
    )
    assert isinstance(stocks[0], InvestorNetBuyStock)
    assert stocks[0].participants["foreign"].quantity == 10
    assert fake.calls[0]["tr_id"] == "FHPTJ04400000"
    assert fake.calls[0]["params"] == {
        "FID_COND_MRKT_DIV_CODE": "V", "FID_COND_SCR_DIV_CODE": "16449",
        "FID_INPUT_ISCD": "0001", "FID_DIV_CLS_CODE": "1",
        "FID_RANK_SORT_CLS_CODE": "1", "FID_ETC_CLS_CODE": "1",
    }


def _program_investor_row():
    row = {"invr_cls_code": "01", "invr_cls_name": "외국인"}
    for prefix in ("all", "arbt", "nabt"):
        row |= {f"{prefix}_seln_qty": "100", f"{prefix}_shnu_qty": "130",
                f"{prefix}_ntby_qty": "30", f"{prefix}_seln_amt": "10",
                f"{prefix}_shnu_amt": "13", f"{prefix}_ntby_amt": "3"}
    return row


def test_program_investor_trades_maps_and_routes():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                           body={"output1": [_program_investor_row()]})
    fake = FakeTransport(response=response)
    rows = _client(fake).domestic.market.program_investor_trades(market="KOSDAQ")
    assert isinstance(rows[0], ProgramInvestorTrade)
    assert rows[0].total.net_buy_quantity == 30
    assert rows[0].arbitrage.buy_amount == Decimal(13)
    assert fake.calls[0]["tr_id"] == "HHPPG046600C1"
    assert fake.calls[0]["params"] == {"MRKT_DIV_CLS_CODE": "4"}


def test_market_investor_flows_bad_value_fails_closed():
    fake = FakeTransport(response=_resp([_flow_row(frgn_ntby_qty="n/a")]))
    with pytest.raises(KISError):
        _client(fake).domestic.market.investor_flows(as_of="20240510")


def _funds_row(**over):
    row = {
        "bsop_date": "20240430", "bstp_nmix_prpr": "2692.06",
        "bstp_nmix_prdy_vrss": "12.34", "prdy_vrss_sign": "2", "prdy_ctrt": "0.46",
        "hts_avls": "2193843858", "cust_dpmn_amt": "572306",
        "cust_dpmn_amt_prdy_vrss": "1234", "amt_tnrt": "1.25", "uncl_amt": "9289",
        "crdt_loan_rmnd": "191730", "futs_tfam_amt": "45000", "sttp_amt": "1112330",
        "mxtp_amt": "220000", "bntp_amt": "330000", "mmf_amt": "1971372",
        "secu_lend_amt": "44000",
    }
    row.update(over)
    return row


def test_market_funds_maps_ledger_fields_signed_values_and_order():
    older = _funds_row(bsop_date="20240429", bstp_nmix_prpr="2680.00", mmf_amt="")
    fake = FakeTransport(response=_resp([_funds_row(), older]))
    funds = _client(fake).domestic.market.funds(as_of="20240430")
    assert isinstance(funds[0], MarketFunds)
    assert [item.date for item in funds] == [date(2024, 4, 30), date(2024, 4, 29)]
    first = funds[0]
    assert first.index_value == Decimal("2692.06")
    assert first.index_change == Decimal("12.34")          # sign 2 -> 상승
    assert first.index_change_percent == Decimal("0.46")
    assert first.market_cap == Decimal(2193843858)
    assert first.customer_deposits == Decimal(572306)
    assert first.receivables == Decimal(9289)
    assert first.credit_loan_balance == Decimal(191730)
    assert first.equity_fund == Decimal(1112330)
    assert first.mmf == Decimal(1971372)
    assert funds[1].mmf is None
    assert fake.calls[0] == {
        "path": "/uapi/domestic-stock/v1/quotations/mktfunds",
        "tr_id": "FHKST649100C0",
        "params": {"FID_INPUT_DATE_1": "20240430"},
    }


def test_market_funds_default_anchor_and_down_sign():
    fake = FakeTransport(response=_resp([_funds_row(prdy_vrss_sign="5")]))
    fund = _client(fake).domestic.market.funds()[0]
    assert fund.index_change == Decimal("-12.34")
    assert fund.index_change_percent == Decimal("-0.46")
    anchor = fake.calls[0]["params"]["FID_INPUT_DATE_1"]
    assert len(anchor) == 8 and anchor.isdigit()


def test_market_funds_non_list_output_fails_closed():
    fake = FakeTransport(response=_resp({}))
    with pytest.raises(KISError):
        _client(fake).domestic.market.funds()


def test_market_funds_bad_required_numeric_fails_closed():
    fake = FakeTransport(response=_resp([_funds_row(bstp_nmix_prpr="n/a")]))
    with pytest.raises(KISError):
        _client(fake).domestic.market.funds(as_of="20240430")


def _interest_row(**over):
    row = {
        "bcdt_code": "Y0202", "hts_kor_isnm": "미국 10년 국채수익률",
        "bond_mnrt_prpr": "4.5600", "prdy_vrss_sign": "2",
        "bond_mnrt_prdy_vrss": "0.0100", "prdy_ctrt": "0.22",
        "bstp_nmix_prdy_ctrt": "0.22", "stck_bsop_date": "20240411",
    }
    row.update(over)
    return row


def test_market_interest_rates_combines_regions_and_maps_signs():
    overseas = _interest_row()
    domestic = _interest_row(
        bcdt_code="Y0101", hts_kor_isnm="국고채 3년", bond_mnrt_prpr="3.4080",
        prdy_vrss_sign="5", bond_mnrt_prdy_vrss="-0.0580",
        bstp_nmix_prdy_ctrt="-1.67", stck_bsop_date="20240412",
    )
    response = RawResponse(
        rt_cd="0", msg_cd="MCA00000", msg1="정상",
        body={"output1": [overseas], "output2": [domestic]},
    )
    fake = FakeTransport(response=response)
    quotes = _client(fake).domestic.market.interest_rates()
    assert all(isinstance(quote, InterestRateQuote) for quote in quotes)
    assert [(quote.code, quote.region) for quote in quotes] == [
        ("Y0202", "overseas"), ("Y0101", "domestic"),
    ]
    assert quotes[0].value == Decimal("4.5600")
    assert quotes[0].change == Decimal("0.0100")
    assert quotes[0].change_percent == Decimal("0.22")
    assert quotes[1].change == Decimal("-0.0580")
    assert quotes[1].change_percent == Decimal("-1.67")
    assert quotes[1].date == date(2024, 4, 12)
    assert fake.calls[0] == {
        "path": "/uapi/domestic-stock/v1/quotations/comp-interest",
        "tr_id": "FHPST07020000",
        "params": {
            "FID_COND_MRKT_DIV_CODE": "I", "FID_COND_SCR_DIV_CODE": "20702",
            "FID_DIV_CLS_CODE": "1", "FID_DIV_CLS_CODE1": "",
        },
    }


@pytest.mark.parametrize("body", [{"output1": []}, {"output2": []}])
def test_market_interest_rates_requires_both_output_blocks(body):
    fake = FakeTransport(
        response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body=body)
    )
    with pytest.raises(KISError):
        _client(fake).domestic.market.interest_rates()


def test_market_interest_rates_bad_row_fails_closed():
    response = RawResponse(
        rt_cd="0", msg_cd="X", msg1="ok", body={"output1": ["bad"], "output2": []}
    )
    fake = FakeTransport(response=response)
    with pytest.raises(KISError):
        _client(fake).domestic.market.interest_rates()


def _lendable_row(**over):
    row = {
        "pdno": "005930", "prdt_name": "삼성전자", "papr": "100",
        "bfdy_clpr": "75000", "sbst_prvs": "60000", "tr_stop_dvsn_name": "정상",
        "psbl_yn_name": "가능", "lmt_qty1": "100000", "use_qty1": "20000",
        "trad_psbl_qty2": "80000", "rght_type_cd": "00", "bass_dt": "20240528",
        "psbl_yn": "Y",
    }
    row.update(over)
    return row


def test_market_lendable_stocks_maps_and_routes():
    response = RawResponse(
        rt_cd="0", msg_cd="X", msg1="ok",
        body={"output1": [_lendable_row()], "output2": {}},
    )
    fake = FakeTransport(response=response)
    stocks = _client(fake).domestic.market.lendable_stocks(market="KOSPI", symbol="005930")
    assert len(stocks) == 1 and isinstance(stocks[0], LendableStock)
    stock = stocks[0]
    assert stock.symbol == "005930"
    assert stock.available_quantity == 80000
    assert stock.limit_quantity - stock.used_quantity == stock.available_quantity
    assert stock.is_lendable is True
    assert stock.base_date == date(2024, 5, 28)
    assert fake.calls[0] == {
        "path": "/uapi/domestic-stock/v1/quotations/lendable-by-company",
        "tr_id": "CTSC2702R",
        "params": {
            "EXCG_DVSN_CD": "02", "PDNO": "005930", "THCO_STLN_PSBL_YN": "Y",
            "INQR_DVSN_1": "0", "CTX_AREA_FK200": "", "CTX_AREA_NK100": "",
        },
    }


def test_market_lendable_stocks_rejects_bad_market_and_output():
    fake = FakeTransport(response=None)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.lendable_stocks(market="NYSE")
    assert fake.calls == []
    bad = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(bad).domestic.market.lendable_stocks()


def test_market_credit_eligible_stocks_maps_query_contract():
    response = RawResponse(
        rt_cd="0", msg_cd="X", msg1="ok", body={"output": [
            {"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "crdt_rate": "40.00"}
        ]},
    )
    fake = FakeTransport(response=response)
    stocks = _client(fake).domestic.market.credit_eligible_stocks(
        market="KOSDAQ", eligible=False, sort="symbol"
    )
    assert len(stocks) == 1 and isinstance(stocks[0], CreditEligibleStock)
    assert stocks[0].credit_rate == Decimal("40.00")
    assert stocks[0].is_eligible is False
    assert fake.calls[0] == {
        "path": "/uapi/domestic-stock/v1/quotations/credit-by-company",
        "tr_id": "FHPST04770000",
        "params": {
            "fid_rank_sort_cls_code": "0", "fid_slct_yn": "1",
            "fid_input_iscd": "1001", "fid_cond_scr_div_code": "20477",
            "fid_cond_mrkt_div_code": "J",
        },
    }


def test_market_credit_eligible_stocks_rejects_bad_filters_and_rows():
    fake = FakeTransport(response=None)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.credit_eligible_stocks(market="NYSE")
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.credit_eligible_stocks(sort="rate")
    assert fake.calls == []
    bad = FakeTransport(
        response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": ["bad"]})
    )
    with pytest.raises(KISError):
        _client(bad).domestic.market.credit_eligible_stocks()


def _broker_opinion_row(**over):
    row = {
        "stck_bsop_date": "20240527", "stck_shrn_iscd": "454910",
        "hts_kor_isnm": "두산로보틱스", "invt_opnn": "매수",
        "invt_opnn_cls_code": "1", "rgbf_invt_opnn": "중립",
        "rgbf_invt_opnn_cls_code": "2", "mbcr_name": "테스트증권",
        "stck_prpr": "74300", "prdy_vrss": "500", "prdy_vrss_sign": "2",
        "prdy_ctrt": "0.68", "hts_goal_prc": "90000", "stck_prdy_clpr": "73800",
        "dprt": "21.13",
    }
    row.update(over)
    return row


def test_market_broker_opinions_maps_and_routes():
    fake = FakeTransport(response=_resp([_broker_opinion_row()]))
    opinions = _client(fake).domestic.market.broker_opinions(
        broker="999", opinion="buy", start="20240501", end="20240528"
    )
    assert len(opinions) == 1 and isinstance(opinions[0], BrokerOpinion)
    item = opinions[0]
    assert item.symbol == "454910"
    assert item.broker == "테스트증권"
    assert item.opinion == "매수"
    assert item.target_price == Decimal(90000)
    assert item.change == Decimal(500)
    assert fake.calls[0] == {
        "path": "/uapi/domestic-stock/v1/quotations/invest-opbysec",
        "tr_id": "FHKST663400C0",
        "params": {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16634",
            "FID_INPUT_ISCD": "999", "FID_DIV_CLS_CODE": "1",
            "FID_INPUT_DATE_1": "20240501", "FID_INPUT_DATE_2": "20240528",
        },
    }


def test_market_broker_opinions_restores_down_sign_and_defaults_window():
    fake = FakeTransport(response=_resp([_broker_opinion_row(
        prdy_vrss_sign="5", prdy_vrss="500", prdy_ctrt="0.68"
    )]))
    item = _client(fake).domestic.market.broker_opinions(broker="999", end="20240131")[0]
    assert item.change == Decimal(-500)
    assert item.change_percent == Decimal("-0.68")
    assert fake.calls[0]["params"]["FID_INPUT_DATE_1"] == "20240101"


def test_market_broker_opinions_rejects_invalid_inputs_before_transport():
    fake = FakeTransport(response=None)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.broker_opinions(broker="")
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.broker_opinions(broker="999", opinion="strong_buy")
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.broker_opinions(
            broker="999", start="20240201", end="20240101"
        )
    assert fake.calls == []


def test_market_broker_opinions_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.market.broker_opinions(broker="999")


def _prog_row(**over):
    # 값 필드는 모두 smtn. smtm 은 _rate(비율) 필드에만 붙는 오탈자다(원장 확인).
    row = {"stck_bsop_date": "20240510", "arbt_smtn_ntby_qty": "12000",
           "arbt_smtn_ntby_tr_pbmn": "84000000", "nabt_smtn_ntby_qty": "-5000",
           "nabt_smtn_ntby_tr_pbmn": "-35000000", "arbt_smtm_ntby_qty_rate": "0.4"}
    row.update(over)
    return row


def test_program_trade_summary_maps_smtn_fields():
    from kis_openapi import ProgramTradeSummary
    fake = FakeTransport(response=_resp([_prog_row()]))
    s = _client(fake).domestic.market.program_trades(market="KOSPI", start="20240101", end="20240513")[0]
    assert isinstance(s, ProgramTradeSummary)
    assert s.arbitrage_net_volume == 12000               # arbt_smtn_ntby_qty (NOT the _rate field)
    assert s.arbitrage_net_amount == Decimal(84000000)
    assert s.nonarb_net_volume == -5000                  # pre-signed 순매도
    assert s.total_net_volume == 12000 - 5000            # 차익 + 비차익
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily"
    assert call["tr_id"] == "FHPPG04600001"
    assert call["params"]["FID_MRKT_CLS_CODE"] == "K"


def test_program_trade_summary_kosdaq_and_bad_market():
    fake = FakeTransport(response=_resp([]))
    _client(fake).domestic.market.program_trades(market="KOSDAQ", start="20240101", end="20240131")
    assert fake.calls[0]["params"]["FID_MRKT_CLS_CODE"] == "Q"
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.program_trades(market="US")


def test_program_trade_summary_bad_value_fails_closed():
    fake = FakeTransport(response=_resp([_prog_row(arbt_smtn_ntby_qty="n/a")]))
    with pytest.raises(KISError):
        _client(fake).domestic.market.program_trades()


def test_program_trade_summary_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.market.program_trades()
