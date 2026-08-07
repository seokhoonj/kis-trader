"""해외 시장 전체 참조표 조회(내부) -- 시장별 현지·국내 결제일자."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from .._domestic.market_data import _missing_block_error, _raise_if_error
from ..errors import KISError, KISUsageError
from ..overseas_items import OverseasSettlementDate
from ..transport import Environment, Transport

_SETTLEMENT_DATES_PATH = "/uapi/overseas-stock/v1/quotations/countries-holiday"
_SETTLEMENT_DATES_TR = "CTOS5011R"
#: 결제일자 CTX_AREA 연속조회 페이지 상한. 도달하면 부분 결과로 자르지 않고 fail-closed.
_MAX_SETTLEMENT_PAGES = 100


def _YYYYMMDD(value: object) -> date | None:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()  # noqa: DTZ007
    except ValueError:
        return None


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
