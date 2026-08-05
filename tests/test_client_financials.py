"""재무제표 -- kis.ticker(code).balance_sheet() / .income_statement().

대차대조표(FHKST66430100)·손익계산서(FHKST66430200)의 TR·URL·분류(년/분기)·필드 매핑·결산기
순서·fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import (
    BalanceSheet,
    FinancialRatio,
    GrowthRatio,
    IncomeStatement,
    KISClient,
    OtherRatio,
)
from kis_openapi.errors import KISError
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


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def test_balance_sheet_maps_and_annual_default():
    rows = [{"stac_yymm": "202312", "cras": "1000", "fxas": "2000", "total_aset": "3000",
             "flow_lblt": "500", "fix_lblt": "300", "total_lblt": "800", "cpfn": "100",
             "total_cptl": "2200"},
            {"stac_yymm": "202212", "cras": "900", "fxas": "1800", "total_aset": "2700",
             "flow_lblt": "450", "fix_lblt": "250", "total_lblt": "700", "cpfn": "100",
             "total_cptl": "2000"}]
    fake = FakeTransport(response=_resp(rows))
    sheets = _client(fake).ticker("000660").balance_sheet()
    assert all(isinstance(s, BalanceSheet) for s in sheets)
    s = sheets[0]
    assert s.symbol == "000660"
    assert s.period == "202312"
    assert s.total_assets == Decimal(3000)
    assert s.total_liabilities == Decimal(800)
    assert s.total_equity == Decimal(2200)
    assert sheets[1].period == "202212"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/finance/balance-sheet"
    assert call["tr_id"] == "FHKST66430100"
    assert call["params"]["FID_DIV_CLS_CODE"] == "0"             # 연간(기본)
    assert call["params"]["FID_INPUT_ISCD"] == "000660"


def test_balance_sheet_quarterly_flag():
    fake = FakeTransport(response=_resp([]))
    _client(fake).ticker("000660").balance_sheet(quarterly=True)
    assert fake.calls[0]["params"]["FID_DIV_CLS_CODE"] == "1"    # 분기


def test_income_statement_maps():
    rows = [{"stac_yymm": "202312", "sale_account": "5000", "sale_cost": "3000",
             "sale_totl_prfi": "2000", "sell_mang": "800", "bsop_prti": "1200",
             "thtr_ntin": "900"}]
    fake = FakeTransport(response=_resp(rows))
    stmts = _client(fake).ticker("000660").income_statement()
    assert isinstance(stmts[0], IncomeStatement)
    assert stmts[0].revenue == Decimal(5000)
    assert stmts[0].operating_income == Decimal(1200)
    assert stmts[0].net_income == Decimal(900)
    assert fake.calls[0]["path"] == "/uapi/domestic-stock/v1/finance/income-statement"
    assert fake.calls[0]["tr_id"] == "FHKST66430200"


def test_finance_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).ticker("000660").balance_sheet()


def test_finance_bad_value_fails_closed():
    rows = [{"stac_yymm": "202312", "cras": "n/a", "fxas": "0", "total_aset": "0",
             "flow_lblt": "0", "fix_lblt": "0", "total_lblt": "0", "cpfn": "0",
             "total_cptl": "0"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).ticker("000660").balance_sheet()


def test_financial_ratios_maps_headline():
    rows = [{"stac_yymm": "202312", "grs": "10.5", "bsop_prfi_inrt": "15.2",
             "ntin_inrt": "8.1", "roe_val": "12.3", "eps": "5000", "sps": "40000",
             "bps": "45000", "rsrv_rate": "1500.0", "lblt_rate": "35.5"}]
    fake = FakeTransport(response=_resp(rows))
    ratios = _client(fake).ticker("000660").financial_ratios()
    assert isinstance(ratios[0], FinancialRatio)
    assert ratios[0].roe == Decimal("12.3")
    assert ratios[0].eps == Decimal(5000)
    assert ratios[0].debt_ratio == Decimal("35.5")
    assert fake.calls[0]["path"] == "/uapi/domestic-stock/v1/finance/financial-ratio"
    assert fake.calls[0]["tr_id"] == "FHKST66430300"


def test_financial_ratios_optional_none():
    rows = [{"stac_yymm": "202312", "roe_val": "", "eps": "5000"}]
    fake = FakeTransport(response=_resp(rows))
    ratios = _client(fake).ticker("000660").financial_ratios()
    assert ratios[0].roe is None
    assert ratios[0].eps == Decimal(5000)


def test_balance_sheet_optional_line_items_none_for_financial_issuer():
    # 은행/보험은 유동/고정 구분을 미보고(공란) -> 소계는 None, 합계는 그대로여야 한다(verb 안 죽음).
    rows = [{"stac_yymm": "202312", "cras": "", "fxas": "", "total_aset": "3000",
             "flow_lblt": "", "fix_lblt": "", "total_lblt": "800", "cpfn": "100",
             "total_cptl": "2200"}]
    fake = FakeTransport(response=_resp(rows))
    s = _client(fake).ticker("000660").balance_sheet()[0]
    assert s.current_assets is None
    assert s.fixed_liabilities is None
    assert s.total_assets == Decimal(3000)               # 합계는 여전히 required
    assert s.total_equity == Decimal(2200)


def test_income_statement_optional_line_items_none_for_financial_issuer():
    rows = [{"stac_yymm": "202312", "sale_account": "5000", "sale_cost": "",
             "sale_totl_prfi": "", "sell_mang": "", "bsop_prti": "1200", "thtr_ntin": "900"}]
    fake = FakeTransport(response=_resp(rows))
    stmt = _client(fake).ticker("000660").income_statement()[0]
    assert stmt.cost_of_sales is None
    assert stmt.gross_profit is None
    assert stmt.sga_expenses is None
    assert stmt.revenue == Decimal(5000)                 # 합계는 required
    assert stmt.operating_income == Decimal(1200)
    assert stmt.net_income == Decimal(900)


def test_growth_ratios_maps():
    # 원장 응답 예시값(삼성전자 202312/202309).
    rows = [{"stac_yymm": "202312", "grs": "-14.33", "bsop_prfi_inrt": "-84.86",
             "equt_inrt": "2.52", "totl_aset_inrt": "1.67"},
            {"stac_yymm": "202309", "grs": "-17.52", "bsop_prfi_inrt": "-90.42",
             "equt_inrt": "5.50", "totl_aset_inrt": "-3.36"}]
    fake = FakeTransport(response=_resp(rows))
    ratios = _client(fake).ticker("005930").growth_ratios(quarterly=True)
    assert isinstance(ratios[0], GrowthRatio)
    assert ratios[0].revenue_growth == Decimal("-14.33")
    assert ratios[0].equity_growth == Decimal("2.52")
    assert ratios[0].total_asset_growth == Decimal("1.67")
    assert ratios[1].period == "202309"
    assert fake.calls[0]["path"] == "/uapi/domestic-stock/v1/finance/growth-ratio"
    assert fake.calls[0]["tr_id"] == "FHKST66430800"
    assert fake.calls[0]["params"]["FID_DIV_CLS_CODE"] == "1"


def test_other_ratios_maps():
    # 원장 응답 예시값(삼성전자 202212). payout_rate 는 무시(별도 필드 없음, _raw 에만).
    rows = [{"stac_yymm": "202212", "payout_rate": "0.05", "eva": "-18075.00",
             "ebitda": "209609.00", "ev_ebitda": "3.48"}]
    fake = FakeTransport(response=_resp(rows))
    ratio = _client(fake).ticker("005930").other_ratios()[0]
    assert isinstance(ratio, OtherRatio)
    assert ratio.eva == Decimal("-18075.00")
    assert ratio.ebitda == Decimal("209609.00")
    assert ratio.ev_ebitda == Decimal("3.48")
    assert ratio._raw["payout_rate"] == "0.05"           # 무시 필드는 _raw 에 보존
    assert fake.calls[0]["path"] == "/uapi/domestic-stock/v1/finance/other-major-ratios"
    assert fake.calls[0]["tr_id"] == "FHKST66430500"
    assert fake.calls[0]["params"]["FID_DIV_CLS_CODE"] == "0"
