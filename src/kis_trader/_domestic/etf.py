"""ETF/ETN 시세 조회 (내부) -- NAV 등 ETF 고유 정보를 :class:`ETFNAV` 로.

사용자면은 종목 핸들(:class:`~kis_trader.stock.DomesticStock`)의 ETF 전용 verb(``kis.domestic.stock(code).nav()``)다.
ETF/ETN 은 종목처럼 거래되므로 시세/주문은 일반 verb 로 하고, 여기선 NAV/괴리율/추적오차 같은 ETF
고유 필드만 다룬다. 엔드포인트는 ``etfetn`` 세그먼트라 경로가 ``/uapi/etfetn/...`` 로 다르다.

KIS URL/TR-ID (KIS 명세 대조):
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

from .._bars import _parse_minute_bar_timestamp
from .._internal._datetime import (
    _KST,
    _parse_intraday_timestamp,
    _parse_kst_date,
    _to_yyyymmdd,
    _today_kst,
)
from .._internal._response import (
    _missing_block_error,
    _raise_if_error,
    _require_mapping_rows,
)
from .._internal._wire import (
    _apply_change_sign,
    optional_decimal,
    optional_int,
    required_decimal,
    required_int,
)
from ..errors import KISUsageError
from ..etf_items import (
    ETFNAV,
    ETFComponent,
    ETFComponents,
    ETFComponentsSummary,
    ETFNAVComparison,
    ETFNAVHistoryPoint,
    ETFNAVMinutePoint,
    ETFOrderBook,
)
from ..order_book import OrderBook, PriceLevel
from ..transport import Transport

_ETF_NAV_PATH = "/uapi/etfetn/v1/quotations/inquire-price"
_ETF_NAV_TR = "FHPST02400000"
#: ETF/ETN 시세의 시장구분 코드(KIS 코드표: 주식 J).
_ETF_MARKET_DIV = "J"

_ETF_COMPONENTS_PATH = "/uapi/etfetn/v1/quotations/inquire-component-stock-price"
_ETF_COMPONENTS_TR = "FHKST121600C0"
_ETF_COMPONENTS_SCR = "11216"

_ETF_NAV_HISTORY_PATH = "/uapi/etfetn/v1/quotations/nav-comparison-daily-trend"
_ETF_NAV_HISTORY_TR = "FHPST02440200"
_ETF_NAV_COMPARISON_PATH = "/uapi/etfetn/v1/quotations/nav-comparison-trend"
_ETF_NAV_COMPARISON_TR = "FHPST02440000"
_ETF_NAV_MINUTE_PATH = "/uapi/etfetn/v1/quotations/nav-comparison-time-trend"
_ETF_NAV_MINUTE_TR = "FHPST02440100"
_ETF_ORDER_BOOK_PATH = "/uapi/etfetn/v1/quotations/inquire-asking-price"
_ETF_ORDER_BOOK_TR = "FHPST02400200"


def fetch_etf_nav(transport: Transport, *, symbol: str) -> ETFNAV:
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


def fetch_etf_nav_comparison(
    transport: Transport, *, symbol: str
) -> ETFNAVComparison:
    """ETF 시장가격과 NAV의 당일 OHLC 비교."""
    resp = transport.request(
        method="GET",
        path=_ETF_NAV_COMPARISON_PATH,
        tr_id=_ETF_NAV_COMPARISON_TR,
        params={"FID_COND_MRKT_DIV_CODE": _ETF_MARKET_DIV, "FID_INPUT_ISCD": symbol},
        idempotent=True,
    )
    _raise_if_error(resp)
    price = resp.body.get("output1")
    nav = resp.body.get("output2")
    if not isinstance(price, Mapping):
        raise _missing_block_error("output1", resp)
    if not isinstance(nav, Mapping):
        raise _missing_block_error("output2", resp)
    price_sign = str(price.get("prdy_vrss_sign", "")).strip()
    nav_sign = str(nav.get("nav_prdy_vrss_sign", "")).strip()
    return ETFNAVComparison(
        symbol=symbol,
        price=required_decimal(price.get("stck_prpr"), "stck_prpr"),
        previous_close=required_decimal(price.get("stck_prdy_clpr"), "stck_prdy_clpr"),
        open=required_decimal(price.get("stck_oprc"), "stck_oprc"),
        high=required_decimal(price.get("stck_hgpr"), "stck_hgpr"),
        low=required_decimal(price.get("stck_lwpr"), "stck_lwpr"),
        change=_apply_change_sign(
            required_decimal(price.get("prdy_vrss"), "prdy_vrss"), price_sign
        ),
        change_percent=_apply_change_sign(
            required_decimal(price.get("prdy_ctrt"), "prdy_ctrt"), price_sign
        ),
        volume=required_int(price.get("acml_vol"), "acml_vol"),
        cumulative_trading_amount=required_decimal(price.get("acml_tr_pbmn"), "acml_tr_pbmn"),
        nav=required_decimal(nav.get("nav"), "nav"),
        previous_nav=required_decimal(nav.get("prdy_clpr_nav"), "prdy_clpr_nav"),
        nav_open=required_decimal(nav.get("oprc_nav"), "oprc_nav"),
        nav_high=required_decimal(nav.get("hprc_nav"), "hprc_nav"),
        nav_low=required_decimal(nav.get("lprc_nav"), "lprc_nav"),
        nav_change=_apply_change_sign(
            required_decimal(nav.get("nav_prdy_vrss"), "nav_prdy_vrss"), nav_sign
        ),
        nav_change_percent=_apply_change_sign(
            required_decimal(nav.get("nav_prdy_ctrt"), "nav_prdy_ctrt"), nav_sign
        ),
        _raw=resp.body,
    )


def fetch_etf_nav_intraday(
    transport: Transport, *, symbol: str, interval_minutes: int
) -> list[ETFNAVMinutePoint]:
    """최근 30개 ETF 시장가격-NAV 분별 비교."""
    if not 1 <= interval_minutes <= 120:
        raise KISUsageError(
            f"interval_minutes 는 1~120 사이 정수여야 한다: {interval_minutes!r}"
        )
    resp = transport.request(
        method="GET",
        path=_ETF_NAV_MINUTE_PATH,
        tr_id=_ETF_NAV_MINUTE_TR,
        params={
            "fid_hour_cls_code": str(interval_minutes * 60),
            "fid_cond_mrkt_div_code": "E",
            "fid_input_iscd": symbol,
        },
        idempotent=True,
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output", resp)
    date_text = _today_kst()
    points: list[ETFNAVMinutePoint] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output[]", resp)
        time_text = str(row.get("bsop_hour", "")).strip()
        if not time_text:
            continue
        nav_sign = str(row.get("nav_prdy_vrss_sign", "")).strip()
        price_sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            ETFNAVMinutePoint(
                timestamp=_parse_minute_bar_timestamp(date_text=date_text, time_text=time_text),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(
                    required_decimal(row.get("prdy_vrss"), "prdy_vrss"), price_sign
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), price_sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                interval_volume=required_int(row.get("cntg_vol"), "cntg_vol"),
                nav=required_decimal(row.get("nav"), "nav"),
                nav_change=_apply_change_sign(
                    required_decimal(row.get("nav_prdy_vrss"), "nav_prdy_vrss"), nav_sign
                ),
                nav_change_percent=_apply_change_sign(
                    required_decimal(row.get("nav_prdy_ctrt"), "nav_prdy_ctrt"), nav_sign
                ),
                price_minus_nav=required_decimal(row.get("nav_vrss_prpr"), "nav_vrss_prpr"),
                premium=required_decimal(row.get("dprt"), "dprt"),
                _raw=row,
            )
        )
    points.sort(key=lambda point: point.timestamp)
    return points


def fetch_etf_order_book(transport: Transport, *, symbol: str) -> ETFOrderBook:
    """ETF 10단계 호가와 LP 잔량·잔량 증감·중간가."""
    resp = transport.request(
        method="GET",
        path=_ETF_ORDER_BOOK_PATH,
        tr_id=_ETF_ORDER_BOOK_TR,
        params={"FID_COND_MRKT_DIV_CODE": _ETF_MARKET_DIV, "FID_INPUT_ISCD": symbol},
        idempotent=True,
    )
    _raise_if_error(resp)
    row = resp.body.get("output")
    if not isinstance(row, Mapping):
        raise _missing_block_error("output", resp)

    def levels(price_prefix: str, quantity_prefix: str) -> tuple[PriceLevel, ...]:
        return tuple(
            PriceLevel(
                price=required_decimal(row.get(f"{price_prefix}{position}"), f"{price_prefix}{position}"),
                quantity=required_int(
                    row.get(f"{quantity_prefix}{position}"), f"{quantity_prefix}{position}"
                ),
            )
            for position in range(1, 11)
        )

    bids = levels("bidp", "bidp_rsqn")
    asks = levels("askp", "askp_rsqn")
    lp_bids = tuple(
        PriceLevel(price=bids[position - 1].price, quantity=required_int(
            row.get(f"lp_bidp_rsqn{position}"), f"lp_bidp_rsqn{position}"
        )) for position in range(1, 11)
    )
    lp_asks = tuple(
        PriceLevel(price=asks[position - 1].price, quantity=required_int(
            row.get(f"lp_askp_rsqn{position}"), f"lp_askp_rsqn{position}"
        )) for position in range(1, 11)
    )
    as_of = _parse_intraday_timestamp(
        str(row.get("aspr_acpt_hour", "")).strip(),
        _parse_minute_bar_timestamp(date_text=_today_kst(), time_text="000000"),
    )
    order_book = OrderBook(
        symbol=symbol,
        market="KRX",
        bids=bids,
        asks=asks,
        total_bid_quantity=required_int(row.get("total_bidp_rsqn"), "total_bidp_rsqn"),
        total_ask_quantity=required_int(row.get("total_askp_rsqn"), "total_askp_rsqn"),
        as_of=as_of,
        _raw=row,
    )
    return ETFOrderBook(
        order_book=order_book,
        lp_bids=lp_bids,
        lp_asks=lp_asks,
        bid_quantity_changes=tuple(required_int(
            row.get(f"bidp_rsqn_icdc{position}"), f"bidp_rsqn_icdc{position}"
        ) for position in range(1, 11)),
        ask_quantity_changes=tuple(required_int(
            row.get(f"askp_rsqn_icdc{position}"), f"askp_rsqn_icdc{position}"
        ) for position in range(1, 11)),
        lp_total_bid_quantity=required_int(row.get("lp_total_bidp_rsqn"), "lp_total_bidp_rsqn"),
        lp_total_ask_quantity=required_int(row.get("lp_total_askp_rsqn"), "lp_total_askp_rsqn"),
        total_bid_quantity_change=required_int(
            row.get("total_bidp_rsqn_icdc"), "total_bidp_rsqn_icdc"
        ),
        total_ask_quantity_change=required_int(
            row.get("total_askp_rsqn_icdc"), "total_askp_rsqn_icdc"
        ),
        midpoint=optional_decimal(row.get("mid_prc"), "mid_prc"),
        midpoint_quantity=optional_int(row.get("midp_total_rsqn"), "midp_total_rsqn"),
        midpoint_code=str(row.get("midp_cls_code", "")).strip(),
        _raw=row,
    )


def fetch_etf_components(transport: Transport, *, symbol: str) -> ETFComponents:
    """ETF 구성종목(PDF) 목록과 ETF 요약. ``symbol`` 이 ETF 가 아니면 서버가 거부한다. output1(ETF
    시세·NAV·구성 규모)을 :class:`ETFComponentsSummary` 로, output2(구성종목)를 :class:`ETFComponent`
    튜플로 담은 :class:`ETFComponents` 를 돌려준다. 두 블록 중 하나라도 없거나 형식이 어긋나면
    부분 결과 대신 fail-closed 한다."""
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
    summary_row = resp.body.get("output1")     # output1=ETF 요약
    rows = resp.body.get("output2")            # output2=구성종목 목록
    if not isinstance(summary_row, Mapping):   # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output1", resp)
    if not isinstance(rows, list):             # 성공 응답인데 목록 아님 -> fail-closed
        raise _missing_block_error("output2", resp)
    return ETFComponents(
        summary=_parse_etf_components_summary(summary_row),
        components=tuple(_parse_etf_components(rows)),
        _raw=resp.body,
    )


def _parse_etf_components_summary(row: Mapping[str, Any]) -> ETFComponentsSummary:
    price_sign = str(row.get("prdy_vrss_sign", "")).strip()
    nav_sign = str(row.get("nav_prdy_vrss_sign", "")).strip()
    return ETFComponentsSummary(
        price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
        change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), price_sign),
        change_percent=_apply_change_sign(
            required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), price_sign
        ),
        components_market_cap=required_decimal(
            row.get("etf_cnfg_issu_avls"), "etf_cnfg_issu_avls"
        ),
        nav=required_decimal(row.get("nav"), "nav"),
        nav_change=_apply_change_sign(
            required_decimal(row.get("nav_prdy_vrss"), "nav_prdy_vrss"), nav_sign
        ),
        nav_change_percent=_apply_change_sign(
            required_decimal(row.get("nav_prdy_ctrt"), "nav_prdy_ctrt"), nav_sign
        ),
        net_assets=required_decimal(row.get("etf_ntas_ttam"), "etf_ntas_ttam"),
        previous_nav=required_decimal(row.get("prdy_clpr_nav"), "prdy_clpr_nav"),
        nav_open=required_decimal(row.get("oprc_nav"), "oprc_nav"),
        nav_high=required_decimal(row.get("hprc_nav"), "hprc_nav"),
        nav_low=required_decimal(row.get("lprc_nav"), "lprc_nav"),
        cu_unit_shares=required_int(row.get("etf_cu_unit_scrt_cnt"), "etf_cu_unit_scrt_cnt"),
        component_count=required_int(row.get("etf_cnfg_issu_cnt"), "etf_cnfg_issu_cnt"),
        _raw=row,
    )


def _parse_etf_components(rows: Sequence[Mapping[str, Any]]) -> list[ETFComponent]:
    components: list[ETFComponent] = []
    for row in rows:
        symbol = str(row.get("stck_shrn_iscd", "")).strip()
        if not symbol:                         # 빈 행 skip
            continue
        change_sign_code = str(row.get("prdy_vrss_sign", "")).strip()
        components.append(
            ETFComponent(
                symbol=symbol,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), change_sign_code),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), change_sign_code
                ),
                weight=required_decimal(row.get("etf_cnfg_issu_rlim"), "etf_cnfg_issu_rlim"),
                valuation=required_decimal(row.get("etf_vltn_amt"), "etf_vltn_amt"),
                _raw=row,
            )
        )
    return components


def fetch_etf_nav_history(
    transport: Transport, *, symbol: str, start: str | date, end: str | date
) -> list[ETFNAVHistoryPoint]:
    """일별 NAV-가격 추이. ``start``/``end`` 는 조회 기간(YYYYMMDD 또는 date). 각 거래일의 종가·NAV·
    괴리율을 :class:`ETFNAVHistoryPoint` 리스트(과거->현재)로. KIS 가 한 번에 주는 창만 돌려준다."""
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


def _parse_etf_nav_history(rows: Sequence[Mapping[str, Any]]) -> list[ETFNAVHistoryPoint]:
    """일별 NAV 추이 행 -> ETFNAVHistoryPoint(거래일 오름차순)."""
    points: list[ETFNAVHistoryPoint] = []
    for row in rows:
        date_text = str(row.get("stck_bsop_date", "")).strip()
        close_text = str(row.get("stck_clpr", "")).strip()
        if not date_text or not close_text:    # 빈 행 skip
            continue
        change_sign_code = str(row.get("nav_prdy_vrss_sign", "")).strip()
        points.append(
            ETFNAVHistoryPoint(
                trading_date=_parse_kst_date(date_text),
                close=required_decimal(close_text, "stck_clpr"),
                nav=required_decimal(row.get("nav"), "nav"),
                nav_change=_apply_change_sign(
                    required_decimal(row.get("nav_prdy_vrss"), "nav_prdy_vrss"), change_sign_code
                ),
                nav_change_percent=_apply_change_sign(
                    required_decimal(row.get("nav_prdy_ctrt"), "nav_prdy_ctrt"), change_sign_code
                ),
                premium=required_decimal(row.get("dprt"), "dprt"),
                _raw=row,
            )
        )
    points.sort(key=lambda p: p.trading_date)  # 과거->현재
    return points


def _parse_etf_nav(output: Mapping[str, Any], *, symbol: str, as_of: datetime) -> ETFNAV:
    change_sign_code = str(output.get("nav_prdy_vrss_sign", "")).strip()
    return ETFNAV(
        symbol=symbol,
        nav=required_decimal(output.get("nav"), "nav"),
        nav_change=_apply_change_sign(
            required_decimal(output.get("nav_prdy_vrss"), "nav_prdy_vrss"), change_sign_code
        ),
        nav_change_percent=_apply_change_sign(
            required_decimal(output.get("nav_prdy_ctrt"), "nav_prdy_ctrt"), change_sign_code
        ),
        previous_nav=required_decimal(output.get("prdy_last_nav"), "prdy_last_nav"),
        premium=required_decimal(output.get("dprt"), "dprt"),
        tracking_error=required_decimal(output.get("trc_errt"), "trc_errt"),
        net_assets=required_decimal(output.get("etf_ntas_ttam"), "etf_ntas_ttam"),
        as_of=as_of,
        _raw=output,
    )
