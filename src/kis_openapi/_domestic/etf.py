"""ETF/ETN 시세 조회 (내부) -- NAV 등 ETF 고유 정보를 :class:`ETFNav` 로.

사용자면은 종목 핸들(:class:`~kis_openapi.ticker.Ticker`)의 ETF 전용 verb(``kis.ticker(code).nav()``)다.
ETF/ETN 은 종목처럼 거래되므로 시세/주문은 일반 verb 로 하고, 여기선 NAV/괴리율/추적오차 같은 ETF
고유 필드만 다룬다. 엔드포인트는 ``etfetn`` 세그먼트라 경로가 ``/uapi/etfetn/...`` 로 다르다.

KIS URL/TR-id (원장 대조):
- ETF/ETN 현재가(NAV 포함): ``GET /uapi/etfetn/v1/quotations/inquire-price`` ``FHPST02400000``
  (``FID_COND_MRKT_DIV_CODE=J``).
- ETF 구성종목시세: ``GET /uapi/etfetn/v1/quotations/inquire-component-stock-price`` ``FHKST121600C0``
  화면 11216 (output2 = 구성종목 목록).
- ETF NAV 비교추이(일): ``GET /uapi/etfetn/v1/quotations/nav-comparison-daily-trend`` ``FHPST02440200``
  (``FID_INPUT_DATE_1``~``FID_INPUT_DATE_2`` 기간).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from .._wire import required_decimal
from ..errors import KISUsageError
from ..etf_items import ETFComponent, ETFNav, ETFNavHistoryPoint
from ..transport import Transport
from .market_data import (
    _KST,
    _apply_change_sign,
    _missing_block_error,
    _parse_kst_date,
    _raise_if_error,
    _to_yyyymmdd,
)

_ETF_NAV_PATH = "/uapi/etfetn/v1/quotations/inquire-price"
_ETF_NAV_TR = "FHPST02400000"
#: ETF/ETN 시세의 시장구분 코드(원장: 주식 J).
_ETF_MARKET_DIV = "J"

_ETF_COMPONENTS_PATH = "/uapi/etfetn/v1/quotations/inquire-component-stock-price"
_ETF_COMPONENTS_TR = "FHKST121600C0"
_ETF_COMPONENTS_SCR = "11216"

_ETF_NAV_HISTORY_PATH = "/uapi/etfetn/v1/quotations/nav-comparison-daily-trend"
_ETF_NAV_HISTORY_TR = "FHPST02440200"


def fetch_etf_nav(transport: Transport, *, symbol: str) -> ETFNav:
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


def fetch_etf_components(transport: Transport, *, symbol: str) -> list[ETFComponent]:
    """ETF 구성종목(PDF) 목록. ``symbol`` 이 ETF 가 아니면 서버가 거부한다. output2를
    :class:`ETFComponent` 리스트로 돌려준다."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _ETF_MARKET_DIV,
        "FID_INPUT_ISCD": symbol,
        "FID_COND_SCR_DIV_CODE": _ETF_COMPONENTS_SCR,
    }
    resp = transport.request(
        method="GET", path=_ETF_COMPONENTS_PATH, tr_id=_ETF_COMPONENTS_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")            # output1=ETF 요약, output2=구성종목 목록
    if not isinstance(rows, list):             # 성공 응답인데 목록 아님 -> fail-closed
        raise _missing_block_error("output2", resp)
    return _parse_etf_components(rows)


def _parse_etf_components(rows: Sequence[Mapping[str, Any]]) -> list[ETFComponent]:
    components: list[ETFComponent] = []
    for row in rows:
        symbol = str(row.get("stck_shrn_iscd", "")).strip()
        if not symbol:                         # 빈 행 skip
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        components.append(
            ETFComponent(
                symbol=symbol,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                weight=required_decimal(row.get("etf_cnfg_issu_rlim"), "etf_cnfg_issu_rlim"),
                valuation=required_decimal(row.get("etf_vltn_amt"), "etf_vltn_amt"),
                _raw=row,
            )
        )
    return components


def fetch_etf_nav_history(
    transport: Transport, *, symbol: str, start: str | date, end: str | date
) -> list[ETFNavHistoryPoint]:
    """일별 NAV-가격 추이. ``start``/``end`` 는 조회 기간(YYYYMMDD 또는 date). 각 거래일의 종가·NAV·
    괴리율을 :class:`ETFNavHistoryPoint` 리스트(과거->현재)로. KIS 가 한 번에 주는 창만 돌려준다."""
    start_date = _to_yyyymmdd(start, "start")
    end_date = _to_yyyymmdd(end, "end")
    if start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    params = {
        "FID_COND_MRKT_DIV_CODE": _ETF_MARKET_DIV,
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
    }
    resp = transport.request(
        method="GET", path=_ETF_NAV_HISTORY_PATH, tr_id=_ETF_NAV_HISTORY_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):             # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_etf_nav_history(rows)


def _parse_etf_nav_history(rows: Sequence[Mapping[str, Any]]) -> list[ETFNavHistoryPoint]:
    """일별 NAV 추이 행 -> ETFNavHistoryPoint(거래일 오름차순)."""
    points: list[ETFNavHistoryPoint] = []
    for row in rows:
        date_text = str(row.get("stck_bsop_date", "")).strip()
        close_text = str(row.get("stck_clpr", "")).strip()
        if not date_text or not close_text:    # 빈 행 skip
            continue
        sign = str(row.get("nav_prdy_vrss_sign", "")).strip()
        points.append(
            ETFNavHistoryPoint(
                date=_parse_kst_date(date_text),
                close=required_decimal(close_text, "stck_clpr"),
                nav=required_decimal(row.get("nav"), "nav"),
                nav_change=_apply_change_sign(
                    required_decimal(row.get("nav_prdy_vrss"), "nav_prdy_vrss"), sign
                ),
                nav_change_percent=_apply_change_sign(
                    required_decimal(row.get("nav_prdy_ctrt"), "nav_prdy_ctrt"), sign
                ),
                premium=required_decimal(row.get("dprt"), "dprt"),
                _raw=row,
            )
        )
    points.sort(key=lambda p: p.date)          # 과거->현재
    return points


def _parse_etf_nav(output: Mapping[str, Any], *, symbol: str, as_of: datetime) -> ETFNav:
    sign = str(output.get("nav_prdy_vrss_sign", "")).strip()
    return ETFNav(
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
