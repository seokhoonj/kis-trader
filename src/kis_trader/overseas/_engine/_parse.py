"""해외 계좌/주문 엔진 공용 파서 -- account/orders 가 함께 쓰는 헬퍼.

시장 그룹 매핑·매도매수 코드표·페이지 상한과, 통화 금액/십진 파싱 헬퍼를 모아 두 엔진이
같은 규칙으로 응답을 읽게 한다(주문조회가 orders 로 옮겨가도 account<->orders 순환 import 가
생기지 않도록 공용 헬퍼는 여기 둔다).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from ..._internal._wire import optional_decimal, required_decimal
from ...errors import KISError
from ...money import Money
from ...order import Side

_MAX_PAGES = 100
_SIDE: dict[str, Side] = {"01": "sell", "02": "buy"}


def _side_from_code(code: object) -> Side:
    """벤더 매매구분코드(01 매도 / 02 매수)를 방향으로 -- 알 수 없는 코드는 fail-closed(:class:`KISError`).
    ``side=""`` 로 뭉개면 buy/sell 어느 쪽도 아닌 주문 레코드가 새어 이후 오귀속/오매칭을 부른다."""
    text = str(code or "").strip()
    try:
        return _SIDE[text]
    except KeyError:
        raise KISError(f"알 수 없는 매매구분코드: {text!r} (01 매도 / 02 매수만 유효).") from None

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
    amount = optional_decimal(row.get(key), key)
    return Decimal(0) if amount is None else amount
