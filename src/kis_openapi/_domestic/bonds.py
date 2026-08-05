"""장내채권 시세 조회 (내부) -- 채권 현재가를 :class:`BondQuote` 로.

사용자면은 채권 핸들(:class:`~kis_openapi.bond.Bond`, ``kis.bond(code)``)이다. 채권은 시장구분 ``B`` +
표준코드(ISIN, 예: KR2033022D33)로 조회한다.

KIS URL/TR-id (원장 대조):
- 채권 현재가: ``GET .../domestic-bond/v1/quotations/inquire-price`` ``FHKBJ773400C0``
  (``FID_COND_MRKT_DIV_CODE=B``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .._wire import optional_decimal, required_decimal, required_int
from ..bond_items import BondQuote
from ..transport import Transport
from .market_data import _KST, _apply_change_sign, _missing_block_error, _raise_if_error

_QUOTE_PATH = "/uapi/domestic-bond/v1/quotations/inquire-price"
_QUOTE_TR = "FHKBJ773400C0"
#: 채권 조회의 시장구분 코드(원장: 채권 B).
_MARKET_DIV = "B"


def fetch_quote(transport: Transport, *, code: str) -> BondQuote:
    """채권 현재가 스냅샷. ``code`` 는 표준코드(ISIN)."""
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_QUOTE_PATH, tr_id=_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_quote(output, code=code, as_of=datetime.now(_KST))


def _parse_quote(output: Mapping[str, Any], *, code: str, as_of: datetime) -> BondQuote:
    sign = str(output.get("prdy_vrss_sign", "")).strip()
    return BondQuote(
        code=code,
        name=str(output.get("hts_kor_isnm", "")).strip(),
        price=required_decimal(output.get("bond_prpr"), "bond_prpr"),
        open=required_decimal(output.get("bond_oprc"), "bond_oprc"),
        high=required_decimal(output.get("bond_hgpr"), "bond_hgpr"),
        low=required_decimal(output.get("bond_lwpr"), "bond_lwpr"),
        previous_close=required_decimal(output.get("bond_prdy_clpr"), "bond_prdy_clpr"),
        change=_apply_change_sign(
            required_decimal(output.get("bond_prdy_vrss"), "bond_prdy_vrss"), sign
        ),
        change_percent=_apply_change_sign(
            required_decimal(output.get("prdy_ctrt"), "prdy_ctrt"), sign
        ),
        volume=required_int(output.get("acml_vol"), "acml_vol"),
        yield_rate=optional_decimal(output.get("ernn_rate"), "ernn_rate"),
        as_of=as_of,
        _raw=output,
    )
