"""선물/옵션(파생) 시세 DATA -- :class:`DerivativesQuote`.

:class:`~kis_openapi.derivative.Derivative` 핸들(``kis.futures(code)`` / ``kis.option(code)``)이
돌려주는 한 계약의 현재가 스냅샷이다. 종목의 :class:`~kis_openapi.quote.Quote` 와 달리 파생 고유의
미결제약정(open interest)·베이시스·이론가·괴리율을 담는다. 옵션 그릭스(delta/gamma/theta/vega/rho)와
변동성·잔존일수는 ``_raw`` 로 접근한다(선물엔 없거나 무의미하므로).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class DerivativesQuote:
    """선물/옵션 계약의 현재가 스냅샷(불변).

    ``change`` / ``change_percent`` 는 전일대비로 하락이면 음수. ``open_interest`` 는 미결제약정,
    ``basis`` 는 선물-기초자산 베이시스, ``theoretical_price`` 는 이론가, ``premium`` 은 괴리율(%).
    베이시스/이론가/괴리율은 계약에 따라 없을 수 있어 ``None`` 이다.
    """

    code: str
    name: str
    last: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    previous_close: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int
    open_interest: int                # 미결제약정
    theoretical_price: Decimal | None  # 이론가
    basis: Decimal | None             # 베이시스(선물-기초자산)
    premium: Decimal | None           # 괴리율(%)
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
