"""국내 선물·옵션 주문 와이어 빌더/재조회 (내부) -- overseas/_engine/orders.py 의 형제.

주문 실행의 안전 규칙(이중체결 방지·쓰기 재시도 금지·보수적 재조회)은 국내주식·해외와 공유하는
안전 코어(:func:`~kis_trader.domestic._engine.orders.place`)가 맡는다. 이 모듈은 그 코어에
``build_request`` 로 주입할 **국내 파생(XKFE) 주문의 와이어 요청**만 조립한다 -- 순수 함수라
오케스트레이션 없이 단독 검증된다.

KIS URL/TR-ID (KIS 명세 대조, sheet '국내선물옵션 주문'):
- 주문: ``POST /uapi/domestic-futureoption/v1/trading/order``. 주간(정규)은 실전 ``TTTO1101U`` /
  모의 ``VTTO1101U``, 야간은 실전 ``STTN1101U`` (모의 미지원). 매수/매도는 같은 TR 로 보내고
  ``SLL_BUY_DVSN_CD`` 로 가른다.
- 주문구분은 세 코드의 조합이다: ``ORD_DVSN_CD``(지정가/시장가/조건부/최유리 x day/IOC/FOK),
  ``NMPR_TYPE_CD``(호가유형), ``KRX_NMPR_CNDT_CD``(체결조건: day=0/IOC=3/FOK=4). :data:`_FO_CODES`
  가 ``(base, time_in_force)`` -> 세 코드를 고정한다(base = ``division`` 있으면 그것, 없으면
  ``order_type``). 국내주식과 코드 체계가 달라 별도 표를 둔다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..._internal._wire import format_wire_decimal
from ...errors import KISUsageError, OrderError
from ...order import _DERIVATIVE_EXCHANGE, Order, WireRequest

if TYPE_CHECKING:
    from ...transport import Environment

_PLACE_PATH = "/uapi/domestic-futureoption/v1/trading/order"

#: (session, environment) -> tr_id. 야간(STTN)은 모의 미지원이라 real 만 있다.
_PLACE_TR: dict[str, dict[str, str]] = {
    "regular": {"real": "TTTO1101U", "paper": "VTTO1101U"},
    "night": {"real": "STTN1101U"},
}

_ACCOUNT_ORD_PROCESS = "02"                  # ORD_PRCS_DVSN_CD 고정(주문전송)
_SIDE_CODE = {"sell": "01", "buy": "02"}     # SLL_BUY_DVSN_CD (매수/매도 동일 TR)

#: (base, time_in_force) -> (ORD_DVSN_CD, NMPR_TYPE_CD, KRX_NMPR_CNDT_CD).
#: base = ``division`` 있으면 그것, 없으면 ``order_type``. immediate_limit=최유리(04),
#: conditional_limit=조건부(03). 조건부/최유리는 day 만 유효한 조합만 표에 있다 -- 그 밖의
#: (조건부+IOC 등) 미매핑 조합은 조용히 바꾸지 않고 :func:`_resolve_fo_codes` 가 거부한다.
_FO_CODES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("limit", "day"): ("01", "01", "0"),
    ("limit", "ioc"): ("10", "01", "3"),
    ("limit", "fok"): ("11", "01", "4"),
    ("market", "day"): ("02", "02", "0"),
    ("market", "ioc"): ("12", "02", "3"),
    ("market", "fok"): ("13", "02", "4"),
    ("conditional_limit", "day"): ("03", "03", "0"),
    ("immediate_limit", "day"): ("04", "04", "0"),
    ("immediate_limit", "ioc"): ("14", "04", "3"),
    ("immediate_limit", "fok"): ("15", "04", "4"),
}


def is_derivative_exchange(exchange: str) -> bool:
    """``exchange`` 가 국내 파생(XKFE)이면 True. 안전 코어의 주문 라우팅에 쓴다."""
    return exchange == _DERIVATIVE_EXCHANGE


def _resolve_fo_codes(
    *, order_type: str, division: str | None, time_in_force: str
) -> tuple[str, str, str]:
    """파생 주문의 (ORD_DVSN_CD, NMPR_TYPE_CD, KRX_NMPR_CNDT_CD) 세 코드를 정한다.

    base = ``division`` 있으면 그것, 없으면 ``order_type``. 미매핑 조합(예: 조건부+IOC)은 조용히
    day/지정가로 바꾸지 않고 :class:`~kis_trader.errors.KISUsageError` 로 fail-closed 한다."""
    base = division if division else order_type
    try:
        return _FO_CODES[(base, time_in_force)]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 파생 주문구분/유효기간 조합: base={base!r}, tif={time_in_force!r}."
        ) from None


def make_order_request(
    order: Order, *, cano: str, product_code: str, environment: Environment
) -> WireRequest:
    """안전 코어(:func:`~kis_trader.domestic._engine.orders.place`)에 넘길 국내 파생 주문 빌더.

    주간(정규)·야간 공통이다. 야간(STTN)은 모의투자 미지원이라 ``paper`` 면 fail-closed. 수량은
    계약 단위 정수만 받는다. 세 주문구분 코드는 :func:`_resolve_fo_codes` 로, 시장가는 단가 0 으로
    보낸다. ``FUOP_ITEM_DVSN_CD``(상품구분)는 야간에만 채운다(주간은 공란)."""
    if order.exchange != _DERIVATIVE_EXCHANGE:
        raise OrderError(f"파생 빌더에 비-XKFE 주문이 들어왔다: {order.exchange!r} (라우팅 오류).")
    session = "night" if order.session == "night" else "regular"
    if session == "night" and environment == "paper":
        raise KISUsageError("파생 야간(STTN)은 모의투자 미지원 -- 실전에서만.")
    if order.quantity != order.quantity.to_integral_value():
        raise KISUsageError(f"파생 주문 수량은 정수여야 한다(계약 단위): {order.quantity}")
    ord_dvsn, nmpr_type, krx_cndt = _resolve_fo_codes(
        order_type=order.order_type, division=order.division, time_in_force=order.time_in_force
    )
    try:
        tr_id = _PLACE_TR[session][environment]
    except KeyError:
        raise KISUsageError(
            f"파생 주문 TR 을 찾지 못했다: session={session} / {environment}."
        ) from None
    fuop_item = order.derivative_item if session == "night" else ""
    body = {
        "ORD_PRCS_DVSN_CD": _ACCOUNT_ORD_PROCESS,
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "SLL_BUY_DVSN_CD": _SIDE_CODE[order.side],
        "SHTN_PDNO": order.symbol,
        "ORD_QTY": format_wire_decimal(order.quantity),
        "UNIT_PRICE": "0" if order.limit_price is None else format_wire_decimal(order.limit_price),
        "NMPR_TYPE_CD": nmpr_type,
        "KRX_NMPR_CNDT_CD": krx_cndt,
        "CTAC_TLNO": "",
        "FUOP_ITEM_DVSN_CD": fuop_item,
        "ORD_DVSN_CD": ord_dvsn,
    }
    return WireRequest("POST", _PLACE_PATH, tr_id, body)


def _extract_fo_output(body: Mapping[str, Any]) -> Mapping[str, Any]:
    """파생 주문 응답에서 ``output``(object)만 엄격히 뽑는다 -- 커널의 top-level 폴백을 쓰지 않는다.

    ``output`` 키가 없거나 Mapping 이 아니면 :class:`~kis_trader.errors.OrderError` 로 fail-closed
    한다(top-level 로 폴백해 엉뚱한 ODNO 를 읽으면 재조회 대상이 어긋나 오확정으로 이어진다)."""
    out = body.get("output")
    if not isinstance(out, Mapping):
        raise OrderError("파생 주문 응답에 output(object)이 없다 -- top-level 폴백 금지, 재조회 불가.")
    return out
