"""해외선물옵션(08) 발주 와이어 빌더 (내부) -- 국내 파생·채권 주문 빌더(domestic/_engine/derivative_orders.py,
domestic/_engine/bond_orders.py)와 같은 주입 구조.

주문 실행의 안전 규칙(이중체결 방지·쓰기 재시도 금지·보수적 재조회)은 국내주식·해외·파생·채권과
공유하는 안전 코어(:func:`~kis_trader.domestic._engine.orders.place`)가 맡는다. 이 모듈은 그
코어에 ``build_request`` 로 주입할 **해외선물옵션(OSFO) 발주의 와이어 요청**만 조립하고(순수 함수라
오케스트레이션 없이 단독 검증된다), 접수 응답에서 output(``{ORD_DT, ODNO}``)을 엄격히 뽑는 파서를
제공한다. ODNO 는 안전 코어가 ``order_id`` 로 뽑고, ORD_DT 는 이 파서가 시장 중립 키
(:data:`~kis_trader.domestic._engine.orders._RECEIPT_DATE_KEY`)로 정규화해 코어가 ``receipt_date`` 로
영속하게 한다 -- 이후 정정·취소가 원주문일자(ORGN_ORD_DT)로 대상을 특정할 수 있게 하려는 것이다.

KIS URL/TR-ID (KIS 명세 대조, sheet '해외선물옵션 주문'):
- 발주: ``POST /uapi/overseas-futureoption/v1/trading/order``, 실전 ``OTFM3001U`` (**모의투자 미지원**).
  매수/매도는 같은 TR 로 보내고 ``SLL_BUY_DVSN_CD`` 로 가른다(매수 "02" / 매도 "01"). 가격구분
  (``PRIC_DVSN_CD``)은 지정가 "1" / 시장가 "2" / STOP "3" 이며, 지정가는 ``FM_LIMIT_ORD_PRIC``,
  STOP 은 ``FM_STOP_ORD_PRIC`` 에 값을 싣는다. 청산(헤지청산) 관련 필드는 v1 에서 공란이다. 정정
  (OTFM3002U)·취소(OTFM3003U)는 원주문일자(ORGN_ORD_DT) 지목과 라이브 검증이 필요한 별도 슬라이스라,
  이 빌더는 발주만 조립한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..._internal._wire import format_wire_decimal
from ...domestic._engine.orders import _RECEIPT_DATE_KEY
from ...errors import KISUsageError, OrderError
from ...order import _OVERSEAS_FO_EXCHANGE, Order, WireRequest

if TYPE_CHECKING:
    from ...transport import Environment

_PLACE_PATH = "/uapi/overseas-futureoption/v1/trading/order"
_PLACE_TR = "OTFM3001U"                             # 실전 전용(모의 미지원)

_SIDE_CODE = {"sell": "01", "buy": "02"}            # SLL_BUY_DVSN_CD (매수/매도 동일 TR)
#: order_type -> PRIC_DVSN_CD(가격구분). 지정가 "1" / 시장가 "2" / STOP "3". 스탑지정가(stop_limit)는
#: 해외선물옵션 v1 에서 미지원이라 여기 없고, 미매핑 order_type 은 :func:`make_order_request` 가 거부한다.
_PRICE_DIVISION = {"limit": "1", "market": "2", "stop": "3"}


def is_overseas_fo_exchange(exchange: str) -> bool:
    """``exchange`` 가 해외선물옵션(OSFO)이면 True. 안전 코어의 주문 라우팅에 쓴다."""
    return exchange == _OVERSEAS_FO_EXCHANGE


def make_order_request(
    order: Order, *, cano: str, product_code: str, environment: Environment
) -> WireRequest:
    """안전 코어(:func:`~kis_trader.domestic._engine.orders.place`)에 넘길 해외선물옵션 발주 빌더.

    해외선물옵션은 **실전 전용**(모의투자 미지원)이라 ``paper`` 면 fail-closed(client 라우팅에서
    먼저 막지만 빌더에서도 방어). 수량은 계약 단위 정수만 받고, 가격구분(``PRIC_DVSN_CD``)은
    ``order_type`` 에서 정한다(지정가 "1" / 시장가 "2" / STOP "3") -- 지원하지 않는 order_type
    (스탑지정가 등)은 조용히 지정가로 바꾸지 않고 거부한다. 지정가는 ``FM_LIMIT_ORD_PRIC``,
    STOP 은 ``FM_STOP_ORD_PRIC`` 에만 값을 싣고 나머지 가격은 공란이다. 헤지청산(``FM_LQD_*``)
    필드는 v1 에서 공란 고정이며, 체결조건(``CCLD_CNDT_CD``)은 시장가면 "2"(시장가) 아니면 "6"(EOD
    지정가)로, 복합주문 없음("0")·예약 아님("N")도 고정값이다."""
    if order.exchange != _OVERSEAS_FO_EXCHANGE:
        raise OrderError(
            f"해외선물옵션 빌더에 비-OSFO 주문이 들어왔다: {order.exchange!r} (라우팅 오류)."
        )
    if environment == "paper":
        raise KISUsageError("해외선물옵션 주문은 모의투자 미지원 -- 실전에서만.")
    if order.quantity != order.quantity.to_integral_value():
        raise KISUsageError(f"해외선물옵션 주문 수량은 계약 단위 정수여야 한다: {order.quantity}")
    try:
        price_division = _PRICE_DIVISION[order.order_type]
    except KeyError:
        raise KISUsageError(
            f"해외선물옵션 주문은 지정가/시장가/STOP 만 지원한다: order_type={order.order_type!r}."
        ) from None
    # order_type 이 곧 가격구분을 정하므로 가격 슬롯도 그에 맞게 채운다: 지정가는 지정가만, STOP 은
    # 스탑가만(Order 생성 규칙이 limit/stop 가격의 존재를 이미 강제하므로 여기선 배치만 한다).
    limit_price = (
        format_wire_decimal(order.limit_price)
        if order.order_type == "limit" and order.limit_price is not None
        else ""
    )
    stop_price = (
        format_wire_decimal(order.stop_price)
        if order.order_type == "stop" and order.stop_price is not None
        else ""
    )
    # 체결조건: 시장가는 "2"(시장가), 그 외는 "6"(EOD 지정가). STOP 의 CCLD_CNDT_CD 는 명세에 명시값이
    # 없어 "6" 을 쓰되 라이브 확인 전까지 미검증이다(지정가와 같은 값으로 둔다).
    ccld_cndt = "2" if order.order_type == "market" else "6"
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "OVRS_FUTR_FX_PDNO": order.symbol,
        "SLL_BUY_DVSN_CD": _SIDE_CODE[order.side],
        "FM_LQD_USTL_CCLD_DT": "",                 # 헤지청산 대상 체결일자 -- v1 공란
        "FM_LQD_USTL_CCNO": "",                    # 헤지청산 대상 체결번호 -- v1 공란
        "PRIC_DVSN_CD": price_division,
        "FM_LIMIT_ORD_PRIC": limit_price,
        "FM_STOP_ORD_PRIC": stop_price,
        "FM_ORD_QTY": format_wire_decimal(order.quantity),
        "FM_LQD_LMT_ORD_PRIC": "",                 # 헤지청산 지정가 -- v1 공란
        "FM_LQD_STOP_ORD_PRIC": "",                # 헤지청산 스탑가 -- v1 공란
        "CCLD_CNDT_CD": ccld_cndt,                 # 체결조건: 시장가 "2" / 그 외 EOD "6"
        "CPLX_ORD_DVSN_CD": "0",                   # 복합주문 아님
        "ECIS_RSVN_ORD_YN": "N",                   # 예약주문 아님
        "FM_HDGE_ORD_SCRN_YN": "N",                # 헤지주문화면 아님
    }
    return WireRequest("POST", _PLACE_PATH, _PLACE_TR, body)


def extract_output(body: Mapping[str, Any]) -> Mapping[str, Any]:
    """해외선물옵션 발주 응답에서 ``output``(object)만 엄격히 뽑는다 -- 커널의 top-level 폴백을 쓰지 않는다.

    ``output`` 키가 없거나 Mapping 이 아니면 :class:`~kis_trader.errors.OrderError` 로 fail-closed
    한다(top-level 로 폴백해 엉뚱한 ODNO/ORD_DT 를 읽으면 재조회·정정취소 대상이 어긋난다). 안전
    코어(place)가 이 매핑에서 ``ODNO`` 를 ``order_id`` 로 뽑는다. 이 응답의 주문일자(``ORD_DT``)는
    시장 중립 키(:data:`~kis_trader.domestic._engine.orders._RECEIPT_DATE_KEY`)로 정규화해 넣어, 코어가
    자산별 필드명을 모른 채 ``receipt_date`` 로 영속하게 한다(다른 자산은 이 키를 안 채워 None)."""
    out = body.get("output")
    if not isinstance(out, Mapping):
        raise OrderError(
            "해외선물옵션 발주 응답에 output(object)이 없다 -- top-level 폴백 금지, 재조회 불가."
        )
    normalized = dict(out)
    receipt_date = out.get("ORD_DT")
    if receipt_date:
        normalized[_RECEIPT_DATE_KEY] = receipt_date
    return normalized
