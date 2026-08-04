"""ETF/ETN 시세 DATA -- :class:`EtfNav`.

ETF/ETN 은 호가창에서 거래되는 종목이라 시세/주문은 :class:`~kis_openapi.ticker.Ticker` 로 하고,
ETF 고유 정보(NAV 등)만 이 타입들로 돌려준다. :class:`EtfNav` 는 순자산가치 스냅샷
(:meth:`~kis_openapi.ticker.Ticker.nav`)이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class EtfNav:
    """ETF/ETN 순자산가치(NAV) 스냅샷(불변).

    ``nav`` 는 현재 NAV(순자산가치), ``premium`` 은 괴리율(시장가가 NAV 대비 얼마나 벗어났는지, %),
    ``tracking_error`` 는 추적오차율(%). ``nav_change`` / ``nav_change_percent`` 는 NAV 전일대비로
    하락이면 음수. 시장 체결가는 :meth:`~kis_openapi.ticker.Ticker.quote` 에 있다.
    """

    symbol: str
    nav: Decimal                      # 현재 NAV
    nav_change: Decimal               # NAV 전일대비(부호 포함)
    nav_change_percent: Decimal       # NAV 전일대비율(부호 포함)
    previous_nav: Decimal             # 전일 최종 NAV
    premium: Decimal                  # 괴리율(%): 시장가 vs NAV
    tracking_error: Decimal           # 추적오차율(%)
    net_assets: Decimal               # 순자산 총액
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
