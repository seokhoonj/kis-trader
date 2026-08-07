"""국내 지수/업종 시세 조회 (내부) -- 지수 현재가 등을 :class:`IndexQuote` 로.

사용자면(:class:`~kis_openapi.index.Index`, ``kis.index(code)``)이 호출한다. 지수/업종은 종목이
아니라 시장구분 ``U`` + 업종코드(``FID_INPUT_ISCD``)로 조회한다. 업종코드는 포털의 업종코드표를
따르며, 대표값은 0001 KOSPI 종합 / 1001 KOSDAQ 종합 / 2001 KOSPI200.

KIS URL/TR-id (원장 대조):
- 지수 현재가: ``GET .../quotations/inquire-index-price`` ``FHPUP02100000`` (``FID_COND_MRKT_DIV_CODE=U``).
- 지수 기간봉(일/주/월/년): ``GET .../quotations/inquire-daily-indexchartprice`` ``FHKUP03500100``
  (``FID_PERIOD_DIV_CODE`` D:일 W:주 M:월 Y:년). 종목 일봉과 페이지네이션은 같고 필드명만
  ``bstp_nmix_*`` 로 다르다 -- 공용 :func:`collect_period_bars` 재사용.
- 지수 분봉: ``GET .../quotations/inquire-time-indexchartprice`` ``FHKUP03500200``
  (``FID_INPUT_HOUR_1=60``, 과거 포함, 한 번에 최대 102건·연속조회 불가).
- 지수 시간대별: ``GET .../quotations/inquire-index-timeprice`` ``FHPUP02110200``
  (``FID_INPUT_HOUR_1`` 샘플 간격(초): 60=1분 300=5분 600=10분).
- 업종별 지수: ``GET .../quotations/inquire-index-category-price`` ``FHPUP02140000`` 화면 20214
  (``FID_MRKT_CLS_CODE`` K:거래소 Q:코스닥 K2:코스피200 -- 코드에서 추론, ``FID_BLNG_CLS_CODE`` 0:전업종).
  output1=시장 지수, output2=하위 업종 지수 목록. output2를 :class:`CategoryIndex` 로 돌려준다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from .._wire import required_decimal, required_int
from ..bar import Bar, Interval
from ..errors import KISUsageError
from ..index_items import CategoryIndex, IndexIntradayPoint, IndexQuote
from ..transport import RawResponse, Transport
from .market_data import (
    _KST,
    _apply_change_sign,
    _missing_block_error,
    _parse_bar_timestamp,
    _parse_minute_bar_timestamp,
    _period_code_for,
    _raise_if_error,
    _to_yyyymmdd,
    _today_kst,
    collect_period_bars,
)

_INDEX_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
_INDEX_QUOTE_TR = "FHPUP02100000"
#: 지수/업종 조회의 시장구분 코드(원장: 업종 U).
_INDEX_MARKET_DIV = "U"

_INDEX_BARS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
_INDEX_BARS_TR = "FHKUP03500100"
_INDEX_MINUTE_BARS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice"
_INDEX_MINUTE_BARS_TR = "FHKUP03500200"

_INDEX_INTRADAY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-timeprice"
_INDEX_INTRADAY_TR = "FHPUP02110200"
_INDEX_TICKS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-tickprice"
_INDEX_TICKS_TR = "FHPUP02110100"
#: 지수 시간대별 샘플 간격 -> FID_INPUT_HOUR_1(초). 원장: 60=1분, 300=5분, 600=10분.
_INDEX_INTRADAY_INTERVAL = {"1m": "60", "5m": "300", "10m": "600"}

_INDEX_CATEGORY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-category-price"
_INDEX_CATEGORY_TR = "FHPUP02140000"
_INDEX_CATEGORY_SCR = "20214"
#: 업종별 지수 시장구분(원장 FID_MRKT_CLS_CODE). 시장 지수 코드에서 추론한다.
_INDEX_CATEGORY_MARKET_CLASS = {"0001": "K", "1001": "Q", "2001": "K2"}


def fetch_index_quote(transport: Transport, *, code: str) -> IndexQuote:
    """지수(업종) 현재가 스냅샷. ``code`` 는 업종코드(예: 0001 KOSPI)."""
    params = {"FID_COND_MRKT_DIV_CODE": _INDEX_MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_INDEX_QUOTE_PATH, tr_id=_INDEX_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_index_quote(output, code=code, as_of=datetime.now(_KST))


def fetch_index_bars(
    transport: Transport,
    *,
    code: str,
    interval: Interval = "1d",
    start: str | date | None = None,
    end: str | date | None = None,
    max_bars: int | None = None,
) -> list[Bar]:
    """지수 봉을 과거->현재 오름차순으로.

    ``1m`` 은 최근 분봉 최대 102건(연속조회 불가)이며 ``start``/``end`` 를 주면 응답 안에서 날짜를
    거른다. ``1d``/``1wk``/``1mo`` 는 ``start`` 가 필요한 기간봉이다. 지수엔 수정주가 개념이 없다.
    """
    if max_bars is not None and max_bars <= 0:
        raise KISUsageError(f"max_bars 는 양의 정수여야 한다: {max_bars}")
    if interval == "1m":
        return _fetch_index_minute_bars(
            transport, code=code, start=start, end=end, max_bars=max_bars
        )
    if start is None:
        raise KISUsageError(f"interval={interval!r}(기간봉)에는 start 가 필요하다.")
    period = _period_code_for(interval)
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _to_yyyymmdd(start, "start")
    if start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    base_params = {
        "FID_COND_MRKT_DIV_CODE": _INDEX_MARKET_DIV,
        "FID_INPUT_ISCD": code,
        "FID_PERIOD_DIV_CODE": period,
    }
    return collect_period_bars(
        transport, path=_INDEX_BARS_PATH, tr=_INDEX_BARS_TR, base_params=base_params,
        start_date=start_date, end_date=end_date, max_bars=max_bars,
        parse_rows=lambda rows: _parse_index_bars(rows, code=code),
    )


def _fetch_index_minute_bars(
    transport: Transport, *, code: str, start: str | date | None,
    end: str | date | None, max_bars: int | None,
) -> list[Bar]:
    """최근 지수 1분봉 한 페이지를 조회하고 날짜 범위·최근 건수를 적용한다."""
    start_date = None if start is None else _to_yyyymmdd(start, "start")
    end_date = None if end is None else _to_yyyymmdd(end, "end")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    params = {
        "FID_COND_MRKT_DIV_CODE": _INDEX_MARKET_DIV,
        "FID_ETC_CLS_CODE": "0",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": "60",
        "FID_PW_DATA_INCU_YN": "Y",
    }
    resp = transport.request(
        method="GET", path=_INDEX_MINUTE_BARS_PATH, tr_id=_INDEX_MINUTE_BARS_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(rows, list):
        raise _missing_block_error("output2", resp)
    bars = _parse_index_minute_bars(rows, code=code, resp=resp)
    if start_date is not None:
        bars = [bar for bar in bars if f"{bar.timestamp:%Y%m%d}" >= start_date]
    if end_date is not None:
        bars = [bar for bar in bars if f"{bar.timestamp:%Y%m%d}" <= end_date]
    if max_bars is not None:
        bars = bars[-max_bars:]
    return bars


def _parse_index_minute_bars(
    rows: Sequence[object], *, code: str, resp: RawResponse
) -> list[Bar]:
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output2[]", resp)
        date_text = str(row.get("stck_bsop_date", "")).strip()
        time_text = str(row.get("stck_cntg_hour", "")).strip()
        close_text = str(row.get("bstp_nmix_prpr", "")).strip()
        if not date_text or not time_text or not close_text:
            continue
        if (
            len(time_text) != 6
            or not time_text.isdigit()
            or int(time_text[:2]) > 23
            or int(time_text[2:4]) > 59
            or int(time_text[4:]) > 59
        ):
            continue
        bars.append(
            Bar(
                symbol=code,
                timestamp=_parse_minute_bar_timestamp(date_text, time_text),
                open=required_decimal(row.get("bstp_nmix_oprc"), "bstp_nmix_oprc"),
                high=required_decimal(row.get("bstp_nmix_hgpr"), "bstp_nmix_hgpr"),
                low=required_decimal(row.get("bstp_nmix_lwpr"), "bstp_nmix_lwpr"),
                close=required_decimal(close_text, "bstp_nmix_prpr"),
                volume=required_int(row.get("cntg_vol"), "cntg_vol"),
                _raw=row,
            )
        )
    bars.sort(key=lambda bar: bar.timestamp)
    return bars


def _parse_index_bars(rows: Sequence[Mapping[str, Any]], *, code: str) -> list[Bar]:
    """지수 일봉 행 -> Bar. 종가/시고저가 ``bstp_nmix_*`` 로 종목(stck_*)과 필드명이 다르다."""
    bars: list[Bar] = []
    for row in rows:
        date_text = str(row.get("stck_bsop_date", "")).strip()
        close_text = str(row.get("bstp_nmix_prpr", "")).strip()
        if not date_text or not close_text:    # 미개장 세션의 빈 바 skip
            continue
        bars.append(
            Bar(
                symbol=code,
                timestamp=_parse_bar_timestamp(date_text),
                open=required_decimal(row.get("bstp_nmix_oprc"), "bstp_nmix_oprc"),
                high=required_decimal(row.get("bstp_nmix_hgpr"), "bstp_nmix_hgpr"),
                low=required_decimal(row.get("bstp_nmix_lwpr"), "bstp_nmix_lwpr"),
                close=required_decimal(close_text, "bstp_nmix_prpr"),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                _raw=row,
            )
        )
    return bars


def fetch_index_intraday(
    transport: Transport, *, code: str, interval: str
) -> list[IndexIntradayPoint]:
    """지수 당일 시간대별 시계열. ``interval`` 은 샘플 간격 ``1m``/``5m``/``10m``. 과거->현재
    오름차순으로 :class:`IndexIntradayPoint` 리스트를 돌려준다."""
    seconds = _INDEX_INTRADAY_INTERVAL.get(interval)
    if seconds is None:
        raise KISUsageError(f'interval 은 "1m"/"5m"/"10m" 중 하나여야 한다: {interval!r}')
    params = {
        "FID_INPUT_HOUR_1": seconds,
        "FID_INPUT_ISCD": code,
        "FID_COND_MRKT_DIV_CODE": _INDEX_MARKET_DIV,
    }
    resp = transport.request(
        method="GET", path=_INDEX_INTRADAY_PATH, tr_id=_INDEX_INTRADAY_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):             # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_index_intraday(rows, today=_today_kst())


def fetch_index_ticks(transport: Transport, *, code: str) -> list[IndexIntradayPoint]:
    """지수 당일 10초 시계열을 과거->현재 오름차순으로 조회한다."""
    resp = transport.request(
        method="GET",
        path=_INDEX_TICKS_PATH,
        tr_id=_INDEX_TICKS_TR,
        params={
            "FID_INPUT_ISCD": code,
            "FID_COND_MRKT_DIV_CODE": _INDEX_MARKET_DIV,
        },
        idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    points: list[IndexIntradayPoint] = []
    today = _today_kst()
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output[]", resp)
        time_text = str(row.get("stck_cntg_hour", "")).strip()
        value_text = str(row.get("bstp_nmix_prpr", "")).strip()
        if not time_text or not value_text:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            IndexIntradayPoint(
                time=_parse_minute_bar_timestamp(today, time_text),
                value=required_decimal(value_text, "bstp_nmix_prpr"),
                change=_apply_change_sign(
                    required_decimal(
                        row.get("bstp_nmix_prdy_vrss"), "bstp_nmix_prdy_vrss"
                    ),
                    sign,
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                interval_volume=required_int(row.get("cntg_vol"), "cntg_vol"),
                _raw=row,
            )
        )
    points.sort(key=lambda point: point.time)
    return points


def _parse_index_intraday(
    rows: Sequence[Mapping[str, Any]], *, today: str
) -> list[IndexIntradayPoint]:
    """시간대별 행 -> IndexIntradayPoint(시각 오름차순). bsop_hour(HHMMSS)에 당일 날짜를 결합한다."""
    points: list[IndexIntradayPoint] = []
    for row in rows:
        time_text = str(row.get("bsop_hour", "")).strip()
        value_text = str(row.get("bstp_nmix_prpr", "")).strip()
        if not time_text or not value_text:    # 빈 점 skip
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            IndexIntradayPoint(
                time=_parse_minute_bar_timestamp(today, time_text),
                value=required_decimal(value_text, "bstp_nmix_prpr"),
                change=_apply_change_sign(
                    required_decimal(row.get("bstp_nmix_prdy_vrss"), "bstp_nmix_prdy_vrss"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                interval_volume=required_int(row.get("cntg_vol"), "cntg_vol"),
                _raw=row,
            )
        )
    points.sort(key=lambda p: p.time)          # 과거->현재
    return points


def fetch_index_categories(transport: Transport, *, code: str) -> list[CategoryIndex]:
    """시장(``code``)의 하위 업종 지수 목록. ``code`` 는 시장 지수(0001 KOSPI/1001 KOSDAQ/2001
    KOSPI200)여야 하며 시장구분(K/Q/K2)을 여기서 추론한다. output2를 :class:`CategoryIndex` 로."""
    market_class = _INDEX_CATEGORY_MARKET_CLASS.get(code)
    if market_class is None:
        raise KISUsageError(
            "categories 는 시장 지수(0001 KOSPI / 1001 KOSDAQ / 2001 KOSPI200)에만 쓴다: "
            f"{code!r}"
        )
    params = {
        "FID_COND_MRKT_DIV_CODE": _INDEX_MARKET_DIV,
        "FID_INPUT_ISCD": code,
        "FID_COND_SCR_DIV_CODE": _INDEX_CATEGORY_SCR,
        "FID_MRKT_CLS_CODE": market_class,
        "FID_BLNG_CLS_CODE": "0",               # 전업종
    }
    resp = transport.request(
        method="GET", path=_INDEX_CATEGORY_PATH, tr_id=_INDEX_CATEGORY_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")             # output1=시장 지수, output2=하위 업종 목록
    if not isinstance(rows, list):              # 성공 응답인데 목록 아님 -> fail-closed
        raise _missing_block_error("output2", resp)
    return _parse_index_categories(rows)


def _parse_index_categories(rows: Sequence[Mapping[str, Any]]) -> list[CategoryIndex]:
    categories: list[CategoryIndex] = []
    for row in rows:
        category_code = str(row.get("bstp_cls_code", "")).strip()
        value_text = str(row.get("bstp_nmix_prpr", "")).strip()
        if not category_code or not value_text:  # 빈 행 skip
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        categories.append(
            CategoryIndex(
                code=category_code,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                value=required_decimal(value_text, "bstp_nmix_prpr"),
                change=_apply_change_sign(
                    required_decimal(row.get("bstp_nmix_prdy_vrss"), "bstp_nmix_prdy_vrss"), sign
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("bstp_nmix_prdy_ctrt"), "bstp_nmix_prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                amount=required_decimal(row.get("acml_tr_pbmn"), "acml_tr_pbmn"),
                volume_share=required_decimal(row.get("acml_vol_rlim"), "acml_vol_rlim"),
                amount_share=required_decimal(row.get("acml_tr_pbmn_rlim"), "acml_tr_pbmn_rlim"),
                _raw=row,
            )
        )
    return categories


def _parse_index_quote(
    output: Mapping[str, Any], *, code: str, as_of: datetime
) -> IndexQuote:
    sign = str(output.get("prdy_vrss_sign", "")).strip()
    return IndexQuote(
        code=code,
        value=required_decimal(output.get("bstp_nmix_prpr"), "bstp_nmix_prpr"),
        open=required_decimal(output.get("bstp_nmix_oprc"), "bstp_nmix_oprc"),
        high=required_decimal(output.get("bstp_nmix_hgpr"), "bstp_nmix_hgpr"),
        low=required_decimal(output.get("bstp_nmix_lwpr"), "bstp_nmix_lwpr"),
        change=_apply_change_sign(
            required_decimal(output.get("bstp_nmix_prdy_vrss"), "bstp_nmix_prdy_vrss"), sign
        ),
        change_percent=_apply_change_sign(
            required_decimal(output.get("bstp_nmix_prdy_ctrt"), "bstp_nmix_prdy_ctrt"), sign
        ),
        volume=required_int(output.get("acml_vol"), "acml_vol"),
        amount=required_decimal(output.get("acml_tr_pbmn"), "acml_tr_pbmn"),
        advances=required_int(output.get("ascn_issu_cnt"), "ascn_issu_cnt"),
        declines=required_int(output.get("down_issu_cnt"), "down_issu_cnt"),
        unchanged=required_int(output.get("stnr_issu_cnt"), "stnr_issu_cnt"),
        limit_up=required_int(output.get("uplm_issu_cnt"), "uplm_issu_cnt"),
        limit_down=required_int(output.get("lslm_issu_cnt"), "lslm_issu_cnt"),
        as_of=as_of,
        _raw=output,
    )
