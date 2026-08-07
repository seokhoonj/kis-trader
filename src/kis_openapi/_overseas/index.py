"""해외 지수/환율/국채/금선물 기간봉 조회 (내부).

사용자면(:class:`~kis_openapi.overseas_index.OverseasIndex`)이 호출한다. 해외 지수류는
``FID_COND_MRKT_DIV_CODE`` 로 자산 종류를 구분하고 ``FID_INPUT_ISCD`` 로 심볼을 조회한다.

KIS URL/TR-id (원장 대조):
- 기간봉: ``GET .../quotations/inquire-daily-chartprice`` ``FHKST03030100``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from .._domestic.market_data import (
    _parse_bar_timestamp,
    _period_code_for,
    _to_yyyymmdd,
    _today_kst,
    collect_period_bars,
)
from .._wire import required_decimal, required_int
from ..bar import Bar, Interval
from ..errors import KISUsageError
from ..transport import Transport

_BARS_PATH = "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
_BARS_TR   = "FHKST03030100"

_MARKET_DIVISION = {"index": "N", "fx": "X", "bond": "I", "gold": "S"}


def fetch_bars(
    transport: Transport,
    *,
    symbol: str,
    market_division: str,
    interval: Interval = "1d",
    start: str | date | None = None,
    end: str | date | None = None,
    max_bars: int | None = None,
) -> list[Bar]:
    """해외 지수류 기간봉을 과거->현재 오름차순으로 조회한다.

    ``interval`` 은 ``1d``/``1wk``/``1mo`` 이고 ``start`` 가 필요하다. ``end`` 는 기본 오늘이며
    ``max_bars`` 로 최근 봉 수를 제한한다. 해외 지수 분봉(``1m``)은 아직 지원하지 않는다.
    """
    if max_bars is not None and max_bars <= 0:
        raise KISUsageError(f"max_bars 는 양의 정수여야 한다: {max_bars}")
    if interval == "1m":
        raise NotImplementedError("해외 지수 분봉은 아직 미구현이다.")
    if start is None:
        raise KISUsageError(f"interval={interval!r}(기간봉)에는 start 가 필요하다.")
    period = _period_code_for(interval)
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _to_yyyymmdd(start, "start")
    if start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    base_params = {
        "FID_COND_MRKT_DIV_CODE": market_division,
        "FID_INPUT_ISCD": symbol,
        "FID_PERIOD_DIV_CODE": period,
    }
    return collect_period_bars(
        transport, path=_BARS_PATH, tr=_BARS_TR, base_params=base_params,
        start_date=start_date, end_date=end_date, max_bars=max_bars,
        parse_rows=lambda rows: _parse_bars(rows, symbol=symbol),
    )


def _parse_bars(rows: Sequence[Mapping[str, Any]], *, symbol: str) -> list[Bar]:
    """해외 지수류 일봉 행의 ``ovrs_nmix_*`` 필드를 :class:`Bar` 로 바꾼다."""
    bars: list[Bar] = []
    for row in rows:
        date_text  = str(row.get("stck_bsop_date", "")).strip()
        close_text = str(row.get("ovrs_nmix_prpr", "")).strip()
        if not date_text or not close_text:
            continue
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(date_text),
                open=required_decimal(row.get("ovrs_nmix_oprc"), "ovrs_nmix_oprc"),
                high=required_decimal(row.get("ovrs_nmix_hgpr"), "ovrs_nmix_hgpr"),
                low=required_decimal(row.get("ovrs_nmix_lwpr"), "ovrs_nmix_lwpr"),
                close=required_decimal(close_text, "ovrs_nmix_prpr"),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                _raw=row,
            )
        )
    return bars
