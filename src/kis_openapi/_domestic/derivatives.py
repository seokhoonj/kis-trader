"""국내 선물/옵션(파생) 시세 조회 (내부) -- 계약 현재가를 :class:`DerivativesQuote` 로.

사용자면은 파생 핸들(:class:`~kis_openapi.derivative.Derivative`, ``kis.futures(code)`` /
``kis.option(code)``)이다. 파생은 종목이 아니라 시장구분(F:지수선물 / O:지수옵션) + 계약코드로
조회한다. 스캘핑에 필요한 미결제약정·베이시스·이론가는 output1 에서 매핑한다.

KIS URL/TR-id (원장 대조):
- 선물옵션 현재가: ``GET .../domestic-futureoption/v1/quotations/inquire-price`` ``FHMIF10000000``
  (``FID_COND_MRKT_DIV_CODE`` F:지수선물 / O:지수옵션).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .._wire import optional_decimal, required_decimal, required_int
from ..derivative_items import DerivativesQuote
from ..transport import Transport
from .market_data import _KST, _apply_change_sign, _missing_block_error, _raise_if_error

_QUOTE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
_QUOTE_TR = "FHMIF10000000"


def fetch_quote(transport: Transport, *, code: str, market: str) -> DerivativesQuote:
    """선물/옵션 계약 현재가 스냅샷. ``market`` 은 F(지수선물)/O(지수옵션), ``code`` 는 계약코드."""
    params = {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_QUOTE_PATH, tr_id=_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output1")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output1", resp)
    return _parse_quote(output, code=code, as_of=datetime.now(_KST))


def _parse_quote(output: Mapping[str, Any], *, code: str, as_of: datetime) -> DerivativesQuote:
    sign = str(output.get("prdy_vrss_sign", "")).strip()
    return DerivativesQuote(
        code=code,
        name=str(output.get("hts_kor_isnm", "")).strip(),
        last=required_decimal(output.get("futs_prpr"), "futs_prpr"),
        open=required_decimal(output.get("futs_oprc"), "futs_oprc"),
        high=required_decimal(output.get("futs_hgpr"), "futs_hgpr"),
        low=required_decimal(output.get("futs_lwpr"), "futs_lwpr"),
        previous_close=required_decimal(output.get("futs_prdy_clpr"), "futs_prdy_clpr"),
        change=_apply_change_sign(
            required_decimal(output.get("futs_prdy_vrss"), "futs_prdy_vrss"), sign
        ),
        change_percent=_apply_change_sign(
            required_decimal(output.get("futs_prdy_ctrt"), "futs_prdy_ctrt"), sign
        ),
        volume=required_int(output.get("acml_vol"), "acml_vol"),
        open_interest=required_int(output.get("hts_otst_stpl_qty"), "hts_otst_stpl_qty"),
        theoretical_price=optional_decimal(output.get("hts_thpr"), "hts_thpr"),
        basis=optional_decimal(output.get("basis"), "basis"),
        premium=optional_decimal(output.get("dprt"), "dprt"),
        as_of=as_of,
        _raw=output,
    )
