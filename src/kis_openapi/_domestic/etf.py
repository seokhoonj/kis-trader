"""ETF/ETN 시세 조회 (내부) -- NAV 등 ETF 고유 정보를 :class:`EtfNav` 로.

사용자면은 종목 핸들(:class:`~kis_openapi.ticker.Ticker`)의 ETF 전용 verb(``kis.ticker(code).nav()``)다.
ETF/ETN 은 종목처럼 거래되므로 시세/주문은 일반 verb 로 하고, 여기선 NAV/괴리율/추적오차 같은 ETF
고유 필드만 다룬다. 엔드포인트는 ``etfetn`` 세그먼트라 경로가 ``/uapi/etfetn/...`` 로 다르다.

KIS URL/TR-id (원장 대조):
- ETF/ETN 현재가(NAV 포함): ``GET /uapi/etfetn/v1/quotations/inquire-price`` ``FHPST02400000``
  (``FID_COND_MRKT_DIV_CODE=J``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .._wire import required_decimal
from ..etf_items import EtfNav
from ..transport import Transport
from .market_data import _KST, _apply_change_sign, _missing_block_error, _raise_if_error

_ETF_NAV_PATH = "/uapi/etfetn/v1/quotations/inquire-price"
_ETF_NAV_TR = "FHPST02400000"
#: ETF/ETN 시세의 시장구분 코드(원장: 주식 J).
_ETF_MARKET_DIV = "J"


def fetch_etf_nav(transport: Transport, *, symbol: str) -> EtfNav:
    """ETF/ETN 순자산가치(NAV) 스냅샷. ``symbol`` 이 ETF/ETN 이 아니면 서버가 거부한다."""
    params = {"FID_COND_MRKT_DIV_CODE": _ETF_MARKET_DIV, "FID_INPUT_ISCD": symbol}
    resp = transport.request(
        method="GET", path=_ETF_NAV_PATH, tr_id=_ETF_NAV_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_etf_nav(output, symbol=symbol, as_of=datetime.now(_KST))


def _parse_etf_nav(output: Mapping[str, Any], *, symbol: str, as_of: datetime) -> EtfNav:
    sign = str(output.get("nav_prdy_vrss_sign", "")).strip()
    return EtfNav(
        symbol=symbol,
        nav=required_decimal(output.get("nav"), "nav"),
        nav_change=_apply_change_sign(
            required_decimal(output.get("nav_prdy_vrss"), "nav_prdy_vrss"), sign
        ),
        nav_change_percent=_apply_change_sign(
            required_decimal(output.get("nav_prdy_ctrt"), "nav_prdy_ctrt"), sign
        ),
        previous_nav=required_decimal(output.get("prdy_last_nav"), "prdy_last_nav"),
        premium=required_decimal(output.get("dprt"), "dprt"),
        tracking_error=required_decimal(output.get("trc_errt"), "trc_errt"),
        net_assets=required_decimal(output.get("etf_ntas_ttam"), "etf_ntas_ttam"),
        as_of=as_of,
        _raw=output,
    )
