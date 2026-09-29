"""실시간 결과 엔티티(frozen dataclass).

REST 결과 타입과 같은 관례: 공개 식별자는 산업표준 영어, 설명은 한국어 docstring, 매핑 필드로
꺼내 쓰고 원본(벤더 키->값)은 ``_raw`` 로. 실시간 프레임은 위치 기반 ``^`` 필드라, 파서가
원장 필드순으로 ``_raw`` (Element 이름 -> 원문)를 만들고 헤드라인 필드를 타입화한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class StockTick:
    """국내주식 실시간 체결(틱). 한 체결 이벤트의 현재가/등락/거래량/체결강도 등.

    KRX/NXT/통합 체결가(H0STCNT0/H0NXCNT0/H0UNCNT0)가 같은 레이아웃을 공유한다. 전체 47개
    필드는 ``_raw`` (KIS Element 이름 기준)에 있고, 아래는 자주 쓰는 헤드라인만 타입화한 것이다
    (2026-09-14 KRX 애프터마켓 도입으로 끝에 ``MARKET_CLS_CODE`` 가 붙어 46->47; ``_raw`` 로 접근).
    """

    symbol: str
    time: str  # HHMMSS
    current_price: Decimal
    change_sign: str  # 1상한 2상승 3보합 4하한 5하락
    change: Decimal
    change_percent: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    best_ask: Decimal
    best_bid: Decimal
    trade_volume: Decimal  # 이번 체결 수량
    accumulated_volume: Decimal
    accumulated_value: Decimal  # 누적 거래대금
    conclusion_strength: Decimal  # 체결강도
    trade_sign: str  # 체결구분 1매수(+) 3장전 5매도(-)
    business_date: str  # YYYYMMDD
    trading_halted: bool
    static_vi_reference_price: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )
