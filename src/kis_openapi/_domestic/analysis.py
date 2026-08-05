"""per-ticker 일별 시세분석 조회 (내부) -- 신용잔고/공매도 추이.

사용자면은 종목 핸들(:meth:`~kis_openapi.ticker.Ticker.credit_balance_trend` /
:meth:`~kis_openapi.ticker.Ticker.short_sale_trend`)이다. 둘 다 기준일에서 과거로 일별 추이를 준다.

KIS URL/TR-id:
- 신용잔고 일별추이: ``GET .../quotations/daily-credit-balance`` ``FHPST04760000``
  (시장 J + 화면 20476 + 종목 + 기준일 FID_INPUT_DATE_1).
- 공매도 일별추이: ``GET .../quotations/daily-short-sale`` ``FHPST04830000``
  (시장 J + 종목 + 기간 FID_INPUT_DATE_1~FID_INPUT_DATE_2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from .._wire import optional_decimal, required_decimal, required_int
from ..analysis import CreditBalancePoint, LoanPoint, ShortSalePoint
from ..transport import Transport
from .market_data import (
    _apply_change_sign,
    _missing_block_error,
    _parse_bar_timestamp,
    _raise_if_error,
    _to_yyyymmdd,
    _today_kst,
)

_CREDIT_PATH = "/uapi/domestic-stock/v1/quotations/daily-credit-balance"
_CREDIT_TR = "FHPST04760000"
_SHORT_PATH = "/uapi/domestic-stock/v1/quotations/daily-short-sale"
_SHORT_TR = "FHPST04830000"


#: 추이 조회에서 start 를 안 주면 잡는 기본 조회 구간(일). _trend 가 한 점만 주지 않도록.
_DEFAULT_TREND_DAYS = 30


def _default_start(end_yyyymmdd: str) -> str:
    """start 미지정 시 기본 시작일 = end 로부터 ``_DEFAULT_TREND_DAYS`` 일 전(YYYYMMDD)."""
    end_day = datetime.strptime(end_yyyymmdd, "%Y%m%d")  # noqa: DTZ007 -- 날짜 산술만
    return f"{end_day - timedelta(days=_DEFAULT_TREND_DAYS):%Y%m%d}"


def _rows(transport: Transport, *, path: str, tr: str, params: Mapping[str, str]
          ) -> Sequence[Mapping[str, Any]]:
    resp = transport.request(method="GET", path=path, tr_id=tr, params=dict(params),
                             idempotent=True)
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    return rows


def fetch_credit_balance_trend(
    transport: Transport, *, symbol: str, date_: str | date | None = None
) -> list[CreditBalancePoint]:
    """일별 신용잔고(융자/대주) 추이(기준일에서 과거로). ``date_`` 없으면 오늘 기준."""
    base = _today_kst() if date_ is None else _to_yyyymmdd(date_, "date")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20476",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_DATE_1": base,
    }
    points: list[CreditBalancePoint] = []
    for row in _rows(transport, path=_CREDIT_PATH, tr=_CREDIT_TR, params=params):
        day = str(row.get("deal_date", "")).strip()
        if not day:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            CreditBalancePoint(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(day),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                margin_loan_shares=required_int(
                    row.get("whol_loan_rmnd_stcn"), "whol_loan_rmnd_stcn"
                ),
                margin_loan_amount=required_decimal(
                    row.get("whol_loan_rmnd_amt"), "whol_loan_rmnd_amt"
                ),
                margin_loan_ratio=optional_decimal(
                    row.get("whol_loan_rmnd_rate"), "whol_loan_rmnd_rate"
                ),
                stock_loan_shares=required_int(
                    row.get("whol_stln_rmnd_stcn"), "whol_stln_rmnd_stcn"
                ),
                stock_loan_amount=required_decimal(
                    row.get("whol_stln_rmnd_amt"), "whol_stln_rmnd_amt"
                ),
                stock_loan_ratio=optional_decimal(
                    row.get("whol_stln_rmnd_rate"), "whol_stln_rmnd_rate"
                ),
                _raw=row,
            )
        )
    return points


def fetch_short_sale_trend(
    transport: Transport, *, symbol: str,
    start: str | date | None = None, end: str | date | None = None,
) -> list[ShortSalePoint]:
    """일별 공매도 추이(기간 [start, end], 최근->과거). ``start`` 미지정이면 ``end`` 로부터 30일 전."""
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _default_start(end_date) if start is None else _to_yyyymmdd(start, "start")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
    }
    points: list[ShortSalePoint] = []
    for row in _rows(transport, path=_SHORT_PATH, tr=_SHORT_TR, params=params):
        day = str(row.get("stck_bsop_date", "")).strip()
        if not day:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            ShortSalePoint(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(day),
                close=required_decimal(row.get("stck_clpr"), "stck_clpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                short_volume=required_int(row.get("ssts_cntg_qty"), "ssts_cntg_qty"),
                short_volume_ratio=optional_decimal(row.get("ssts_vol_rlim"), "ssts_vol_rlim"),
                short_amount=required_decimal(row.get("ssts_tr_pbmn"), "ssts_tr_pbmn"),
                short_average_price=optional_decimal(row.get("avrg_prc"), "avrg_prc"),
                _raw=row,
            )
        )
    return points


_LOAN_PATH = "/uapi/domestic-stock/v1/quotations/daily-loan-trans"
_LOAN_TR = "HHPST074500C0"


def fetch_loan_trend(
    transport: Transport, *, symbol: str,
    start: str | date | None = None, end: str | date | None = None,
) -> list[LoanPoint]:
    """일별 대차거래(대여) 추이(기간 [start, end], 최근->과거). ``start`` 미지정이면 ``end`` 로부터 30일 전."""
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _default_start(end_date) if start is None else _to_yyyymmdd(start, "start")
    params = {
        "MRKT_DIV_CLS_CODE": "1",
        "MKSC_SHRN_ISCD": symbol,
        "START_DATE": start_date,
        "END_DATE": end_date,
        "CTS": "",
    }
    points: list[LoanPoint] = []
    for row in _rows(transport, path=_LOAN_PATH, tr=_LOAN_TR, params=params):
        day = str(row.get("bsop_date", "")).strip()
        if not day:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            LoanPoint(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(day),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                new_shares=required_int(row.get("new_stcn"), "new_stcn"),
                redeemed_shares=required_int(row.get("rdmp_stcn"), "rdmp_stcn"),
                balance_shares=required_int(row.get("rmnd_stcn"), "rmnd_stcn"),
                balance_amount=required_decimal(row.get("rmnd_amt"), "rmnd_amt"),
                balance_change=required_int(row.get("prdy_rmnd_vrss"), "prdy_rmnd_vrss"),
                _raw=row,
            )
        )
    return points
