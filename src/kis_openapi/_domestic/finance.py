"""재무제표 조회 (내부) -- 결산기별 대차대조표/손익계산서.

사용자면은 종목 핸들(:meth:`~kis_openapi.ticker.Ticker.balance_sheet` /
:meth:`~kis_openapi.ticker.Ticker.income_statement`)이다. 모두 ``/uapi/domestic-stock/v1/finance/``
아래에 있고, 파라미터는 시장구분(J)+종목코드+분류(FID_DIV_CLS_CODE 0:년/1:분기)를 공유하며 응답은
``output`` 결산기 배열이다(최근->과거).

KIS URL/TR-id:
- 대차대조표: ``GET .../finance/balance-sheet`` ``FHKST66430100``.
- 손익계산서: ``GET .../finance/income-statement`` ``FHKST66430200``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .._wire import optional_decimal, required_decimal
from ..financials import BalanceSheet, FinancialRatio, IncomeStatement
from ..transport import Transport
from .market_data import _missing_block_error, _raise_if_error

_BALANCE_SHEET = ("/uapi/domestic-stock/v1/finance/balance-sheet", "FHKST66430100")
_INCOME_STATEMENT = ("/uapi/domestic-stock/v1/finance/income-statement", "FHKST66430200")
_FINANCIAL_RATIO = ("/uapi/domestic-stock/v1/finance/financial-ratio", "FHKST66430300")


def _fetch_finance(
    transport: Transport, *, path: str, tr: str, symbol: str, quarterly: bool
) -> Sequence[Mapping[str, Any]]:
    """재무 조회 공통 -- 시장 J + 종목 + 분류(년/분기)로 GET, ``output`` 배열 반환(fail-closed)."""
    params = {
        "FID_DIV_CLS_CODE": "1" if quarterly else "0",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
    }
    resp = transport.request(method="GET", path=path, tr_id=tr, params=params, idempotent=True)
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    return rows


def fetch_balance_sheet(
    transport: Transport, *, symbol: str, quarterly: bool = False
) -> list[BalanceSheet]:
    """결산기별 대차대조표(최근->과거). ``quarterly`` 면 분기, 아니면 연간."""
    path, tr = _BALANCE_SHEET
    rows = _fetch_finance(transport, path=path, tr=tr, symbol=symbol, quarterly=quarterly)
    sheets: list[BalanceSheet] = []
    for row in rows:
        period = str(row.get("stac_yymm", "")).strip()
        if not period:
            continue
        sheets.append(
            BalanceSheet(
                symbol=symbol,
                period=period,
                current_assets=required_decimal(row.get("cras"), "cras"),
                fixed_assets=required_decimal(row.get("fxas"), "fxas"),
                total_assets=required_decimal(row.get("total_aset"), "total_aset"),
                current_liabilities=required_decimal(row.get("flow_lblt"), "flow_lblt"),
                fixed_liabilities=required_decimal(row.get("fix_lblt"), "fix_lblt"),
                total_liabilities=required_decimal(row.get("total_lblt"), "total_lblt"),
                capital=required_decimal(row.get("cpfn"), "cpfn"),
                total_equity=required_decimal(row.get("total_cptl"), "total_cptl"),
                _raw=row,
            )
        )
    return sheets


def fetch_income_statement(
    transport: Transport, *, symbol: str, quarterly: bool = False
) -> list[IncomeStatement]:
    """결산기별 손익계산서(최근->과거). ``quarterly`` 면 분기, 아니면 연간."""
    path, tr = _INCOME_STATEMENT
    rows = _fetch_finance(transport, path=path, tr=tr, symbol=symbol, quarterly=quarterly)
    statements: list[IncomeStatement] = []
    for row in rows:
        period = str(row.get("stac_yymm", "")).strip()
        if not period:
            continue
        statements.append(
            IncomeStatement(
                symbol=symbol,
                period=period,
                revenue=required_decimal(row.get("sale_account"), "sale_account"),
                cost_of_sales=required_decimal(row.get("sale_cost"), "sale_cost"),
                gross_profit=required_decimal(row.get("sale_totl_prfi"), "sale_totl_prfi"),
                sga_expenses=required_decimal(row.get("sell_mang"), "sell_mang"),
                operating_income=required_decimal(row.get("bsop_prti"), "bsop_prti"),
                net_income=required_decimal(row.get("thtr_ntin"), "thtr_ntin"),
                _raw=row,
            )
        )
    return statements


def fetch_financial_ratios(
    transport: Transport, *, symbol: str, quarterly: bool = False
) -> list[FinancialRatio]:
    """결산기별 주요 재무비율(최근->과거). ``quarterly`` 면 분기, 아니면 연간."""
    path, tr = _FINANCIAL_RATIO
    rows = _fetch_finance(transport, path=path, tr=tr, symbol=symbol, quarterly=quarterly)
    ratios: list[FinancialRatio] = []
    for row in rows:
        period = str(row.get("stac_yymm", "")).strip()
        if not period:
            continue
        ratios.append(
            FinancialRatio(
                symbol=symbol,
                period=period,
                revenue_growth=optional_decimal(row.get("grs"), "grs"),
                operating_income_growth=optional_decimal(row.get("bsop_prfi_inrt"),
                                                         "bsop_prfi_inrt"),
                net_income_growth=optional_decimal(row.get("ntin_inrt"), "ntin_inrt"),
                roe=optional_decimal(row.get("roe_val"), "roe_val"),
                eps=optional_decimal(row.get("eps"), "eps"),
                sps=optional_decimal(row.get("sps"), "sps"),
                bps=optional_decimal(row.get("bps"), "bps"),
                reserve_ratio=optional_decimal(row.get("rsrv_rate"), "rsrv_rate"),
                debt_ratio=optional_decimal(row.get("lblt_rate"), "lblt_rate"),
                _raw=row,
            )
        )
    return ratios
