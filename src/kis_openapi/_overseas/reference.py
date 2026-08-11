"""해외 시장 전체 참조표 조회(내부) -- 시장별 현지·국내 결제일자."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from .._datetime import (
    _KST,
    _to_yyyymmdd,
)
from .._response import (
    _missing_block_error,
    _raise_if_error,
)
from .._wire import optional_decimal
from ..errors import KISError, KISUsageError
from ..market_items import NewsHeadline
from ..overseas_items import (
    OverseasCollateralStock,
    OverseasCorporateAction,
    OverseasNewsHeadline,
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
_NEWS_PATH = "/uapi/overseas-price/v1/quotations/news-title"
_NEWS_TR = "HHPSTH60100C1"
_BREAKING_NEWS_PATH = "/uapi/overseas-price/v1/quotations/brknews-title"
_BREAKING_NEWS_TR = "FHKST01011801"
_COLLATERAL_STOCKS_PATH = "/uapi/overseas-price/v1/quotations/colable-by-company"
_COLLATERAL_STOCKS_TR = "CTLN4050R"


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


def _news_timestamp(day: object, time: object) -> datetime:
    return datetime.strptime(f"{str(day).strip()}{str(time).strip()}", "%Y%m%d%H%M%S").replace(
        tzinfo=_KST
    )


def fetch_news(
    transport: Transport, *, country: str = "", exchange: str = "", symbol: str = "",
    date_: str | date | None = None, time: str = "", category: str = "",
) -> list[OverseasNewsHeadline]:
    """해외뉴스 종합 제목 피드를 연속조회한다."""
    input_date = "" if date_ is None else _to_yyyymmdd(date_, "date_")
    params = {"INFO_GB": "", "CLASS_CD": category, "NATION_CD": country,
              "EXCHANGE_CD": exchange, "SYMB": symbol, "DATA_DT": input_date,
              "DATA_TM": time, "CTS": ""}
    rows: list[Mapping[str, Any]] = []
    tr_cont = ""
    for _page in range(100):
        resp = transport.request(
            method="GET", path=_NEWS_PATH, tr_id=_NEWS_TR, params=params,
            idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        page = resp.body.get("outblock1")
        if not isinstance(page, list) or not all(isinstance(row, Mapping) for row in page):
            raise _missing_block_error("outblock1", resp)
        rows.extend(page)
        if resp.tr_cont not in {"F", "M"}:
            break
        tr_cont = "N"
    else:
        raise KISError("해외뉴스 종합 조회가 100페이지 상한을 초과했다.")
    return [OverseasNewsHeadline(
        news_type=str(row.get("info_gb", "")).strip(), key=str(row.get("news_key", "")).strip(),
        timestamp=_news_timestamp(row.get("data_dt"), row.get("data_tm")),
        category_code=str(row.get("class_cd", "")).strip(),
        category_name=str(row.get("class_name", "")).strip(), source=str(row.get("source", "")).strip(),
        country_code=str(row.get("nation_cd", "")).strip(),
        exchange_code=str(row.get("exchange_cd", "")).strip(), symbol=str(row.get("symb", "")).strip(),
        symbol_name=str(row.get("symb_name", "")).strip(), title=str(row.get("title", "")).strip(), _raw=row,
    ) for row in rows]


def fetch_breaking_news(
    transport: Transport, *, symbol: str = "", title: str = "",
    date_: str | date | None = None, time: str = "",
) -> list[NewsHeadline]:
    """해외속보 제목 피드(최대 100건)."""
    input_date = "" if date_ is None else _to_yyyymmdd(date_, "date_")
    resp = transport.request(
        method="GET", path=_BREAKING_NEWS_PATH, tr_id=_BREAKING_NEWS_TR,
        params={"FID_NEWS_OFER_ENTP_CODE": "0", "FID_COND_SCR_DIV_CODE": "11801",
                "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": symbol,
                "FID_TITL_CNTT": title, "FID_INPUT_DATE_1": input_date,
                "FID_INPUT_HOUR_1": time, "FID_RANK_SORT_CLS_CODE": "",
                "FID_INPUT_SRNO": ""}, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise _missing_block_error("output", resp)
    return [NewsHeadline(
        serial=str(row.get("cntt_usiq_srno", "")).strip(),
        timestamp=_news_timestamp(row.get("data_dt"), row.get("data_tm")),
        title=str(row.get("hts_pbnt_titl_cntt", "")).strip(),
        source=str(row.get("dorg", "")).strip(), category=str(row.get("news_lrdv_code", "")).strip(),
        symbols=tuple(code for i in range(1, 11)
                      if (code := str(row.get(f"iscd{i}", "")).strip())), _raw=row,
    ) for row in rows]


def fetch_collateral_stocks(
    transport: Transport, *, symbol: str, country: str, sort: str = "name",
    product_type: str = "", loanable: bool | None = None,
) -> list[OverseasCollateralStock]:
    """해외주식 담보대출 가능 종목과 적용 비율을 조회한다."""
    if not symbol.strip() or not country.strip():
        raise KISUsageError("symbol 과 country 가 필요하다.")
    sort_code = {"name": "01", "symbol": "02"}.get(sort)
    if sort_code is None:
        raise KISUsageError("sort 는 name 또는 symbol 이어야 한다.")
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(100):
        resp = transport.request(
            method="GET", path=_COLLATERAL_STOCKS_PATH, tr_id=_COLLATERAL_STOCKS_TR,
            params={"PDNO": symbol.strip(), "NATN_CD": country.strip(),
                    "INQR_SQN_DVSN": sort_code, "PRDT_TYPE_CD": product_type.strip(),
                    "INQR_STRT_DT": "", "INQR_END_DT": "", "INQR_DVSN": "",
                    "RT_DVSN_CD": "", "RT": "",
                    "LOAN_PSBL_YN": "" if loanable is None else ("Y" if loanable else "N"),
                    "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk},
            idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        page, summary = resp.body.get("output1"), resp.body.get("output2")
        if not isinstance(page, list) or not all(isinstance(row, Mapping) for row in page):
            raise _missing_block_error("output1", resp)
        if not isinstance(summary, Mapping):
            raise _missing_block_error("output2", resp)
        rows.extend(page)
        ctx_fk = str(resp.body.get("ctx_area_fk100", "")).strip()
        ctx_nk = str(resp.body.get("ctx_area_nk100", "")).strip()
        if resp.tr_cont not in {"F", "M"}:
            break
        tr_cont = "N"
    else:
        raise KISError("해외주식 담보대출 가능종목 조회가 100페이지 상한을 초과했다.")
    return [OverseasCollateralStock(
        symbol=str(row.get("pdno", "")).strip(), name=str(row.get("ovrs_item_name", "")).strip(),
        loan_rate=optional_decimal(row.get("loan_rt"), "loan_rt"),
        maintenance_rate=optional_decimal(row.get("mgge_mntn_rt"), "mgge_mntn_rt"),
        collateral_rate=optional_decimal(row.get("mgge_ensu_rt"), "mgge_ensu_rt"),
        is_loanable=str(row.get("loan_exec_psbl_yn", "")).strip() == "Y",
        registered_date=_YYYYMMDD(row.get("erlm_dt")),
        market_name=str(row.get("tr_mket_name", "")).strip(), currency=str(row.get("crcy_cd", "")).strip(),
        country_name=str(row.get("natn_kor_name", "")).strip(),
        exchange=str(row.get("ovrs_excg_cd", "")).strip(), _raw=row,
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
