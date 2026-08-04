"""해외주식 시세 조회 (내부) -- 현재가 등을 :class:`Quote` 로.

해외는 거래소코드(EXCD)+심볼로 조회한다. 국내와 달리 통화가 시장마다 다르므로(USD/HKD/JPY...)
``Quote.currency`` 를 응답의 통화(``curr``)로 채운다. 전일대비는 KIS 가 native 통화로는 따로 주지
않아 현재가-전일종가로 계산한다.

KIS URL/TR-id (원장 대조):
- 해외 현재가상세: ``GET /uapi/overseas-price/v1/quotations/price-detail`` ``HHDFS76200200``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from .._domestic.market_data import _KST, _missing_block_error, _raise_if_error
from .._wire import required_decimal, required_int
from ..quote import Quote
from ..transport import Transport

_QUOTE_PATH = "/uapi/overseas-price/v1/quotations/price-detail"
_QUOTE_TR = "HHDFS76200200"
_PERCENT = Decimal("0.01")


def fetch_quote(transport: Transport, *, symbol: str, exchange: str) -> Quote:
    """해외 현재가 스냅샷. ``exchange`` 는 거래소코드(NAS/NYS/AMS/TSE/HKS/...)."""
    params = {"AUTH": "", "EXCD": exchange, "SYMB": symbol}
    resp = transport.request(
        method="GET", path=_QUOTE_PATH, tr_id=_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_quote(output, symbol=symbol, exchange=exchange, as_of=datetime.now(_KST))


def _parse_quote(
    output: Mapping[str, Any], *, symbol: str, exchange: str, as_of: datetime
) -> Quote:
    last = required_decimal(output.get("last"), "last")
    previous_close = required_decimal(output.get("base"), "base")
    change = last - previous_close
    # native 등락률은 응답에 없어 계산한다(전일종가 0 이면 나눗셈 불가 -> 0).
    change_percent = (
        (change / previous_close * 100).quantize(_PERCENT)
        if previous_close != 0
        else Decimal(0)
    )
    return Quote(
        symbol=symbol,
        market=exchange,
        currency=str(output.get("curr", "")).strip(),
        last=last,
        open=required_decimal(output.get("open"), "open"),
        high=required_decimal(output.get("high"), "high"),
        low=required_decimal(output.get("low"), "low"),
        previous_close=previous_close,
        change=change,
        change_percent=change_percent,
        volume=required_int(output.get("tvol"), "tvol"),
        week_52_high=required_decimal(output.get("h52p"), "h52p"),
        week_52_low=required_decimal(output.get("l52p"), "l52p"),
        as_of=as_of,
        _raw=output,
    )
