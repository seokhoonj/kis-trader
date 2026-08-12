"""KIS 날짜/시각 와이어 형식 <-> 파이썬 날짜/시간.

KIS 응답은 날짜를 ``"YYYYMMDD"`` 문자열로, 장중 시각을 ``"HHMMSS"`` 문자열로 준다(모두
한국 표준시 KST 기준). 이 모듈은 자산군과 무관하게 그 변환만 담당한다. 파싱 실패는
fail-closed(:class:`KISError`)로 올리고, 사용자 입력 날짜의 정규화 실패는
:class:`KISUsageError` 로 올린다. 시장별 엔진이 공통으로 import 한다.

의존은 표준 라이브러리와 :mod:`.errors` 뿐이다(순환 import 금지).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .errors import KISError, KISUsageError

_KST = timezone(timedelta(hours=9))

#: KIS 가 "날짜 없음"을 나타내는 0-채움 센티넬.
_DATE_SENTINEL = "00000000"


def _today_kst() -> str:
    """오늘(KST)을 "YYYYMMDD" 로. 조회 기본 종료일/앵커 등에 쓴다."""
    return f"{datetime.now(_KST):%Y%m%d}"


def _parse_kst_date(date_text: str) -> date:
    """"YYYYMMDD" -> date. 날짜만 필요한 곳(투자자 일자 등)에서 쓴다."""
    try:
        return datetime.strptime(date_text, "%Y%m%d").date()  # noqa: DTZ007 -- date 만 취함
    except ValueError as err:
        raise KISError(f"날짜(YYYYMMDD) 파싱 실패: {date_text!r}") from err


def parse_optional_kst_date(value: object, *, required: bool = False) -> date | None:
    """선택적 날짜 필드를 :class:`date` 로. 값이 없거나 센티넬이면 ``None``.

    KIS 응답의 날짜 필드는 종종 비어 있거나(JSON ``null``/누락) 0-채움 센티넬
    ("00000000")로 온다. 그런 값은 "날짜 없음"이므로 ``None`` 을 돌려준다(``required=True``
    면 :class:`KISError`). 그 밖의 비어있지 않은 값은 반드시 달력에 실재하는 날짜여야 하며,
    8자리 ASCII 숫자가 아니거나(예: "20230230" 같은) 불가능한 날짜면 fail-closed
    (:class:`KISError`)로 올린다.

    - ``None`` (JSON null/누락) -> ``None``. ``str(None)`` 이 "None" 문자열이 되는 버그를
      막으려고 ``None`` 을 str() 전에 먼저 가른다.
    - 빈 문자열, 센티넬("00000000") -> ``None``.
    - 그 외 -> ``date`` (실재하지 않는 날짜면 예외). datetime 이 아니라 date 를 돌려준다.
    """
    if value is None:
        if required:
            raise KISError("필수 날짜 필드가 비어 있다(None)")
        return None
    text = str(value).strip().replace("-", "")
    if text == "" or text == _DATE_SENTINEL:
        if required:
            raise KISError(f"필수 날짜 필드가 비어 있다: {value!r}")
        return None
    if len(text) != 8 or not text.isascii() or not text.isdigit():
        raise KISError(f"날짜(YYYYMMDD) 파싱 실패: {value!r}")
    try:
        return datetime.strptime(text, "%Y%m%d").date()  # noqa: DTZ007 -- date 만 취함
    except ValueError as err:
        raise KISError(f"날짜(YYYYMMDD) 파싱 실패: {value!r}") from err


def _parse_intraday_timestamp(time_text: str, as_of: datetime) -> datetime:
    """당일 체결 시각("HHMMSS")을 KST-aware datetime 으로(날짜는 조회일 ``as_of``)."""
    try:
        moment = datetime.strptime(time_text, "%H%M%S").time()  # noqa: DTZ007 -- 아래에서 KST 결합
    except ValueError as err:
        raise KISError(f"체결 시각(stck_cntg_hour) 파싱 실패: {time_text!r}") from err
    return datetime.combine(as_of.date(), moment, tzinfo=_KST)


def _combine_date_time(date_text: str, time_text: str) -> datetime:
    """영업일자("YYYYMMDD") + 체결시각("HHMMSS") -> KST-aware datetime.

    두 값 모두 유효해야 하며 파싱 실패는 fail-closed(:class:`KISError`). 날짜/시각 결합
    방식은 :func:`_parse_intraday_timestamp` 와 동일(파싱한 date + HHMMSS 를 KST 로 묶음)."""
    day = _parse_kst_date(date_text)
    try:
        moment = datetime.strptime(time_text, "%H%M%S").time()  # noqa: DTZ007 -- 아래에서 KST 결합
    except ValueError as err:
        raise KISError(f"체결 시각(HHMMSS) 파싱 실패: {time_text!r}") from err
    return datetime.combine(day, moment, tzinfo=_KST)


def _to_yyyymmdd(value: str | date, name: str) -> str:
    """사용자 입력 날짜를 KIS 와이어 정본("YYYYMMDD")으로.

    date/datetime, 또는 "YYYYMMDD"/"YYYY-MM-DD" 문자열만 받는다. 그 밖의 타입(int 등)이나
    달력에 실재하지 않는 날짜(예: "20230230")는 :class:`KISUsageError` 로 거부한다."""
    if isinstance(value, date):  # datetime 도 date 하위형
        return f"{value:%Y%m%d}"
    if not isinstance(value, str):
        raise KISUsageError(
            f"{name} 는 date 또는 YYYYMMDD/YYYY-MM-DD 문자열이어야 한다: {value!r}"
        )
    digits = value.strip().replace("-", "")
    if len(digits) != 8 or not digits.isascii() or not digits.isdigit():
        raise KISUsageError(
            f"{name} 는 date 또는 YYYYMMDD/YYYY-MM-DD 문자열이어야 한다: {value!r}"
        )
    try:
        datetime.strptime(digits, "%Y%m%d")  # noqa: DTZ007 -- 유효성만 검사
    except ValueError as err:
        raise KISUsageError(f"{name} 는 실재하는 날짜여야 한다: {value!r}") from err
    return digits
