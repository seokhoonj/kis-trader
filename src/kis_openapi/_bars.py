"""KIS OHLCV 바(캔들) 파싱과 기간봉 페이지네이션 -- 시장 중립.

기간봉은 종목/지수/파생/채권/해외가 같은 페이지네이션 형태(날짜창을 뒤로 밀며 KIS 페이지
상한을 넘고, 중복 날짜 병합, 빈 페이지 종료, 상한 도달 시 부분 결과로 자르지 않고 예외)를
쓴다. 필드명만 자산군마다 달라 :func:`collect_period_bars` 는 행 파서를 콜백으로 받는다.
바 타임스탬프 파서(일봉/분봉)와 분봉 페이지 기준시각 산술도 여기 둔다. 시장별 엔진이 공통으로
import 한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from ._datetime import _KST
from ._response import _missing_block_error, _raise_if_error
from .bar import Bar
from .errors import KISError, KISUsageError
from .transport import Transport

#: 기간봉 interval -> KIS FID_PERIOD_DIV_CODE(일/주/월).
_PERIOD_BY_INTERVAL = {"1d": "D", "1wk": "W", "1mo": "M"}

#: 기간봉 페이지 상한 backstop(날짜창을 뒤로 밀며 조회, 무한 루프 방지).
_MAX_BAR_PAGES = 200

#: 분봉 페이지 상한 backstop(기준시각을 뒤로 밀며 조회).
_MAX_MINUTE_PAGES = 60


def collect_period_bars(
    transport: Transport,
    *,
    path: str,
    tr: str,
    base_params: dict[str, str],
    start_date: str,
    end_date: str,
    max_bars: int | None,
    parse_rows: Callable[[Sequence[Mapping[str, Any]]], list[Bar]],
) -> list[Bar]:
    """[start, end] 기간봉을 과거->현재 오름차순으로. KIS 페이지 상한을 날짜창을 뒤로 밀며 넘고,
    중복 날짜는 병합, 빈 페이지면 종료, 페이지 상한에 닿으면 부분 결과로 자르지 않고 예외.

    종목/지수 공용 -- ``base_params`` 는 날짜 외 고정 파라미터(시장구분/코드/기간/수정주가 등),
    ``parse_rows`` 는 output2 행을 :class:`Bar` 로 바꾸는 파서(필드명이 종목/지수마다 다르다)."""
    bar_by_date: dict[str, Bar] = {}
    window_end = end_date
    for _page in range(_MAX_BAR_PAGES):
        params = {**base_params, "FID_INPUT_DATE_1": start_date, "FID_INPUT_DATE_2": window_end}
        resp = transport.request(
            method="GET", path=path, tr_id=tr, params=params, idempotent=True
        )
        _raise_if_error(resp)
        rows = resp.body.get("output2")
        if not isinstance(rows, list):  # 성공 응답인데 바 배열 아님 -> fail-closed
            raise _missing_block_error("output2", resp)
        page_by_date = {f"{bar.timestamp:%Y%m%d}": bar for bar in parse_rows(rows)}
        if not page_by_date:
            break
        bar_by_date.update(page_by_date)
        if max_bars is not None and len(bar_by_date) >= max_bars:
            break  # 최근 max_bars 면 충분 -> 더 안 훑음
        oldest_date = min(page_by_date)  # YYYYMMDD 고정폭 -> 문자열 비교 = 시간순
        if oldest_date <= start_date:
            break
        oldest = page_by_date[oldest_date].timestamp
        window_end = f"{oldest - timedelta(days=1):%Y%m%d}"
    else:
        raise KISError(
            f"바 조회가 {_MAX_BAR_PAGES}페이지 상한에 도달했으나 start({start_date})에 못 미쳤다 "
            f"-- 부분 결과로 자르지 않는다. 범위를 좁히거나 재시도하라."
        )

    bars = [
        bar_by_date[key]
        for key in sorted(bar_by_date)
        if start_date <= key <= end_date
    ]
    if max_bars is not None and len(bars) > max_bars:
        bars = bars[-max_bars:]
    return bars


def _period_code_for(interval: str) -> str:
    if interval in _PERIOD_BY_INTERVAL:
        return _PERIOD_BY_INTERVAL[interval]
    raise KISUsageError(f"지원하지 않는 기간봉 interval: {interval!r} (1d/1wk/1mo).")


def _parse_bar_timestamp(date_text: str) -> datetime:
    try:
        day = datetime.strptime(date_text, "%Y%m%d")  # noqa: DTZ007 -- 아래 replace 로 KST-aware
    except ValueError as err:
        raise KISError(f"바 날짜(stck_bsop_date) 파싱 실패: {date_text!r}") from err
    return day.replace(tzinfo=_KST)


def _parse_minute_bar_timestamp(*, date_text: str, time_text: str) -> datetime:
    try:
        moment = datetime.strptime(date_text + time_text, "%Y%m%d%H%M%S")  # noqa: DTZ007 -- KST 결합
    except ValueError as err:
        raise KISError(f"분봉 시각 파싱 실패: {date_text!r} {time_text!r}") from err
    return moment.replace(tzinfo=_KST)


def _subtract_one_minute(hhmmss: str) -> str:
    """"HHMMSS" 에서 1분 뺀 "HHMMSS"(다음 페이지의 기준시각). 분 경계/시 경계 넘김 처리."""
    try:
        moment = datetime.strptime(hhmmss, "%H%M%S")  # noqa: DTZ007 -- 날짜 없는 시각 산술용
    except ValueError as err:
        raise KISError(f"분봉 기준시각 파싱 실패: {hhmmss!r}") from err
    return f"{moment - timedelta(minutes=1):%H%M%S}"
