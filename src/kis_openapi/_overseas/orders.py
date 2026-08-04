"""해외주식 주문 요청 조립 (내부) -- (거래소, 매수/매도, 실전/모의) -> TR + 바디.

주문 실행의 안전 규칙(이중체결 방지·쓰기 재시도 금지·보수적 재조회)은 국내와 공유하는 안전
코어(:class:`~kis_openapi.store.OrderStore`)가 맡는다. 이 모듈은 그 코어에 넘길 **해외 주문의
와이어 요청**만 조립한다 -- 순수 함수라 오케스트레이션 없이 단독 검증된다.

KIS URL/TR-id (원장 대조, sheet '해외주식 주문'):
- 주문: ``POST /uapi/overseas-stock/v1/trading/order``. TR 은 시장 x 매수/매도 x 실전/모의로 갈린다
  (아래 :data:`_ORDER_TR`). 시세 조회의 거래소코드(NAS/NYS/...)와 주문 거래소코드(NASD/NYSE/...)가
  다르므로 :data:`_ORDER_EXCHANGE` 로 매핑한다. 해외 주문은 지정가(ORD_DVSN=00) 중심이다.
"""

from __future__ import annotations

from decimal import Decimal

from .._wire import format_wire_decimal
from ..errors import KisUsageError
from ..transport import Environment

_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"

#: 시세 거래소코드(EXCD) -> (주문 거래소코드 OVRS_EXCG_CD, 시장 그룹). 원장 코드표.
_ORDER_EXCHANGE: dict[str, tuple[str, str]] = {
    "NAS": ("NASD", "US"), "NYS": ("NYSE", "US"), "AMS": ("AMEX", "US"),
    "HKS": ("SEHK", "HK"),
    "SHS": ("SHAA", "SH"),
    "SZS": ("SZAA", "SZ"),
    "TSE": ("TKSE", "JP"),
    "HNX": ("HASE", "VN"), "HSX": ("VNSE", "VN"),
}

#: (시장 그룹, 매수/매도, 실전/모의) -> tr_id. 원장 코드표('해외주식 주문').
_ORDER_TR: dict[tuple[str, str, Environment], str] = {
    ("US", "buy", "real"): "TTTT1002U", ("US", "sell", "real"): "TTTT1006U",
    ("US", "buy", "demo"): "VTTT1002U", ("US", "sell", "demo"): "VTTT1001U",
    ("JP", "buy", "real"): "TTTS0308U", ("JP", "sell", "real"): "TTTS0307U",
    ("JP", "buy", "demo"): "VTTS0308U", ("JP", "sell", "demo"): "VTTS0307U",
    ("SH", "buy", "real"): "TTTS0202U", ("SH", "sell", "real"): "TTTS1005U",
    ("SH", "buy", "demo"): "VTTS0202U", ("SH", "sell", "demo"): "VTTS1005U",
    ("HK", "buy", "real"): "TTTS1002U", ("HK", "sell", "real"): "TTTS1001U",
    ("HK", "buy", "demo"): "VTTS1002U", ("HK", "sell", "demo"): "VTTS1001U",
    ("SZ", "buy", "real"): "TTTS0305U", ("SZ", "sell", "real"): "TTTS0304U",
    ("SZ", "buy", "demo"): "VTTS0305U", ("SZ", "sell", "demo"): "VTTS0304U",
    ("VN", "buy", "real"): "TTTS0311U", ("VN", "sell", "real"): "TTTS0310U",
    ("VN", "buy", "demo"): "VTTS0311U", ("VN", "sell", "demo"): "VTTS0310U",
}
_ORD_DVSN_LIMIT = "00"  # 지정가


def build_order_request(
    *,
    side: str,
    symbol: str,
    quantity: Decimal,
    limit_price: Decimal | None,
    exchange: str,
    cano: str,
    product_code: str,
    environment: Environment,
) -> tuple[str, str, str, dict[str, str]]:
    """해외 주문의 (method, path, tr_id, body) 를 조립한다.

    ``exchange`` 는 시세 거래소코드(NAS/NYS/...). 해외 주문은 지정가만 지원하므로 ``limit_price`` 가
    필요하다(시장가/MOO/MOC 등은 시장별로 제약이 달라 아직 미지원). ``quantity`` 는 정수(주 단위)."""
    if limit_price is None:
        raise KisUsageError("해외 주문은 지정가만 지원한다 -- price 를 지정하라(시장가 미지원).")
    if quantity != quantity.to_integral_value():
        raise KisUsageError(f"해외 주문 수량은 정수여야 한다(주 단위): {quantity}")
    try:
        order_exchange, market = _ORDER_EXCHANGE[exchange]
    except KeyError:
        raise KisUsageError(
            f"해외 주문을 지원하지 않는 거래소코드: {exchange!r} ({'/'.join(_ORDER_EXCHANGE)})."
        ) from None
    try:
        tr_id = _ORDER_TR[(market, side, environment)]
    except KeyError:
        raise KisUsageError(
            f"해외 주문 TR 을 찾지 못했다: 시장 {market} / {side} / {environment}."
        ) from None
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "OVRS_EXCG_CD": order_exchange,
        "PDNO": symbol,
        "ORD_QTY": format_wire_decimal(quantity),
        "OVRS_ORD_UNPR": format_wire_decimal(limit_price),
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": _ORD_DVSN_LIMIT,
    }
    if side == "sell":
        body["SLL_TYPE"] = "00"        # 매도 표시(매수는 필드 없음)
    return "POST", _ORDER_PATH, tr_id, body
