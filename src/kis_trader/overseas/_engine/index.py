"""해외 지수/환율/국채/금선물 기간봉 조회 (내부).

사용자면(:class:`~kis_trader.overseas.index.OverseasIndex`)이 호출한다. 해외 지수류는
``FID_COND_MRKT_DIV_CODE`` 로 자산 종류를 구분하고 ``FID_INPUT_ISCD`` 로 심볼을 조회한다.

KIS URL/TR-ID (대표 예시 -- 전체는 각 함수의 경로/TR-ID 상수 참조):
- 기간봉: ``GET .../quotations/inquire-daily-chartprice`` ``FHKST03030100``.
- 분봉: ``GET .../quotations/inquire-time-indexchartprice`` ``FHKST03030200``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from ..._bars import (
    _parse_bar_timestamp,
    _parse_minute_bar_timestamp,
    _period_code_for,
    collect_period_bars,
)
from ..._internal._datetime import _KST, _to_yyyymmdd, _today_kst
from ..._internal._response import (
    _missing_block_error,
    _raise_if_error,
)
from ..._internal._wire import _apply_change_sign, optional_int, required_decimal, required_int
from ...bar import Bar, Interval
from ...errors import KISError, KISUsageError
from ...transport import RawResponse, Transport
from ..entities.index import OverseasIndexQuote

_BARS_PATH = "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
_BARS_TR   = "FHKST03030100"
_MINUTE_BARS_PATH = "/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice"
_MINUTE_BARS_TR = "FHKST03030200"


def fetch_quote(
    transport: Transport, *, symbol: str, market_division: str
) -> OverseasIndexQuote:
    """해외 지수류 현재가를 기간별시세 응답 ``output1`` 에서 조회한다.

    기간봉과 같은 FHKST03030100 엔드포인트를 오늘 일봉 범위로 한 번 호출한다. ``output2`` 는
    사용하지 않고, 현재가·전일대비·시고저가 담긴 ``output1`` 만 파싱한다.
    """
    today = _today_kst()
    resp = transport.request(
        method="GET",
        path=_BARS_PATH,
        tr_id=_BARS_TR,
        params={
            "FID_COND_MRKT_DIV_CODE": market_division,
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": today,
            "FID_INPUT_DATE_2": today,
            "FID_PERIOD_DIV_CODE": "D",
        },
        idempotent=True,
    )
    _raise_if_error(resp)
    output = resp.body.get("output1")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output1", resp)
    return _parse_quote(output, code=symbol, as_of=datetime.now(_KST))


def fetch_quotes(
    transport: Transport, *, symbols: Sequence[str], market_division: str
) -> list[OverseasIndexQuote]:
    """여러 해외 지수류 코드를 요청 순서대로 조회한다.

    KIS가 이 지수류에 멀티시세 엔드포인트를 제공하지 않으므로, 단일 조회를 코드마다 호출하되
    호출자는 한 번의 배치 메서드로 결과를 받는다.
    """
    return [
        fetch_quote(transport, symbol=symbol, market_division=market_division)
        for symbol in symbols
    ]


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

    ``interval`` 은 ``1m``(분봉)/``1d``/``1wk``/``1mo`` 이고 기간봉(``1d``/``1wk``/``1mo``)은
    ``start`` 가 필요하다. ``end`` 는 기본 오늘이며 ``max_bars`` 로 최근 봉 수를 제한한다.
    분봉(``1m``)은 해외지수·환율(market_division N/X)만 FHKST03030200 으로 조회한다(국채·금선물은 기간봉만).
    """
    if max_bars is not None and max_bars <= 0:
        raise KISUsageError(f"max_bars 는 양의 정수여야 한다: {max_bars}")
    if interval == "1m":
        return _fetch_minute_bars(
            transport,
            symbol=symbol,
            market_division=market_division,
            start=start,
            end=end,
            max_bars=max_bars,
        )
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


def _fetch_minute_bars(
    transport: Transport,
    *,
    symbol: str,
    market_division: str,
    start: str | date | None,
    end: str | date | None,
    max_bars: int | None,
) -> list[Bar]:
    if market_division not in {"N", "X"}:
        raise KISUsageError("해외 지수 분봉은 해외지수(kind='index')와 환율(kind='fx')만 지원한다.")
    start_date = None if start is None else _to_yyyymmdd(start, "start")
    end_date = None if end is None else _to_yyyymmdd(end, "end")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    resp = transport.request(
        method="GET",
        path=_MINUTE_BARS_PATH,
        tr_id=_MINUTE_BARS_TR,
        params={
            "FID_COND_MRKT_DIV_CODE": market_division,
            "FID_INPUT_ISCD": symbol,
            "FID_HOUR_CLS_CODE": "0",
            "FID_PW_DATA_INCU_YN": "Y",
        },
        idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(resp.body.get("output1"), Mapping):
        raise _missing_block_error("output1", resp)
    if not isinstance(rows, (list, tuple)):
        raise _missing_block_error("output2", resp)
    bars = _parse_minute_bars(rows, symbol=symbol, resp=resp)
    if start_date is not None:
        bars = [bar for bar in bars if f"{bar.timestamp:%Y%m%d}" >= start_date]
    if end_date is not None:
        bars = [bar for bar in bars if f"{bar.timestamp:%Y%m%d}" <= end_date]
    if max_bars is not None:
        bars = bars[-max_bars:]
    return bars


def _parse_minute_bars(
    rows: Sequence[object], *, symbol: str, resp: RawResponse
) -> list[Bar]:
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output2[]", resp)
        date_text = str(row.get("stck_bsop_date", "")).strip()
        time_text = str(row.get("stck_cntg_hour", "")).strip()
        close_text = str(row.get("optn_prpr", "")).strip()
        if not date_text or not time_text or not close_text:
            continue
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=_parse_minute_bar_timestamp(date_text=date_text, time_text=time_text),
                open=required_decimal(row.get("optn_oprc"), "optn_oprc"),
                high=required_decimal(row.get("optn_hgpr"), "optn_hgpr"),
                low=required_decimal(row.get("optn_lwpr"), "optn_lwpr"),
                close=required_decimal(close_text, "optn_prpr"),
                volume=required_int(row.get("cntg_vol"), "cntg_vol"),
                _raw=row,
            )
        )
    bars.sort(key=lambda bar: bar.timestamp)
    return bars


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


def _parse_quote(
    output: Mapping[str, Any], *, code: str, as_of: datetime
) -> OverseasIndexQuote:
    """FHKST03030100 ``output1`` 을 해외 지수류 스냅샷으로 바꾼다."""
    if not str(output.get("stck_shrn_iscd", "")).strip():
        raise KISError(
            f"해외 지수류 현재가 응답의 stck_shrn_iscd 가 비어 있다 -- 코드 {code!r} 를 확인하라."
        )
    change_sign_code = str(output.get("prdy_vrss_sign", "")).strip()
    return OverseasIndexQuote(
        code=code,
        name=str(output.get("hts_kor_isnm", "")).strip(),
        current_value=required_decimal(output.get("ovrs_nmix_prpr"), "ovrs_nmix_prpr"),
        open=required_decimal(output.get("ovrs_prod_oprc"), "ovrs_prod_oprc"),
        high=required_decimal(output.get("ovrs_prod_hgpr"), "ovrs_prod_hgpr"),
        low=required_decimal(output.get("ovrs_prod_lwpr"), "ovrs_prod_lwpr"),
        previous_close=required_decimal(
            output.get("ovrs_nmix_prdy_clpr"), "ovrs_nmix_prdy_clpr"
        ),
        change=_apply_change_sign(
            required_decimal(output.get("ovrs_nmix_prdy_vrss"), "ovrs_nmix_prdy_vrss"),
            change_sign_code,
        ),
        change_percent=_apply_change_sign(
            required_decimal(output.get("prdy_ctrt"), "prdy_ctrt"), change_sign_code
        ),
        volume=required_int(output.get("acml_vol"), "acml_vol"),
        previous_volume=optional_int(output.get("prdy_vol"), "prdy_vol"),
        as_of=as_of,
        _raw=output,
    )
