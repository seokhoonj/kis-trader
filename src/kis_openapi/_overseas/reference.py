"""해외 시장 전체 참조표 조회(내부) -- 시장별 현지·국내 결제일자."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from .._domestic.market_data import _missing_block_error, _raise_if_error, _to_yyyymmdd
from .._wire import optional_decimal
from ..errors import KISError, KISUsageError
from ..overseas_items import (
    OverseasCorporateAction,
    OverseasRight,
    OverseasSettlementDate,
)
from ..transport import Environment, Transport

_SETTLEMENT_DATES_PATH = "/uapi/overseas-stock/v1/quotations/countries-holiday"
_SETTLEMENT_DATES_TR = "CTOS5011R"
#: 결제일자 CTX_AREA 연속조회 페이지 상한. 도달하면 부분 결과로 자르지 않고 fail-closed.
_MAX_SETTLEMENT_PAGES = 100
_PERIOD_RIGHTS_PATH = "/uapi/overseas-price/v1/quotations/period-rights"
_PERIOD_RIGHTS_TR = "CTRGT011R"
_CORPORATE_ACTIONS_PATH = "/uapi/overseas-price/v1/quotations/rights-by-ice"
_CORPORATE_ACTIONS_TR = "HHDFS78330900"


def _YYYYMMDD(value: object) -> date | None:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()  # noqa: DTZ007
    except ValueError:
        return None


def fetch_period_rights(
    transport: Transport, *, start: str | date, end: str | date,
    right_type: str = "%%", date_basis: str = "local_base",
    symbol: str = "", product_type: str = "",
) -> list[OverseasRight]:
    """기간별 해외증권 권리를 연속조회한다."""
    basis_code = {"local_base": "02", "subscription_start": "03", "subscription_end": "04"}.get(date_basis)
    if basis_code is None or not right_type.strip():
        raise KISUsageError("date_basis 또는 right_type 값이 유효하지 않다.")
    start_date, end_date = _to_yyyymmdd(start, "start"), _to_yyyymmdd(end, "end")
    if start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    rows: list[Mapping[str, Any]] = []
    ctx_nk, ctx_fk, tr_cont = "", "", ""
    for _page in range(100):
        resp = transport.request(
            method="GET", path=_PERIOD_RIGHTS_PATH, tr_id=_PERIOD_RIGHTS_TR,
            params={"RGHT_TYPE_CD": right_type.strip(), "INQR_DVSN_CD": basis_code,
                    "INQR_STRT_DT": start_date, "INQR_END_DT": end_date, "PDNO": symbol.strip(),
                    "PRDT_TYPE_CD": product_type.strip(), "CTX_AREA_NK50": ctx_nk,
                    "CTX_AREA_FK50": ctx_fk}, idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        page = resp.body.get("output")
        if not isinstance(page, list) or not all(isinstance(row, Mapping) for row in page):
            raise _missing_block_error("output", resp)
        rows.extend(page)
        ctx_nk = str(resp.body.get("ctx_area_nk50", "")).strip()
        ctx_fk = str(resp.body.get("ctx_area_fk50", "")).strip()
        if resp.tr_cont not in {"F", "M"}:
            break
        tr_cont = "N"
    else:
        raise KISError("해외 기간별 권리조회가 100페이지 상한을 초과했다.")
    return [_parse_period_right(row) for row in rows]


def _parse_period_right(row: Mapping[str, Any]) -> OverseasRight:
    return OverseasRight(
        base_date=_YYYYMMDD(row.get("bass_dt")), right_type_code=str(row.get("rght_type_cd", "")).strip(),
        symbol=str(row.get("pdno", "")).strip(), name=str(row.get("prdt_name", "")).strip(),
        product_type=str(row.get("prdt_type_cd", "")).strip(),
        standard_symbol=str(row.get("std_pdno", "")).strip(),
        local_base_date=_YYYYMMDD(row.get("acpl_bass_dt")),
        subscription_start_date=_YYYYMMDD(row.get("sbsc_strt_dt")),
        subscription_end_date=_YYYYMMDD(row.get("sbsc_end_dt")),
        cash_allocation_rate=optional_decimal(row.get("cash_alct_rt"), "cash_alct_rt"),
        stock_allocation_rate=optional_decimal(row.get("stck_alct_rt"), "stck_alct_rt"),
        currencies=tuple(str(row.get(key, "")).strip() for key in ("crcy_cd", "crcy_cd2", "crcy_cd3", "crcy_cd4")),
        allocation_price=optional_decimal(row.get("alct_frcr_unpr"), "alct_frcr_unpr"),
        dividends_per_share=tuple(optional_decimal(row.get(key), key) for key in
                                  ("stkp_dvdn_frcr_amt2", "stkp_dvdn_frcr_amt3", "stkp_dvdn_frcr_amt4")),
        is_final=str(row.get("dfnt_yn", "")).strip() == "Y", _raw=row,
    )


def fetch_corporate_actions(
    transport: Transport, *, country: str, symbol: str,
    start: str | date | None = None, end: str | date | None = None,
) -> list[OverseasCorporateAction]:
    """해외종목 권리·기업행사 종합 일정을 조회한다."""
    if not country.strip() or not symbol.strip():
        raise KISUsageError("country 와 symbol 이 필요하다.")
    start_date = "" if start is None else _to_yyyymmdd(start, "start")
    end_date = "" if end is None else _to_yyyymmdd(end, "end")
    if start_date and end_date and start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    resp = transport.request(
        method="GET", path=_CORPORATE_ACTIONS_PATH, tr_id=_CORPORATE_ACTIONS_TR,
        params={"NCOD": country.strip(), "SYMB": symbol.strip(),
                "ST_YMD": start_date, "ED_YMD": end_date}, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output1")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise _missing_block_error("output1", resp)
    return [OverseasCorporateAction(
        announced_date=_YYYYMMDD(row.get("anno_dt")), title=str(row.get("ca_title", "")).strip(),
        ex_dividend_date=_YYYYMMDD(row.get("div_lock_dt")), payment_date=_YYYYMMDD(row.get("pay_dt")),
        record_date=_YYYYMMDD(row.get("record_dt")), validity_date=_YYYYMMDD(row.get("validity_dt")),
        local_deadline=_YYYYMMDD(row.get("local_end_dt")), ex_rights_date=_YYYYMMDD(row.get("lock_dt")),
        delisting_date=_YYYYMMDD(row.get("delist_dt")), redemption_date=_YYYYMMDD(row.get("redempt_dt")),
        early_redemption_date=_YYYYMMDD(row.get("early_redempt_dt")),
        effective_date=_YYYYMMDD(row.get("effective_dt")), _raw=row,
    ) for row in rows]
def fetch_settlement_dates(
    transport: Transport, *, environment: Environment
) -> list[OverseasSettlementDate]:
    """해외 각 시장의 현지·국내 결제일자 전체를 조회한다.

    종목과 무관한 시장 전체 참조표이며 별도 조회 필터는 없다.

    KIS ``GET /uapi/overseas-stock/v1/quotations/countries-holiday``
    (``CTOS5011R``)를 사용하며 모의투자는 지원하지 않는다.

    이 TR은 ``tr_cont`` 미지원이므로 ``CTX_AREA`` 커서만으로 연속조회한다.

    성공 응답의 ``output`` 배열이 없거나 페이지 상한 뒤에도 커서가 남으면 부분 결과 대신 실패한다.
    """
    if environment == "demo":
        raise KISUsageError("해외 시장별 결제일자 조회는 모의투자 미지원이다(실전만).")

    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk = "", ""
    for _page in range(_MAX_SETTLEMENT_PAGES):
        resp = transport.request(
            method="GET",
            path=_SETTLEMENT_DATES_PATH,
            tr_id=_SETTLEMENT_DATES_TR,
            params={"CTX_AREA_NK": ctx_nk, "CTX_AREA_FK": ctx_fk},
            idempotent=True,
        )
        _raise_if_error(resp)
        page = resp.body.get("output")
        if not isinstance(page, list):
            raise _missing_block_error("output", resp)
        if not all(isinstance(row, Mapping) for row in page):
            raise KISError("결제일자 응답의 output 항목이 객체가 아니다.", raw=resp.body)
        rows.extend(page)
        ctx_nk = str(resp.body.get("ctx_area_nk") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk") or "").strip()
        if not ctx_nk:
            break
    else:
        raise KISError(
            f"해외 시장별 결제일자 조회가 {_MAX_SETTLEMENT_PAGES}페이지 상한에 "
            "도달했으나 연속조회가 남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 "
            "수동 확인하라."
        )
    return [_parse_settlement_date(row) for row in rows]


def _parse_settlement_date(row: Mapping[str, Any]) -> OverseasSettlementDate:
    return OverseasSettlementDate(
        market_type_code=str(row.get("prdt_type_cd", "")).strip(),
        country_code=str(row.get("tr_natn_cd", "")).strip(),
        country_name=str(row.get("tr_natn_name", "")).strip(),
        country_abbr=str(row.get("natn_eng_abrv_cd", "")).strip(),
        market_code=str(row.get("tr_mket_cd", "")).strip(),
        market_name=str(row.get("tr_mket_name", "")).strip(),
        local_settlement_date=_YYYYMMDD(row.get("acpl_sttl_dt")),
        domestic_settlement_date=_YYYYMMDD(row.get("dmst_sttl_dt")),
        _raw=row,
    )
