"""ELW(주식워런트증권) 고유 지표 조회 (내부).

사용자면은 ELW 핸들(:class:`~kis_openapi.elw.Elw`, ``kis.elw(code)``)이다. ELW 는 6자리 코드로
상장돼 기본 시세는 종목 엔진(시장구분 J)으로 조회되므로, 여기서는 ELW 고유의 옵션 분석 지표
(민감도/변동성/투자지표 추이)만 다룬다. ELW 조회의 시장구분코드는 ``W`` 다.

각 추이는 체결별/일별/분별/틱 시간축을 갖는데, 지표군마다 지원 축이 다르다(원장 대조):
- 민감도 추이: 체결(``FHPEW02830100``) / 일별(``FHPEW02830200``).
  ``GET .../elw/v1/quotations/sensitivity-trend-ccnl`` / ``.../sensitivity-trend-daily``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from .._wire import optional_decimal, required_decimal
from ..elw_items import ElwSensitivityPoint
from ..errors import KisUsageError
from ..transport import Transport
from .market_data import (
    _KST,
    _apply_change_sign,
    _missing_block_error,
    _parse_bar_timestamp,
    _parse_intraday_timestamp,
    _raise_if_error,
)

#: ELW 조회의 시장구분코드(원장: ELW W).
_MARKET_DIV = "W"

#: 시계열 시간축 -- "trade"(체결별), "day"(일별). 지표군마다 지원 축이 다르다.
TrendInterval = Literal["trade", "day", "minute", "tick"]

_SENSITIVITY_TR = {
    "trade": ("/uapi/elw/v1/quotations/sensitivity-trend-ccnl", "FHPEW02830100"),
    "day": ("/uapi/elw/v1/quotations/sensitivity-trend-daily", "FHPEW02830200"),
}


def fetch_sensitivity_trend(
    transport: Transport, *, code: str, interval: TrendInterval = "day"
) -> list[ElwSensitivityPoint]:
    """ELW 민감도(그릭스) 추이. ``interval`` 은 ``"trade"``(체결별)/``"day"``(일별).

    체결별은 조회일의 체결 시각별, 일별은 최근 영업일별 그릭스 시계열이다(둘 다 최신순 벤더 순서
    유지). ``code`` 는 ELW 표준코드(6자리, 예: 58J297)."""
    try:
        path, tr = _SENSITIVITY_TR[interval]
    except KeyError:
        raise KisUsageError(
            f"민감도 추이는 interval='trade'/'day' 만 지원한다: {interval!r}"
        ) from None
    rows = _fetch_trend_rows(transport, path=path, tr=tr, code=code)
    intraday = interval != "day"
    return [
        _parse_sensitivity_row(row, code=code, as_of=datetime.now(_KST), intraday=intraday)
        for row in rows
        if _has_time_key(row, intraday)
    ]


def _fetch_trend_rows(
    transport: Transport, *, path: str, tr: str, code: str
) -> Sequence[Mapping[str, Any]]:
    """ELW 추이 조회 공통 -- 시장구분 W + 종목코드로 GET, ``output`` 배열을 돌려준다(fail-closed)."""
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=path, tr_id=tr, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):             # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return rows


def _has_time_key(row: Mapping[str, Any], intraday: bool) -> bool:
    key = "stck_cntg_hour" if intraday else "stck_bsop_date"
    return bool(str(row.get(key, "")).strip())


def _trend_timestamp(row: Mapping[str, Any], *, as_of: datetime, intraday: bool) -> datetime:
    if intraday:
        return _parse_intraday_timestamp(str(row.get("stck_cntg_hour", "")).strip(), as_of)
    return _parse_bar_timestamp(str(row.get("stck_bsop_date", "")).strip())


def _parse_sensitivity_row(
    row: Mapping[str, Any], *, code: str, as_of: datetime, intraday: bool
) -> ElwSensitivityPoint:
    sign = str(row.get("prdy_vrss_sign", "")).strip()
    return ElwSensitivityPoint(
        code=code,
        timestamp=_trend_timestamp(row, as_of=as_of, intraday=intraday),
        price=required_decimal(row.get("elw_prpr"), "elw_prpr"),
        change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
        change_percent=_apply_change_sign(
            required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
        ),
        theoretical_price=optional_decimal(row.get("hts_thpr"), "hts_thpr"),
        delta=optional_decimal(row.get("delta_val"), "delta_val"),
        gamma=optional_decimal(row.get("gama"), "gama"),
        theta=optional_decimal(row.get("theta"), "theta"),
        vega=optional_decimal(row.get("vega"), "vega"),
        rho=optional_decimal(row.get("rho"), "rho"),
        _raw=row,
    )
