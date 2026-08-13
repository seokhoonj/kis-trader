"""시간외 단일가(DATA) -- :class:`AfterHoursQuote` / :class:`AfterHoursConclusion` /
:class:`AfterHoursDailyPrice`.

정규장 마감 후 시간외 단일가 세션의 스냅샷·시간별 체결·일자별 종가다. 스냅샷
(:class:`AfterHoursQuote`)은 세션이 열려 있지 않으면(장중 조회 등) 값이 비어 올 수 있어 호가·예상체결
필드를 optional 로 둔다(빈 값은 ``None``). 체결(:class:`AfterHoursConclusion`)은 체결값이 required
이고 호가(ask/bid)만 optional, 일자별 종가(:class:`AfterHoursDailyPrice`)는 모든 값 필드가 required
다(있는데 깨지면 예외).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class AfterHoursQuote:
    """시간외 단일가 스냅샷(불변). 세션이 열려 있지 않으면 대부분 ``None`` 일 수 있다.

    ``change`` / ``change_percent`` 는 예상체결가의 전일대비(하락이면 음수). ``as_of`` 는 조회 시각.
    """

    symbol: str
    bid: Decimal | None               # 최우선 매수호가
    ask: Decimal | None               # 최우선 매도호가
    expected_price: Decimal | None    # 시간외 단일가 예상체결가
    expected_quantity: int | None     # 예상체결 수량
    change: Decimal | None            # 예상체결가 전일대비(부호 포함)
    change_percent: Decimal | None    # 예상체결가 전일대비율(부호 포함)
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class AfterHoursConclusion:
    """시간외 단일가 세션의 한 시각 체결(불변).

    ``price`` 는 그 시각 체결가, ``change`` / ``change_percent`` 는 전일대비(하락이면 음수),
    ``ask`` / ``bid`` 는 그 시각 최우선 호가, ``cumulative_volume`` 은 시간외 누적 거래량,
    ``tick_volume`` 은 그 체결의 거래량이다. :meth:`~kis_trader.stock.DomesticStock.after_hours_conclusions`
    가 시각 리스트로 돌려준다. ``timestamp`` 는 체결시각(시각은 벤더, 날짜는 조회일; KST-aware).
    """

    symbol: str
    timestamp: datetime               # 체결시각(시각은 벤더, 날짜는 조회일; KST)
    price: Decimal                    # 체결가(stck_prpr)
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    ask: Decimal | None               # 최우선 매도호가(askp)
    bid: Decimal | None               # 최우선 매수호가(bidp)
    cumulative_volume: int            # 시간외 누적 거래량(acml_vol)
    tick_volume: int                  # 이 체결의 거래량(cntg_vol)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class AfterHoursDailyPrice:
    """시간외 단일가 세션의 하루 종가(불변).

    ``price`` 는 그날 시간외 단일가 종가, ``change`` / ``change_percent`` 는 그 시간외가의 전일대비
    (하락이면 음수), ``volume`` / ``trading_amount`` 는 시간외 거래량/거래대금이다.
    :meth:`~kis_trader.stock.DomesticStock.after_hours_daily` 가 일자 리스트(최근->과거)로 돌려준다.
    ``timestamp`` 는 영업일(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 영업일(KST-aware)
    price: Decimal                    # 시간외 단일가 종가(ovtm_untp_prpr)
    change: Decimal                   # 시간외가 전일대비(부호 포함, ovtm_untp_prdy_vrss)
    change_percent: Decimal           # 시간외가 전일대비율(부호 포함, ovtm_untp_prdy_ctrt)
    volume: int                       # 시간외 거래량(ovtm_untp_vol)
    trading_amount: Decimal           # 시간외 거래대금(ovtm_untp_tr_pbmn)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
