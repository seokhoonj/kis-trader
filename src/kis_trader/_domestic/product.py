"""주식·파생·채권·해외주식 공통 상품기본조회(내부)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from .._datetime import _parse_kst_date
from .._response import (
    _missing_block_error,
    _raise_if_error,
)
from ..errors import KISUsageError
from ..product import ProductInfo
from ..transport import Transport

_PRODUCT_INFO_PATH = "/uapi/domestic-stock/v1/quotations/search-info"
_PRODUCT_INFO_TR = "CTPF1604R"


def _optional_date(value: object) -> date | None:
    text = str(value or "").strip()
    return _parse_kst_date(text) if text else None


def fetch_product_info(
    transport: Transport, *, symbol: str, product_type: str
) -> ProductInfo:
    """상품유형과 상품번호로 공통 등록·판매 기본정보를 조회한다."""
    if not symbol.strip():
        raise KISUsageError("symbol 이 필요하다.")
    if not product_type.strip():
        raise KISUsageError("product_type 이 필요하다.")
    resp = transport.request(
        method="GET", path=_PRODUCT_INFO_PATH, tr_id=_PRODUCT_INFO_TR,
        params={"PDNO": symbol.strip(), "PRDT_TYPE_CD": product_type.strip()}, idempotent=True,
    )
    _raise_if_error(resp)
    row = resp.body.get("output")
    if not isinstance(row, Mapping):
        raise _missing_block_error("output", resp)
    return ProductInfo(
        symbol=str(row.get("pdno", "")).strip(),
        product_type=str(row.get("prdt_type_cd", "")).strip(),
        name=str(row.get("prdt_name", "")).strip(),
        long_name=str(row.get("prdt_name120", "")).strip(),
        short_name=str(row.get("prdt_abrv_name", "")).strip(),
        english_name=str(row.get("prdt_eng_name", "")).strip(),
        long_english_name=str(row.get("prdt_eng_name120", "")).strip(),
        short_english_name=str(row.get("prdt_eng_abrv_name", "")).strip(),
        standard_symbol=str(row.get("std_pdno", "")).strip(),
        compact_symbol=str(row.get("shtn_pdno", "")).strip(),
        sale_status_code=str(row.get("prdt_sale_stat_cd", "")).strip(),
        risk_grade_code=str(row.get("prdt_risk_grad_cd", "")).strip(),
        classification_code=str(row.get("prdt_clsf_cd", "")).strip(),
        classification_name=str(row.get("prdt_clsf_name", "")).strip(),
        sale_start_date=_optional_date(row.get("sale_strt_dt")),
        sale_end_date=_optional_date(row.get("sale_end_dt")),
        wrap_asset_type_code=str(row.get("wrap_asst_type_cd", "")).strip(),
        investment_product_type_code=str(row.get("ivst_prdt_type_cd", "")).strip(),
        investment_product_type_name=str(row.get("ivst_prdt_type_cd_name", "")).strip(),
        first_registered_date=_optional_date(row.get("frst_erlm_dt")),
        _raw=row,
    )
