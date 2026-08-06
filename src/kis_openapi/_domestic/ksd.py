"""기업행위 캘린더 조회 (내부) -- 한국예탁결제원(KSD) 일정.

사용자면은 :class:`~kis_openapi.calendar.CalendarQueries`(``kis.calendar``)다. 모든 조회가
``/uapi/domestic-stock/v1/ksdinfo/`` 아래에 있고, 기간(``F_DT`` ~ ``T_DT``) + 선택 종목(``SHT_CD``)
으로 이벤트 배열(``output1``)을 준다. 날짜는 순수 달력 날짜라 :class:`datetime.date` 로 돌려준다.

KIS URL/TR-id:
- 배당일정: ``GET .../ksdinfo/dividend`` ``HHKDB669102C0``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from .._wire import optional_decimal
from ..calendar_items import DividendEvent
from ..errors import KISError, KISUsageError
from ..transport import Transport
from .market_data import _missing_block_error, _raise_if_error, _to_yyyymmdd

_DIVIDEND_PATH = "/uapi/domestic-stock/v1/ksdinfo/dividend"
_DIVIDEND_TR = "HHKDB669102C0"

#: 배당 조회구분(GB1). 원장: 0(배당전체), 1(결산배당), 2(중간배당).
_DIVIDEND_KIND = {"all": "0", "final": "1", "interim": "2"}


def _parse_ksd_date(value: object, *, required: bool, name: str) -> date | None:
    """KSD 날짜("YYYYMMDD") -> date. 빈 값/"00000000"(미정 sentinel)은 ``None``(required 면 예외).
    값이 있는데 형식이 깨지면 fail-closed."""
    text = str(value).strip()
    if not text or text == "00000000":
        if required:
            raise KISError(f"필수 날짜 필드 {name!r} 가 비어 있다: {value!r}")
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()  # noqa: DTZ007 -- 달력 날짜, 시각/tz 없음
    except ValueError as err:
        raise KISError(f"날짜 필드 {name!r} 파싱 실패: {value!r}") from err


def _rows(transport: Transport, *, path: str, tr: str, params: Mapping[str, str]
          ) -> Sequence[Mapping[str, Any]]:
    resp = transport.request(method="GET", path=path, tr_id=tr, params=dict(params),
                             idempotent=True)
    _raise_if_error(resp)
    rows = resp.body.get("output1")
    if not isinstance(rows, list):
        raise _missing_block_error("output1", resp)
    return rows


def fetch_dividends(
    transport: Transport, *, start: str | date, end: str | date,
    symbol: str | None = None, kind: str = "all",
) -> list[DividendEvent]:
    """기간 [start, end] 의 배당 일정. ``symbol`` 지정 시 그 종목만, ``kind`` 는 all/final/interim."""
    try:
        gb1 = _DIVIDEND_KIND[kind]
    except KeyError:
        raise KISUsageError(f"kind 는 {sorted(_DIVIDEND_KIND)} 중 하나: {kind!r}") from None
    params = {
        "CTS": "",
        "GB1": gb1,
        "F_DT": _to_yyyymmdd(start, "start"),
        "T_DT": _to_yyyymmdd(end, "end"),
        "SHT_CD": symbol or "",
        "HIGH_GB": "",
    }
    events: list[DividendEvent] = []
    for row in _rows(transport, path=_DIVIDEND_PATH, tr=_DIVIDEND_TR, params=params):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        high = str(row.get("high_divi_gb", "")).strip().upper() in {"Y", "1"}
        events.append(
            DividendEvent(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                dividend_kind=str(row.get("divi_kind", "")).strip(),
                face_value=optional_decimal(row.get("face_val"), "face_val"),
                cash_dividend=optional_decimal(row.get("per_sto_divi_amt"), "per_sto_divi_amt"),
                cash_dividend_rate=optional_decimal(row.get("divi_rate"), "divi_rate"),
                stock_dividend_rate=optional_decimal(row.get("stk_divi_rate"), "stk_divi_rate"),
                cash_pay_date=_parse_ksd_date(row.get("divi_pay_dt"), required=False,
                                              name="divi_pay_dt"),
                stock_pay_date=_parse_ksd_date(row.get("stk_div_pay_dt"), required=False,
                                               name="stk_div_pay_dt"),
                odd_lot_pay_date=_parse_ksd_date(row.get("odd_pay_dt"), required=False,
                                                 name="odd_pay_dt"),
                stock_kind=str(row.get("stk_kind", "")).strip(),
                high_dividend=high,
                _raw=row,
            )
        )
    return events
