"""ELW(주식워런트증권) 고유 지표 조회 (내부).

사용자면은 ELW 핸들(:class:`~kis_openapi.elw.Elw`, ``kis.elw(code)``)이다. ELW 는 6자리 코드로
상장돼 기본 시세는 종목 엔진(시장구분 J)으로 조회되므로, 여기서는 ELW 고유의 옵션 분석 지표
(민감도/변동성/투자지표 추이)만 다룬다. ELW 조회의 시장구분코드는 ``W`` 다.

각 추이는 체결별/일별/분별/틱 시간축을 갖는데, 지표군마다 지원 축이 다르다(원장 대조):
- 민감도 추이: 체결(``FHPEW02830100``) / 일별(``FHPEW02830200``).
  ``GET .../elw/v1/quotations/sensitivity-trend-ccnl`` / ``.../sensitivity-trend-daily``.
- 변동성 추이: 체결(``FHPEW02840100``) / 일별(``FHPEW02840200``) / 분별(``FHPEW02840300``) /
  틱(``FHPEW02840400``). ``GET .../elw/v1/quotations/volatility-trend-{ccnl,daily,minute,tick}``.
  시간축마다 가격 키/시각 키가 다르다(체결=elw_prpr+체결시각, 일별=elw_prpr+영업일자, 분별=stck_prpr
  +영업일자/체결시각, 틱=elw_prpr+영업일자/체결시각). 공통 축(가격/내재변동성/전일대비)만 매핑.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, NamedTuple

from .._wire import optional_decimal, required_decimal
from ..elw_items import ElwSensitivityPoint, ElwVolatilityPoint
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

#: 시계열 시간축 -- "trade"(체결별), "day"(일별), "minute"(분별), "tick"(틱).
#: 지표군마다 지원 축이 다르다(미지원 축은 KisUsageError).
TrendInterval = Literal["trade", "day", "minute", "tick"]

#: 분별 조회의 시간 간격(분) -> KIS 초 코드(FID_HOUR_CLS_CODE).
_MINUTE_SPAN_SECONDS = {1: "60", 3: "180", 5: "300", 10: "600", 30: "1800", 60: "3600"}

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
    transport: Transport, *, path: str, tr: str, code: str,
    extra_params: Mapping[str, str] | None = None,
) -> Sequence[Mapping[str, Any]]:
    """ELW 추이 조회 공통 -- 시장구분 W + 종목코드로 GET, ``output`` 배열을 돌려준다(fail-closed)."""
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    if extra_params:
        params.update(extra_params)
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


class _VolatilitySpec(NamedTuple):
    path: str
    tr: str
    price_key: str                    # 가격 필드(체결/일별/틱=elw_prpr, 분별=stck_prpr)
    date_key: str | None              # 영업일자 필드(없으면 조회일로 시각만)
    time_key: str | None              # 체결시각 필드(없으면 날짜만)
    has_change: bool                  # 전일대비 필드 유무(분별/틱은 없음)


_VOLATILITY_SPEC = {
    "trade": _VolatilitySpec(
        "/uapi/elw/v1/quotations/volatility-trend-ccnl", "FHPEW02840100",
        "elw_prpr", None, "stck_cntg_hour", True,
    ),
    "day": _VolatilitySpec(
        "/uapi/elw/v1/quotations/volatility-trend-daily", "FHPEW02840200",
        "elw_prpr", "stck_bsop_date", None, True,
    ),
    "minute": _VolatilitySpec(
        "/uapi/elw/v1/quotations/volatility-trend-minute", "FHPEW02840300",
        "stck_prpr", "stck_bsop_date", "stck_cntg_hour", False,
    ),
    "tick": _VolatilitySpec(
        "/uapi/elw/v1/quotations/volatility-trend-tick", "FHPEW02840400",
        "elw_prpr", "bsop_date", "stck_cntg_hour", False,
    ),
}


def fetch_volatility_trend(
    transport: Transport, *, code: str, interval: TrendInterval = "day",
    minutes: int = 1, include_past: bool = False,
) -> list[ElwVolatilityPoint]:
    """ELW 변동성(내재변동성) 추이. ``interval`` 은 체결/일별/분별/틱 모두 지원.

    ``minutes`` 는 ``interval="minute"`` 일 때만 쓰는 봉 간격(1/3/5/10/30/60분), ``include_past`` 는
    분별에서 과거 데이터 포함 여부(FID_PW_DATA_INCU_YN). 벤더 순서(최신순)를 유지하며, 가격/내재
    변동성/전일대비만 매핑하고 나머지(역사변동성 곡선·OHLC·호가)는 ``_raw`` 에 있다."""
    try:
        spec = _VOLATILITY_SPEC[interval]
    except KeyError:
        raise KisUsageError(
            f"변동성 추이 interval 은 'trade'/'day'/'minute'/'tick': {interval!r}"
        ) from None
    extra: dict[str, str] | None = None
    if interval == "minute":
        try:
            span = _MINUTE_SPAN_SECONDS[minutes]
        except KeyError:
            raise KisUsageError(
                f"minutes 는 1/3/5/10/30/60 중 하나: {minutes!r}"
            ) from None
        extra = {"FID_HOUR_CLS_CODE": span, "FID_PW_DATA_INCU_YN": "Y" if include_past else "N"}
    rows = _fetch_trend_rows(transport, path=spec.path, tr=spec.tr, code=code, extra_params=extra)
    as_of = datetime.now(_KST)
    return [
        _parse_volatility_row(row, code=code, as_of=as_of, spec=spec)
        for row in rows
        if _spec_has_time(row, spec)
    ]


def _spec_has_time(row: Mapping[str, Any], spec: _VolatilitySpec) -> bool:
    key = spec.time_key or spec.date_key
    return bool(key) and bool(str(row.get(key, "")).strip())


def _spec_timestamp(row: Mapping[str, Any], *, as_of: datetime, spec: _VolatilitySpec) -> datetime:
    date_text = str(row.get(spec.date_key, "")).strip() if spec.date_key else ""
    time_text = str(row.get(spec.time_key, "")).strip() if spec.time_key else ""
    if date_text and time_text:
        return _combine_date_time(date_text, time_text)
    if time_text:                              # 시각만 -> 조회일 날짜를 붙임
        return _parse_intraday_timestamp(time_text, as_of)
    return _parse_bar_timestamp(date_text)     # 날짜만


def _combine_date_time(date_text: str, time_text: str) -> datetime:
    """영업일자(YYYYMMDD) + 체결시각(HHMMSS) -> KST-aware datetime."""
    day = _parse_bar_timestamp(date_text)
    moment = _parse_intraday_timestamp(time_text, day)
    return day.replace(hour=moment.hour, minute=moment.minute, second=moment.second)


def _parse_volatility_row(
    row: Mapping[str, Any], *, code: str, as_of: datetime, spec: _VolatilitySpec
) -> ElwVolatilityPoint:
    change = change_percent = None
    if spec.has_change:
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        change = _apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign)
        change_percent = _apply_change_sign(
            required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
        )
    return ElwVolatilityPoint(
        code=code,
        timestamp=_spec_timestamp(row, as_of=as_of, spec=spec),
        price=required_decimal(row.get(spec.price_key), spec.price_key),
        implied_volatility=optional_decimal(row.get("hts_ints_vltl"), "hts_ints_vltl"),
        change=change,
        change_percent=change_percent,
        _raw=row,
    )
