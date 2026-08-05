"""ELW(주식워런트증권) 고유 지표 조회 (내부).

사용자면은 ELW 핸들(:class:`~kis_openapi.elw.ELW`, ``kis.elw(code)``)이다. ELW 는 6자리 코드로
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
from decimal import Decimal
from typing import Any, Literal, NamedTuple

from .._wire import optional_decimal, required_decimal, required_int
from ..elw_items import (
    ELWIndicatorPoint,
    ELWLpFlow,
    ELWSensitivityPoint,
    ELWVolatilityPoint,
    RankedELW,
)
from ..errors import KISUsageError
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
#: 지표군마다 지원 축이 다르다(미지원 축은 KISUsageError).
TrendInterval = Literal["trade", "day", "minute", "tick"]

#: 분별 조회의 시간 간격(분) -> KIS 초 코드(FID_HOUR_CLS_CODE).
_MINUTE_SPAN_SECONDS = {1: "60", 3: "180", 5: "300", 10: "600", 30: "1800", 60: "3600"}

_SENSITIVITY_TR = {
    "trade": ("/uapi/elw/v1/quotations/sensitivity-trend-ccnl", "FHPEW02830100"),
    "day": ("/uapi/elw/v1/quotations/sensitivity-trend-daily", "FHPEW02830200"),
}


def fetch_sensitivity_trend(
    transport: Transport, *, code: str, interval: TrendInterval = "day"
) -> list[ELWSensitivityPoint]:
    """ELW 민감도(그릭스) 추이. ``interval`` 은 ``"trade"``(체결별)/``"day"``(일별).

    체결별은 조회일의 체결 시각별, 일별은 최근 영업일별 그릭스 시계열이다(둘 다 최신순 벤더 순서
    유지). ``code`` 는 ELW 표준코드(6자리, 예: 58J297)."""
    try:
        path, tr = _SENSITIVITY_TR[interval]
    except KeyError:
        raise KISUsageError(
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
) -> ELWSensitivityPoint:
    sign = str(row.get("prdy_vrss_sign", "")).strip()
    return ELWSensitivityPoint(
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


class _TrendSpec(NamedTuple):
    path: str
    tr: str
    price_key: str                    # 가격 필드(체결/일별/틱=elw_prpr, 분별=stck_prpr)
    date_key: str | None              # 영업일자 필드(없으면 조회일로 시각만)
    time_key: str | None              # 체결시각 필드(없으면 날짜만)
    has_change: bool                  # 전일대비 필드 유무(분별/틱은 없음)


_VOLATILITY_SPEC = {
    "trade": _TrendSpec(
        "/uapi/elw/v1/quotations/volatility-trend-ccnl", "FHPEW02840100",
        "elw_prpr", None, "stck_cntg_hour", True,
    ),
    "day": _TrendSpec(
        "/uapi/elw/v1/quotations/volatility-trend-daily", "FHPEW02840200",
        "elw_prpr", "stck_bsop_date", None, True,
    ),
    "minute": _TrendSpec(
        "/uapi/elw/v1/quotations/volatility-trend-minute", "FHPEW02840300",
        "stck_prpr", "stck_bsop_date", "stck_cntg_hour", False,
    ),
    "tick": _TrendSpec(
        "/uapi/elw/v1/quotations/volatility-trend-tick", "FHPEW02840400",
        "elw_prpr", "bsop_date", "stck_cntg_hour", False,
    ),
}


def fetch_volatility_trend(
    transport: Transport, *, code: str, interval: TrendInterval = "day",
    minutes: int = 1, include_past: bool = False,
) -> list[ELWVolatilityPoint]:
    """ELW 변동성(내재변동성) 추이. ``interval`` 은 체결/일별/분별/틱 모두 지원.

    ``minutes`` 는 ``interval="minute"`` 일 때만 쓰는 봉 간격(1/3/5/10/30/60분), ``include_past`` 는
    분별에서 과거 데이터 포함 여부(FID_PW_DATA_INCU_YN). 벤더 순서(최신순)를 유지하며, 가격/내재
    변동성/전일대비만 매핑하고 나머지(역사변동성 곡선·OHLC·호가)는 ``_raw`` 에 있다."""
    spec = _pick_spec(_VOLATILITY_SPEC, interval, "변동성 추이")
    extra = _minute_extra_params(interval, minutes, include_past)
    rows = _fetch_trend_rows(transport, path=spec.path, tr=spec.tr, code=code, extra_params=extra)
    as_of = datetime.now(_KST)
    return [
        _parse_volatility_row(row, code=code, as_of=as_of, spec=spec)
        for row in rows
        if _spec_has_time(row, spec)
    ]


def _pick_spec(
    table: Mapping[str, _TrendSpec], interval: str, label: str
) -> _TrendSpec:
    try:
        return table[interval]
    except KeyError:
        raise KISUsageError(
            f"{label} interval 은 {sorted(table)} 중 하나: {interval!r}"
        ) from None


def _minute_extra_params(
    interval: str, minutes: int, include_past: bool
) -> dict[str, str] | None:
    """분별 조회의 추가 파라미터(간격 초 + 과거포함). 분별이 아니면 ``None``."""
    if interval != "minute":
        return None
    try:
        span = _MINUTE_SPAN_SECONDS[minutes]
    except KeyError:
        raise KISUsageError(f"minutes 는 1/3/5/10/30/60 중 하나: {minutes!r}") from None
    return {"FID_HOUR_CLS_CODE": span, "FID_PW_DATA_INCU_YN": "Y" if include_past else "N"}


def _spec_has_time(row: Mapping[str, Any], spec: _TrendSpec) -> bool:
    key = spec.time_key or spec.date_key
    return bool(key) and bool(str(row.get(key, "")).strip())


def _spec_timestamp(row: Mapping[str, Any], *, as_of: datetime, spec: _TrendSpec) -> datetime:
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


def _row_change(
    row: Mapping[str, Any], spec: _TrendSpec
) -> tuple[Decimal | None, Decimal | None]:
    """전일대비/전일대비율(부호 복원). 전일대비 필드가 없는 축(분별/틱)은 ``(None, None)``."""
    if not spec.has_change:
        return None, None
    sign = str(row.get("prdy_vrss_sign", "")).strip()
    change = _apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign)
    change_percent = _apply_change_sign(
        required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
    )
    return change, change_percent


def _parse_volatility_row(
    row: Mapping[str, Any], *, code: str, as_of: datetime, spec: _TrendSpec
) -> ELWVolatilityPoint:
    change, change_percent = _row_change(row, spec)
    return ELWVolatilityPoint(
        code=code,
        timestamp=_spec_timestamp(row, as_of=as_of, spec=spec),
        price=required_decimal(row.get(spec.price_key), spec.price_key),
        implied_volatility=optional_decimal(row.get("hts_ints_vltl"), "hts_ints_vltl"),
        change=change,
        change_percent=change_percent,
        _raw=row,
    )


_INDICATOR_SPEC = {
    "trade": _TrendSpec(
        "/uapi/elw/v1/quotations/indicator-trend-ccnl", "FHPEW02740100",
        "elw_prpr", None, "stck_cntg_hour", True,
    ),
    "day": _TrendSpec(
        "/uapi/elw/v1/quotations/indicator-trend-daily", "FHPEW02740200",
        "elw_prpr", "stck_bsop_date", None, True,
    ),
    "minute": _TrendSpec(
        "/uapi/elw/v1/quotations/indicator-trend-minute", "FHPEW02740300",
        "elw_prpr", "stck_bsop_date", "stck_cntg_hour", False,
    ),
}


def fetch_indicator_trend(
    transport: Transport, *, code: str, interval: TrendInterval = "day",
    minutes: int = 1, include_past: bool = False,
) -> list[ELWIndicatorPoint]:
    """ELW 투자지표 추이. ``interval`` 은 체결/일별/분별(틱 미지원).

    ``minutes``/``include_past`` 는 분별에서만 쓴다. 레버리지/기어링/내재가치/패리티만 매핑하고
    나머지(시간가치·프리미엄·자본지지점 근접률·OHLC)는 ``_raw`` 에 있다(축마다 부가 필드가 다름)."""
    spec = _pick_spec(_INDICATOR_SPEC, interval, "투자지표 추이")
    extra = _minute_extra_params(interval, minutes, include_past)
    rows = _fetch_trend_rows(transport, path=spec.path, tr=spec.tr, code=code, extra_params=extra)
    as_of = datetime.now(_KST)
    return [
        _parse_indicator_row(row, code=code, as_of=as_of, spec=spec)
        for row in rows
        if _spec_has_time(row, spec)
    ]


def _parse_indicator_row(
    row: Mapping[str, Any], *, code: str, as_of: datetime, spec: _TrendSpec
) -> ELWIndicatorPoint:
    change, change_percent = _row_change(row, spec)
    return ELWIndicatorPoint(
        code=code,
        timestamp=_spec_timestamp(row, as_of=as_of, spec=spec),
        price=required_decimal(row.get(spec.price_key), spec.price_key),
        leverage=optional_decimal(row.get("lvrg_val"), "lvrg_val"),
        gearing=optional_decimal(row.get("gear"), "gear"),
        intrinsic_value=optional_decimal(row.get("invl_val"), "invl_val"),
        parity=optional_decimal(row.get("prit"), "prit"),
        change=change,
        change_percent=change_percent,
        _raw=row,
    )


_LP_TREND_PATH = "/uapi/elw/v1/quotations/lp-trade-trend"
_LP_TREND_TR = "FHPEW03760000"


def fetch_lp_trend(transport: Transport, *, code: str) -> list[ELWLpFlow]:
    """ELW 의 일별 LP(유동성공급자) 매매 흐름(최신순). ``code`` 는 ELW 표준코드.

    응답의 ``output2`` 가 일별 LP 매매내역이다(``output1`` 은 현재 요약이라 다루지 않는다 -- 레버리지/
    패리티 등은 :meth:`~kis_openapi.elw.ELW.indicator_trend` 로 얻는다)."""
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_LP_TREND_PATH, tr_id=_LP_TREND_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(rows, list):             # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output2", resp)
    return [
        _parse_lp_row(row, code=code)
        for row in rows
        if str(row.get("stck_bsop_date", "")).strip()
    ]


def _parse_lp_row(row: Mapping[str, Any], *, code: str) -> ELWLpFlow:
    sign = str(row.get("prdy_vrss_sign", "")).strip()
    return ELWLpFlow(
        code=code,
        timestamp=_parse_bar_timestamp(str(row.get("stck_bsop_date", "")).strip()),
        price=required_decimal(row.get("elw_prpr"), "elw_prpr"),
        change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
        change_percent=_apply_change_sign(
            required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
        ),
        lp_buy_quantity=required_int(row.get("lp_shnu_qty"), "lp_shnu_qty"),
        lp_buy_avg_price=optional_decimal(row.get("lp_shnu_avrg_unpr"), "lp_shnu_avrg_unpr"),
        lp_sell_quantity=required_int(row.get("lp_seln_qty"), "lp_seln_qty"),
        lp_sell_avg_price=optional_decimal(row.get("lp_seln_avrg_unpr"), "lp_seln_avrg_unpr"),
        lp_holding_quantity=required_int(row.get("lp_hvol"), "lp_hvol"),
        lp_holding_rate=optional_decimal(row.get("lp_hldn_rate"), "lp_hldn_rate"),
        _raw=row,
    )


# --- 시장 전체 ELW 순위 -----------------------------------------------------
# ELW 순위는 필터 파라미터가 많고(기초자산/발행사/콜풋/가격/거래량 범위/정렬/소속) 순위마다 필요한
# 파라미터 집합이 조금씩 다르다. KIS 는 누락/불필요 파라미터에 민감하므로 순위별로 정확히 보낸다.
# 출력 행은 공통 축(코드/이름/가격/전일대비/거래량)만 매핑하고 순위 고유지표는 _raw 에 둔다.

#: 콜풋 구분(FID_DIV_CLS_CODE): 전체/콜/풋.
_RIGHT_CODE = {"all": "0", "call": "1", "put": "2"}

_VOLUME_SORT = {
    "volume": "0", "turnover_growth": "1", "turnover_rate": "2",
    "amount": "3", "net_buy_balance": "4", "net_sell_balance": "5",
}
_CHANGE_SORT = {
    "gainers": "0", "losers": "1", "from_open_up": "2", "from_open_down": "3",
    "fluctuation": "4",
}
_SENSITIVITY_SORT = {
    "theoretical": "0", "delta": "1", "gamma": "2", "rho": "3", "vega": "4",
    "implied_volatility": "6", "hist_volatility": "7",
}
_INDICATOR_SORT = {
    "conversion_ratio": "0", "leverage": "1", "strike": "2", "intrinsic_value": "3",
    "time_value": "4",
}
_QUICK_CHANGE_SORT = {
    "price_surge": "1", "price_plunge": "2", "volume_surge": "3",
    "bid_surge": "4", "ask_surge": "5",
}


def _code_of(value: str, table: Mapping[str, str], argname: str) -> str:
    try:
        return table[value]
    except KeyError:
        raise KISUsageError(f"{argname} 는 {sorted(table)} 중 하나: {value!r}") from None


def _fetch_ranking(
    transport: Transport, *, path: str, tr: str, params: Mapping[str, str]
) -> list[RankedELW]:
    resp = transport.request(
        method="GET", path=path, tr_id=tr, params=dict(params), idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):             # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    ranked: list[RankedELW] = []
    for row in rows:
        code = str(row.get("elw_shrn_iscd", "")).strip()
        if not code:                           # 빈 행 건너뜀
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        ranked.append(
            RankedELW(
                rank=len(ranked) + 1,          # 응답 순서 기반 1-베이스 순위
                symbol=code,
                name=str(row.get("elw_kor_isnm") or row.get("hts_kor_isnm") or "").strip(),
                price=required_decimal(row.get("elw_prpr"), "elw_prpr"),
                change=_apply_change_sign(
                    required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                _raw=row,
            )
        )
    return ranked


def _base_ranking_params(scr: str, underlying: str, issuer: str) -> dict[str, str]:
    return {
        "FID_COND_MRKT_DIV_CODE": _MARKET_DIV,
        "FID_COND_SCR_DIV_CODE": scr,
        "FID_UNAS_INPUT_ISCD": underlying,
        "FID_INPUT_ISCD": issuer,
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_INPUT_VOL_1": "", "FID_INPUT_VOL_2": "",
        "FID_BLNG_CLS_CODE": "0",
    }


def fetch_ranking_by_volume(
    transport: Transport, *, sort: str = "volume",
    underlying: str = "000000", issuer: str = "00000", right: str = "all",
) -> list[RankedELW]:
    """ELW 거래량 순위. ``sort`` 는 volume/turnover_growth/turnover_rate/amount/
    net_buy_balance/net_sell_balance."""
    params = _base_ranking_params("20278", underlying, issuer)
    params.update({
        "FID_INPUT_RMNN_DYNU_1": "",
        "FID_DIV_CLS_CODE": _code_of(right, _RIGHT_CODE, "right"),
        "FID_INPUT_DATE_1": "",
        "FID_RANK_SORT_CLS_CODE": _code_of(sort, _VOLUME_SORT, "sort"),
        "FID_INPUT_ISCD_2": "0000",
        "FID_INPUT_DATE_2": "",
    })
    return _fetch_ranking(
        transport, path="/uapi/elw/v1/ranking/volume-rank", tr="FHPEW02780000", params=params
    )


def fetch_ranking_by_change(
    transport: Transport, *, sort: str = "gainers",
    underlying: str = "000000", issuer: str = "00000", right: str = "all",
) -> list[RankedELW]:
    """ELW 등락률 순위. ``sort`` 는 gainers/losers/from_open_up/from_open_down/fluctuation."""
    params = _base_ranking_params("20277", underlying, issuer)
    params.update({
        "FID_INPUT_RMNN_DYNU_1": "",
        "FID_DIV_CLS_CODE": _code_of(right, _RIGHT_CODE, "right"),
        "FID_INPUT_DATE_1": "",
        "FID_RANK_SORT_CLS_CODE": _code_of(sort, _CHANGE_SORT, "sort"),
        "FID_INPUT_DATE_2": "",
    })
    return _fetch_ranking(
        transport, path="/uapi/elw/v1/ranking/updown-rate", tr="FHPEW02770000", params=params
    )


def fetch_ranking_by_sensitivity(
    transport: Transport, *, sort: str = "delta",
    underlying: str = "000000", issuer: str = "00000", right: str = "all",
) -> list[RankedELW]:
    """ELW 민감도 순위. ``sort`` 는 theoretical/delta/gamma/rho/vega/implied_volatility/
    hist_volatility. 그릭스 등 지표는 각 행의 ``_raw`` 에 있다."""
    params = _base_ranking_params("20285", underlying, issuer)
    params.update({
        "FID_DIV_CLS_CODE": _code_of(right, _RIGHT_CODE, "right"),
        "FID_RANK_SORT_CLS_CODE": _code_of(sort, _SENSITIVITY_SORT, "sort"),
        "FID_INPUT_RMNN_DYNU_1": "",
        "FID_INPUT_DATE_1": "",
    })
    return _fetch_ranking(
        transport, path="/uapi/elw/v1/ranking/sensitivity", tr="FHPEW02850000", params=params
    )


def fetch_ranking_by_indicator(
    transport: Transport, *, sort: str = "leverage",
    underlying: str = "000000", issuer: str = "00000", right: str = "all",
) -> list[RankedELW]:
    """ELW 투자지표 순위. ``sort`` 는 conversion_ratio/leverage/strike/intrinsic_value/
    time_value. 레버리지 등 지표는 각 행의 ``_raw`` 에 있다."""
    params = _base_ranking_params("20279", underlying, issuer)
    params.update({
        "FID_DIV_CLS_CODE": _code_of(right, _RIGHT_CODE, "right"),
        "FID_RANK_SORT_CLS_CODE": _code_of(sort, _INDICATOR_SORT, "sort"),
    })
    return _fetch_ranking(
        transport, path="/uapi/elw/v1/ranking/indicator", tr="FHPEW02790000", params=params
    )


def fetch_ranking_quick_change(
    transport: Transport, *, sort: str = "price_surge", window: str = "day",
    underlying: str = "000000", issuer: str = "00000",
) -> list[RankedELW]:
    """ELW 당일 급변 종목. ``sort`` 는 price_surge/price_plunge/volume_surge/bid_surge/
    ask_surge, ``window`` 는 ``"minute"``(분 기준)/``"day"``(일 기준). 콜풋 필터는 없다."""
    params = _base_ranking_params("20287", underlying, issuer)
    params.update({
        "FID_MRKT_CLS_CODE": "A",
        "FID_HOUR_CLS_CODE": _code_of(window, {"minute": "1", "day": "2"}, "window"),
        "FID_INPUT_HOUR_1": "", "FID_INPUT_HOUR_2": "",
        "FID_RANK_SORT_CLS_CODE": _code_of(sort, _QUICK_CHANGE_SORT, "sort"),
    })
    return _fetch_ranking(
        transport, path="/uapi/elw/v1/ranking/quick-change", tr="FHPEW02870000", params=params
    )
