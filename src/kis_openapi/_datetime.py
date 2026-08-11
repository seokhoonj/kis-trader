"""KIS 날짜/시각 와이어 형식 <-> 파이썬 날짜/시간.

KIS 응답은 날짜를 ``"YYYYMMDD"`` 문자열로, 장중 시각을 ``"HHMMSS"`` 문자열로 준다(모두
한국 표준시 KST 기준). 이 모듈은 자산군과 무관하게 그 변환만 담당한다. 파싱 실패는
fail-closed(:class:`KISError`)로 올리고, 사용자 입력 날짜의 정규화 실패는
:class:`KISUsageError` 로 올린다. 시장별 엔진이 공통으로 import 한다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .errors import KISError, KISUsageError

_KST = timezone(timedelta(hours=9))


def _parse_kst_date(date_text: str) -> date:
    """"YYYYMMDD" -> date. 날짜만 필요한 곳(투자자 일자 등)에서 쓴다."""
    try:
        return datetime.strptime(date_text, "%Y%m%d").date()  # noqa: DTZ007 -- date 만 취함
    except ValueError as err:
        raise KISError(f"날짜(YYYYMMDD) 파싱 실패: {date_text!r}") from err


def _parse_intraday_timestamp(time_text: str, as_of: datetime) -> datetime:
    """당일 체결 시각("HHMMSS")을 KST-aware datetime 으로(날짜는 조회일 ``as_of``)."""
    try:
        moment = datetime.strptime(time_text, "%H%M%S").time()  # noqa: DTZ007 -- 아래에서 KST 결합
    except ValueError as err:
        raise KISError(f"체결 시각(stck_cntg_hour) 파싱 실패: {time_text!r}") from err
    return datetime.combine(as_of.date(), moment, tzinfo=_KST)


def _to_yyyymmdd(value: str | date, name: str) -> str:
    if isinstance(value, date):  # datetime 도 date 하위형
        return f"{value:%Y%m%d}"
    digits = str(value).strip().replace("-", "")
    if len(digits) == 8 and digits.isdigit():
        return digits
    raise KISUsageError(f"{name} 는 date 또는 YYYYMMDD/YYYY-MM-DD 문자열이어야 한다: {value!r}")
