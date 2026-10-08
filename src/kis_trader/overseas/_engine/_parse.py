"""해외 계좌/주문 엔진 공용 파서 -- account/orders 가 함께 쓰는 헬퍼.

시장 그룹 매핑·매도매수 코드표·페이지 상한과, 통화 금액/십진 파싱 헬퍼를 모아 두 엔진이
같은 규칙으로 응답을 읽게 한다(주문조회가 orders 로 옮겨가도 account<->orders 순환 import 가
생기지 않도록 공용 헬퍼는 여기 둔다).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

# 매매구분코드는 _internal/_parse 로 단일화 -- 형제 엔진은 종전대로 이 모듈에서 가져온다(re-export).
from ..._internal._parse import _side_from_code as _side_from_code  # noqa: PLC0414 -- re-export
from ..._internal._wire import field_decimal_or_zero, required_decimal
from ...money import Money

_MAX_PAGES = 100

#: 해외 잔고 시장 -> (OVRS_EXCG_CD, TR_CRCY_CD). KIS 코드표. 미국은 NASD(실전=미국전체).
_MARKETS: dict[str, tuple[str, str]] = {
    "US": ("NASD", "USD"),
    "HK": ("SEHK", "HKD"),
    "CN_SH": ("SHAA", "CNY"),
    "CN_SZ": ("SZAA", "CNY"),
    "JP": ("TKSE", "JPY"),
    "VN_HN": ("HASE", "VND"),
    "VN_HCM": ("VNSE", "VND"),
}


def _money(row: Mapping[str, Any], key: str, currency: str) -> Money:
    return Money(required_decimal(row.get(key), key), currency)


def _decimal_or_zero(row: Mapping[str, Any], key: str) -> Decimal:
    return field_decimal_or_zero(row.get(key), key)
