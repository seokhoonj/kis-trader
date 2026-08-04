"""국내 지수/업종 시세 조회 (내부) -- 지수 현재가 등을 :class:`IndexQuote` 로.

사용자면(:class:`~kis_openapi.index.Index`, ``kis.index(code)``)이 호출한다. 지수/업종은 종목이
아니라 시장구분 ``U`` + 업종코드(``FID_INPUT_ISCD``)로 조회한다. 업종코드는 포털의 업종코드표를
따르며, 대표값은 0001 KOSPI 종합 / 1001 KOSDAQ 종합 / 2001 KOSPI200.

KIS URL/TR-id (원장 대조):
- 지수 현재가: ``GET .../quotations/inquire-index-price`` ``FHPUP02100000`` (``FID_COND_MRKT_DIV_CODE=U``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .._wire import required_decimal, required_int
from ..index_quote import IndexQuote
from ..transport import Transport
from .market_data import _KST, _apply_change_sign, _missing_block_error, _raise_if_error

_INDEX_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
_INDEX_QUOTE_TR = "FHPUP02100000"
#: 지수/업종 조회의 시장구분 코드(원장: 업종 U).
_INDEX_MARKET_DIV = "U"


def fetch_index_quote(transport: Transport, *, code: str) -> IndexQuote:
    """지수(업종) 현재가 스냅샷. ``code`` 는 업종코드(예: 0001 KOSPI)."""
    params = {"FID_COND_MRKT_DIV_CODE": _INDEX_MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_INDEX_QUOTE_PATH, tr_id=_INDEX_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_index_quote(output, code=code, as_of=datetime.now(_KST))


def _parse_index_quote(
    output: Mapping[str, Any], *, code: str, as_of: datetime
) -> IndexQuote:
    sign = str(output.get("prdy_vrss_sign", "")).strip()
    return IndexQuote(
        code=code,
        value=required_decimal(output.get("bstp_nmix_prpr"), "bstp_nmix_prpr"),
        open=required_decimal(output.get("bstp_nmix_oprc"), "bstp_nmix_oprc"),
        high=required_decimal(output.get("bstp_nmix_hgpr"), "bstp_nmix_hgpr"),
        low=required_decimal(output.get("bstp_nmix_lwpr"), "bstp_nmix_lwpr"),
        change=_apply_change_sign(
            required_decimal(output.get("bstp_nmix_prdy_vrss"), "bstp_nmix_prdy_vrss"), sign
        ),
        change_percent=_apply_change_sign(
            required_decimal(output.get("bstp_nmix_prdy_ctrt"), "bstp_nmix_prdy_ctrt"), sign
        ),
        volume=required_int(output.get("acml_vol"), "acml_vol"),
        amount=required_decimal(output.get("acml_tr_pbmn"), "acml_tr_pbmn"),
        advances=required_int(output.get("ascn_issu_cnt"), "ascn_issu_cnt"),
        declines=required_int(output.get("down_issu_cnt"), "down_issu_cnt"),
        unchanged=required_int(output.get("stnr_issu_cnt"), "stnr_issu_cnt"),
        limit_up=required_int(output.get("uplm_issu_cnt"), "uplm_issu_cnt"),
        limit_down=required_int(output.get("lslm_issu_cnt"), "lslm_issu_cnt"),
        as_of=as_of,
        _raw=output,
    )
