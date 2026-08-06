"""기업행위 캘린더(DATA) -- :class:`DividendEvent` 등.

한국예탁결제원(KSD)이 제공하는 기업행위 일정의 한 항목이다. 배당·유상증자·주주총회 같은 이벤트를
기준일 기준으로 준다. :class:`~kis_openapi.calendar.CalendarQueries`(``kis.calendar``)가 기간
조회로 리스트를 돌려준다. 날짜는 시각/시간대 없는 순수 달력 날짜라 :class:`datetime.date` 로 둔다
(시세 타임스탬프의 KST-aware ``datetime`` 과 구분).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class DividendEvent:
    """한 종목의 배당 일정(불변).

    ``record_date`` 는 배당 기준일, ``dividend_kind`` 는 배당종류(결산/중간 등), ``cash_dividend`` 는
    1주당 현금배당금, ``cash_dividend_rate`` / ``stock_dividend_rate`` 는 현금/주식 배당률(%)이다.
    지급일(``cash_pay_date`` / ``stock_pay_date`` / ``odd_lot_pay_date``)은 미정이면 ``None``.
    ``high_dividend`` 는 고배당종목 표시 여부.
    """

    symbol: str                       # 종목코드(sht_cd)
    name: str                         # 종목명(isin_name)
    record_date: date                 # 기준일(record_date)
    dividend_kind: str                # 배당종류(divi_kind; 결산/중간 등)
    face_value: Decimal | None        # 액면가(face_val)
    cash_dividend: Decimal | None     # 1주당 현금배당금(per_sto_divi_amt)
    cash_dividend_rate: Decimal | None  # 현금배당률 %(divi_rate)
    stock_dividend_rate: Decimal | None  # 주식배당률 %(stk_divi_rate)
    cash_pay_date: date | None        # 배당금지급일(divi_pay_dt)
    stock_pay_date: date | None       # 주식배당지급일(stk_div_pay_dt)
    odd_lot_pay_date: date | None     # 단주대금지급일(odd_pay_dt)
    stock_kind: str                   # 주식종류(stk_kind; 보통/우선)
    high_dividend: bool               # 고배당종목여부(high_divi_gb)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
