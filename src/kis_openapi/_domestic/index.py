"""국내 지수/업종 시세 조회 (내부) -- 지수 현재가 등을 :class:`IndexQuote` 로.

사용자면(:class:`~kis_openapi.index.Index`, ``kis.index(code)``)이 호출한다. 지수/업종은 종목이
아니라 시장구분 ``U`` + 업종코드(``FID_INPUT_ISCD``)로 조회한다. 업종코드는 포털의 업종코드표를
따르며, 대표값은 0001 KOSPI 종합 / 1001 KOSDAQ 종합 / 2001 KOSPI200.

KIS URL/TR-id (원장 대조):
- 지수 현재가: ``GET .../quotations/inquire-index-price`` ``FHPUP02100000`` (``FID_COND_MRKT_DIV_CODE=U``).
- 지수 기간봉(일/주/월/년): ``GET .../quotations/inquire-daily-indexchartprice`` ``FHKUP03500100``
  (``FID_PERIOD_DIV_CODE`` D:일 W:주 M:월 Y:년). 종목 일봉과 페이지네이션은 같고 필드명만
  ``bstp_nmix_*`` 로 다르다 -- 공용 :func:`collect_period_bars` 재사용.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from .._wire import required_decimal, required_int
from ..bar import Bar, Interval
from ..errors import KisUsageError
from ..index_quote import IndexQuote
from ..transport import Transport
from .market_data import (
    _KST,
    _apply_change_sign,
    _missing_block_error,
    _parse_bar_timestamp,
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
    """지수 기간봉(일/주/월)을 과거->현재 오름차순으로. ``interval`` 은 ``1d``/``1wk``/``1mo``,
    ``start`` 가 필요하다(``end`` 기본 오늘). 지수엔 수정주가 개념이 없어 ``adjusted`` 는 없다.

    지수 분봉(``1m``)은 별도 엔드포인트(inquire-time-indexchartprice)이고 앵커가 아니라 봉 크기(초)
    파라미터라 페이지네이션이 달라 아직 지원하지 않는다."""
    if max_bars is not None and max_bars <= 0:
        raise KisUsageError(f"max_bars 는 양의 정수여야 한다: {max_bars}")
    if interval == "1m":
        raise NotImplementedError(
            "지수 분봉은 아직 미구현 -- inquire-time-indexchartprice 는 앵커가 아니라 봉 크기(초) "
            "파라미터라 별 슬라이스로 다룬다."
        )
    if start is None:
        raise KisUsageError(f"interval={interval!r}(기간봉)에는 start 가 필요하다.")
    period = _period_code_for(interval)
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _to_yyyymmdd(start, "start")
    if start_date > end_date:
        raise KisUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
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
