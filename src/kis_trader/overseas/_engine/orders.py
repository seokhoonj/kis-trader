"""해외주식 주문 요청 조립 (내부) -- (거래소, 매수/매도, 실전/모의) -> TR + 바디.

주문 실행의 안전 규칙(이중체결 방지·쓰기 재시도 금지·보수적 재조회)은 국내와 공유하는 안전
코어(:class:`~kis_trader.store.OrderStore`)가 맡는다. 이 모듈은 그 코어에 넘길 **해외 주문의
와이어 요청**만 조립한다 -- 순수 함수라 오케스트레이션 없이 단독 검증된다.

KIS URL/TR-ID (대표 예시 -- 전체는 각 함수의 경로/TR-ID 상수 참조):
- 주문: ``POST /uapi/overseas-stock/v1/trading/order``. TR 은 시장 x 매수/매도 x 실전/모의로 갈린다
  (아래 :data:`_ORDER_TR`). 시세 조회의 거래소코드(NAS/NYS/...)와 주문 거래소코드(NASD/NYSE/...)가
  다르므로 :data:`_ORDER_EXCHANGE` 로 매핑한다. 해외 주문은 지정가(ORD_DVSN=00) 중심이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..._internal._datetime import _KST, _parse_hhmmss
from ..._internal._response import _CONTINUATION_END, _fetch_paginated_rows
from ..._internal._wire import format_wire_decimal, normalize_order_number
from ...errors import KISError, KISUsageError, OrderTimeoutError
from ...order import (
    AlgoStrategy,
    ChangeAction,
    ImmediateOrderFingerprint,
    Order,
    OrderType,
    Side,
    TimeInForce,
    WireRequest,
    reject_bad_change_price_shape,
)
from ...report import TERMINAL_ORDER_STATUSES, ExecutionReport, OrderStatus
from ...risk import RiskLimits
from ...store import OrderStore
from ...transport import Environment, Transport, TransportTimeoutError
from ..entities.orders import OverseasAlgoExecution, OverseasAlgoOrder, OverseasOpenOrder
from . import market_data
from ._parse import _MARKETS, _MAX_PAGES, _decimal_or_zero, _money, _side_from_code

_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"
_CHANGE_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
#: (시장 그룹, 실전/모의) -> 정정취소 tr_id. KIS 공식('해외주식 정정취소주문', v1_해외주식-003)은
#: 거래소별로 다른 TR 을 쓴다 -- 미국만 단일 TTTT1004U 가 전 미국 거래소(NASD/NYSE/AMEX)를 포괄하고
#: 아시아권은 거래소별로 나뉜다. 배치(`_ORDER_TR`)가 이미 시장별이라 정정취소도 같은 축으로 맞춘다.
_CHANGE_TR: dict[tuple[str, Environment], str] = {
    ("US", "real"): "TTTT1004U", ("US", "paper"): "VTTT1004U",
    ("HK", "real"): "TTTS1003U", ("HK", "paper"): "VTTS1003U",
    ("JP", "real"): "TTTS0309U", ("JP", "paper"): "VTTS0309U",
    ("SH", "real"): "TTTS0302U", ("SH", "paper"): "VTTS0302U",
    ("SZ", "real"): "TTTS0306U", ("SZ", "paper"): "VTTS0306U",
    ("VN", "real"): "TTTS0312U", ("VN", "paper"): "VTTS0312U",
}
#: KIS 공식상 취소만 가능한 시장 그룹(정정 TR 없음) -- 상해/심천/베트남. 정정 요청은 fail-closed.
_CHANGE_CANCEL_ONLY_MARKETS = frozenset({"SH", "SZ", "VN"})

#: 시세 거래소코드(EXCD) -> (주문 거래소코드 OVRS_EXCG_CD, 시장 그룹). KIS 코드표.
_ORDER_EXCHANGE: dict[str, tuple[str, str]] = {
    "NAS": ("NASD", "US"), "NYS": ("NYSE", "US"), "AMS": ("AMEX", "US"),
    "HKS": ("SEHK", "HK"),
    "SHS": ("SHAA", "SH"),
    "SZS": ("SZAA", "SZ"),
    "TSE": ("TKSE", "JP"),
    "HNX": ("HASE", "VN"), "HSX": ("VNSE", "VN"),
}

#: (시장 그룹, 매수/매도, 실전/모의) -> tr_id. KIS 코드표('해외주식 주문').
_ORDER_TR: dict[tuple[str, Side, Environment], str] = {
    ("US", "buy", "real"):  "TTTT1002U", ("US", "sell", "real"):  "TTTT1006U",
    ("US", "buy", "paper"): "VTTT1002U", ("US", "sell", "paper"): "VTTT1001U",
    ("JP", "buy", "real"):  "TTTS0308U", ("JP", "sell", "real"):  "TTTS0307U",
    ("JP", "buy", "paper"): "VTTS0308U", ("JP", "sell", "paper"): "VTTS0307U",
    ("SH", "buy", "real"):  "TTTS0202U", ("SH", "sell", "real"):  "TTTS1005U",
    ("SH", "buy", "paper"): "VTTS0202U", ("SH", "sell", "paper"): "VTTS1005U",
    ("HK", "buy", "real"):  "TTTS1002U", ("HK", "sell", "real"):  "TTTS1001U",
    ("HK", "buy", "paper"): "VTTS1002U", ("HK", "sell", "paper"): "VTTS1001U",
    ("SZ", "buy", "real"):  "TTTS0305U", ("SZ", "sell", "real"):  "TTTS0304U",
    ("SZ", "buy", "paper"): "VTTS0305U", ("SZ", "sell", "paper"): "VTTS0304U",
    ("VN", "buy", "real"):  "TTTS0311U", ("VN", "sell", "real"):  "TTTS0310U",
    ("VN", "buy", "paper"): "VTTS0311U", ("VN", "sell", "paper"): "VTTS0310U",
}
_ORD_DVSN_LIMIT = "00"  # 지정가
#: 미국주식 알고리즘 분할주문 전략 -> 주문구분(ORD_DVSN). 지정가(00) 대신 실린다. KIS 코드표(공지 2025-05-23).
_ORD_DVSN_ALGO = {"twap": "35", "vwap": "36"}
#: 알고리즘주문시간구분코드(ALGO_ORD_TMD_DVSN_CD) -- 시간창을 주면 "00"(직접입력, START/END 필수),
#: 안 주면 "02"(정규장 종료까지 집행). 미국주식 algo(TWAP/VWAP) 전용 필드.
_ALGO_TMD_DIRECT = "00"
_ALGO_TMD_CLOSE = "02"
#: 미국주식 algo 가능 시장 그룹(`_ORDER_EXCHANGE` 의 market 값). algo 는 미국만.
_ALGO_MARKET = "US"


def make_order_request_from_fields(
    *,
    side: Side,
    symbol: str,
    quantity: Decimal,
    limit_price: Decimal | None,
    exchange: str,
    cano: str,
    product_code: str,
    environment: Environment,
    order_type: OrderType = "limit",
    time_in_force: TimeInForce = "day",
    algo_strategy: AlgoStrategy | None = None,
    algo_start: str = "",
    algo_end: str = "",
) -> WireRequest:
    """해외 주문의 :class:`~kis_trader.order.WireRequest` 를 조립한다.

    ``exchange`` 는 시세 거래소코드(NAS/NYS/...). 해외 주문은 지금 **지정가·day 만** 지원한다
    (시장가/MOO/MOC·IOC/FOK 등은 시장별 제약이 달라 미구현) -- 도메스틱처럼 그 밖은 조용히 day
    지정가로 바꾸지 않고 fail-closed 로 거부한다. ``quantity`` 는 정수(주 단위).

    미국 주문의 **주문가능시간**(거래소·주문유형별, 한국시간, 서머타임 여부에 따라 다름)과 **입력
    가격범위**(대략 매수 현재가 -97%~+30%, 매도 -20%~+200%; 현지 브로커 정책에 따라 상이)는
    **KIS/브로커가 접수 시 강제**한다(범위 밖이면 거부). 이 클라이언트는 그 시간/범위를 사전 차단
    하지 않는다 -- 서머타임 전환·휴장 등 엣지에서 정상 주문을 잘못 막을 위험이 있어, 판정은 KIS 에
    맡기고 명확한 거부 메시지를 전달한다. 사용자측 fat-finger 방지가 필요하면 세션의
    :class:`~kis_trader.risk.RiskLimits` (설정 가능한 지정가 collar)를 쓰라."""
    if order_type != "limit":
        raise KISUsageError(f"해외 주문은 지정가만 지원한다(order_type={order_type!r}).")
    if time_in_force != "day":
        raise KISUsageError(f"해외 주문은 아직 day 만 지원한다(time_in_force={time_in_force!r}).")
    if limit_price is None:
        raise KISUsageError("해외 주문은 지정가만 지원한다 -- limit_price 를 지정하라(시장가 미지원).")
    if quantity != quantity.to_integral_value():
        raise KISUsageError(f"해외 주문 수량은 정수여야 한다(주 단위): {quantity}")
    try:
        order_exchange, market = _ORDER_EXCHANGE[exchange]
    except KeyError:
        raise KISUsageError(
            f"해외 주문을 지원하지 않는 거래소코드: {exchange!r} ({'/'.join(_ORDER_EXCHANGE)})."
        ) from None
    try:
        tr_id = _ORDER_TR[(market, side, environment)]
    except KeyError:
        raise KISUsageError(
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
    if algo_strategy is not None:
        # 미국주식 algo(TWAP/VWAP): ORD_DVSN 을 35/36 으로 바꾸고 시간구분/시간창을 싣는다. 비-algo 는
        # 이 블록을 건너뛰어 와이어가 종전과 바이트 동일하다(회귀 0). algo 는 미국 실전 전용 -- Order 가
        # 이미 미국·지정가·정규세션을 강제하지만, 와이어 빌더에서 시장·환경을 한 번 더 fail-closed 한다.
        if environment == "paper":
            raise KISUsageError("미국주식 algo(TWAP/VWAP) 주문은 모의투자 미지원 -- 실전에서만.")
        if market != _ALGO_MARKET:
            raise KISUsageError(f"미국주식 algo(TWAP/VWAP)는 미국 거래소만 지원한다: {exchange!r}.")
        try:
            body["ORD_DVSN"] = _ORD_DVSN_ALGO[algo_strategy]
        except KeyError:
            raise KISUsageError(f"지원하지 않는 algo 전략: {algo_strategy!r} (twap/vwap).") from None
        if algo_start or algo_end:
            body["ALGO_ORD_TMD_DVSN_CD"] = _ALGO_TMD_DIRECT
            body["START_TIME"] = algo_start
            body["END_TIME"] = algo_end
        else:
            body["ALGO_ORD_TMD_DVSN_CD"] = _ALGO_TMD_CLOSE
    if side == "sell":
        body["SLL_TYPE"] = "00"        # 매도 표시(매수는 필드 없음)
    return WireRequest("POST", _ORDER_PATH, tr_id, body)


def is_overseas_exchange(exchange: str) -> bool:
    """``exchange`` 가 해외 거래소코드(주문 지원)면 True. 안전 코어의 주문 라우팅에 쓴다."""
    return exchange in _ORDER_EXCHANGE


# --- 거래소 -> 리스크검사 통화 (금액 한도 통화 판별) ---------------------------
#: 주문 시장 그룹 -> **리스크 검사용** 통화(결제통화 자체가 아니다). 시장 그룹 축으로 고정된 값이라,
#: USD 로 결제되는 홍콩(HK) 카운터나 상해/심천 B주식(SH-B, SZ-B)도 그룹 통화로 검사된다 -- 그래서
#: 그 종목들의 실효 한도는 느슨해질 수 있다(아래 리스크 docstring 참조). 그룹 축은 _ORDER_EXCHANGE 와
#: 한 소스라, 주문 가능한 거래소는 반드시 통화를 가진다(완전성은 테스트로 고정). 미지 거래소/시장
#: 그룹은 resolve 가 None 을 돌려 호출자가 fail-closed 한다. 종목별 실제 통화는 시세상세의 ``curr``
#: 필드로 알 수 있으나, 주문마다 그것을 유도하는 것은 보류한다(비용: 모든 해외 지정가 주문에 스로틀되는
#: 시세 조회가 한 번 더 붙는다). 홍콩(HK)은 HKD 로 고정 매핑한다 -- HKD/CNY/USD 복수 카운터(스톡커넥트
#: 등)는 주문 와이어에 통화 필드가 없어(KIS 가 PDNO 로 추론) 주문별 통화를 판별하지 않는다.
_RISK_CURRENCY_BY_MARKET: dict[str, str] = {
    "US": "USD", "HK": "HKD", "SH": "CNY", "SZ": "CNY", "JP": "JPY", "VN": "VND",
}


def resolve_risk_currency(exchange: str) -> str | None:
    """해외 주문 거래소코드(EXCD) -> **리스크 검사용** 통화(ISO). 시장 그룹에 고정된 검사 통화로,
    종목별 실제 결제통화가 아니다(USD 결제 HK 카운터·SH-B/SZ-B 는 그룹 통화로 검사돼 캡이 느슨할 수
    있다). 미지원 거래소/시장 그룹은 None(호출자가 거부)."""
    entry = _ORDER_EXCHANGE.get(exchange)
    if entry is None:
        return None
    _, market = entry
    # 미래에 _ORDER_EXCHANGE 에 새 시장 그룹이 들어오고 _RISK_CURRENCY_BY_MARKET 에 빠지면, 날 KeyError
    # 대신 계약대로 None 으로 degrade 한다(호출자가 fail-closed 거부). 완전성은 테스트로 고정.
    return _RISK_CURRENCY_BY_MARKET.get(market)


def run_pre_trade_risk(transport: Transport, order: Order, risk: RiskLimits) -> None:
    """해외주식 사전 리스크 점검(해외 리스크 진입점). 거래소로 리스크 검사 통화를 판별해 그 통화의
    금액 한도로 검사한다.

    참조가가 필요한 한도(collar)는 주문 거래소의 해외 현재가를 조회한다 -- 조회 실패/빈 값은 잡지
    않고 그대로 올린다(fail-closed: 한도를 확인 못 하면 주문을 보내지 않는다). place(발주)와
    정정(modify) 양쪽이 쓴다. 거부는 두 종류다 -- 잘못된 입력/판별 불가 통화는
    :class:`~kis_trader.errors.KISUsageError`, 한도 초과(캡 위반)는
    :class:`~kis_trader.errors.PreTradeRiskError`."""
    currency = resolve_risk_currency(order.exchange)
    if currency is None:
        raise KISUsageError(
            f"해외 거래소 {order.exchange!r} 의 결제통화를 판별할 수 없어 리스크 한도를 적용할 수 "
            "없다 -- 지원 거래소로 주문하거나 risk 없는 세션에서 내라."
        )
    reference_price = None
    if risk.needs_reference_price(order):
        # 참조가(collar 등)가 필요해도, 적용할 통화 한도가 없으면 이 주문은 어차피 금액 한도
        # 단계에서 거부된다(해외는 통화별 캡 필수, fail-closed). 그 경우 스로틀되는 현재가 조회를
        # 낭비하지 않고, 조회 없이 check 로 같은 거부를 올린다(거부 결과는 바뀌지 않는다).
        by_currency = risk.max_order_notional_by_currency
        if by_currency is not None and currency in by_currency:
            reference_price = market_data.fetch_current_price(
                transport, symbol=order.symbol, exchange=order.exchange
            ).current_price
    risk.check(order, reference_price=reference_price, currency=currency)


def make_order_request(
    order: Order, *, cano: str, product_code: str, environment: Environment
) -> WireRequest:
    """안전 코어(:func:`~kis_trader._order_core.place`)에 넘길 해외 주문 빌더.

    :class:`~kis_trader.order.Order` 를 :func:`make_order_request_from_fields` 인자로 풀어 넘긴다.
    ``order.exchange`` 는 시세 거래소코드(NAS/NYS/...)를 담고, ``order_type``/``time_in_force`` 도 넘겨
    미지원 조합은 거기서 fail-closed 로 거부된다."""
    return make_order_request_from_fields(
        side=order.side, symbol=order.symbol, quantity=order.quantity,
        limit_price=order.limit_price, exchange=order.exchange,
        cano=cano, product_code=product_code, environment=environment,
        order_type=order.order_type, time_in_force=order.time_in_force,
        algo_strategy=order.algo_strategy,
        algo_start=order.algo_start, algo_end=order.algo_end,
    )


#: 미국 오버나이트 거래(daytime) 엔드포인트/TR (모의 미지원). 매수 TTTS6036U / 매도 TTTS6037U,
#: 정정취소 TTTS6038U. 미국(NASD/NYSE/AMEX)만·지정가만.
_OVERNIGHT_ORDER_PATH = "/uapi/overseas-stock/v1/trading/daytime-order"
_OVERNIGHT_ORDER_TR = {"buy": "TTTS6036U", "sell": "TTTS6037U"}
_OVERNIGHT_CHANGE_PATH = "/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl"
_OVERNIGHT_CHANGE_TR = "TTTS6038U"
#: 미국 오버나이트 거래 가능 시장 그룹(위 _ORDER_EXCHANGE 의 market 값). 미국만.
_OVERNIGHT_MARKET = "US"


def make_overnight_order_request(
    order: Order, *, cano: str, product_code: str, environment: Environment
) -> WireRequest:
    """안전 코어(place)에 넘길 **미국 오버나이트 거래** 주문 빌더 -- 정규 해외주문과 같은 즉시체결·ODNO
    응답이라 dedup/무재시도/reconcile 안전 코어를 공유한다. **모의투자 미지원**, 미국(NASD/NYSE/
    AMEX)·지정가만(주문 정체성의 세션 구분은 :class:`Order` 가 생성 시점에 검증)."""
    if environment == "paper":
        raise KISUsageError("미국 오버나이트 거래 주문(daytime-order)은 모의투자 미지원 -- 실전에서만.")
    if order.limit_price is None or order.order_type != "limit":
        raise KISUsageError("미국 오버나이트 거래는 지정가만 지원한다 -- limit_price 를 지정하라.")
    if order.quantity != order.quantity.to_integral_value():
        raise KISUsageError(f"주문 수량은 정수여야 한다(주 단위): {order.quantity}")
    try:
        order_exchange, market = _ORDER_EXCHANGE[order.exchange]
    except KeyError:
        raise KISUsageError(
            f"미국 오버나이트 거래를 지원하지 않는 거래소코드: {order.exchange!r}."
        ) from None
    if market != _OVERNIGHT_MARKET:
        raise KISUsageError(f"미국 오버나이트 거래는 미국 거래소만 지원한다: {order.exchange!r}.")
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "OVRS_EXCG_CD": order_exchange,
        "PDNO": order.symbol,
        "ORD_QTY": format_wire_decimal(order.quantity),
        "OVRS_ORD_UNPR": format_wire_decimal(order.limit_price),
        "CTAC_TLNO": "",
        "MGCO_APTM_ODNO": "",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": _ORD_DVSN_LIMIT,
    }
    return WireRequest("POST", _OVERNIGHT_ORDER_PATH, _OVERNIGHT_ORDER_TR[order.side], body)


def make_overnight_change_request(
    *, original_report: ExecutionReport, original_fingerprint: ImmediateOrderFingerprint,
    action: ChangeAction, quantity: Decimal, limit_price: Decimal | None,
    cano: str, product_code: str, environment: Environment,
) -> WireRequest:
    """미국 오버나이트 거래 정정·취소 요청 와이어(daytime-order-rvsecncl TTTS6038U). **모의투자 미지원**."""
    # 지정가 전용 자산이라 가격형상 규칙을 공유 헬퍼로 강제한다(정정=>가격>0, 취소=>가격 없음).
    reject_bad_change_price_shape(action, limit_price)
    if environment == "paper":
        raise KISUsageError("미국 오버나이트 거래 정정·취소는 모의투자 미지원 -- 실전에서만.")
    try:
        order_exchange, market = _ORDER_EXCHANGE[original_fingerprint.exchange]
    except KeyError:
        raise KISUsageError(
            f"미국 오버나이트 거래 정정·취소를 지원하지 않는 거래소코드: {original_fingerprint.exchange!r}."
        ) from None
    if market != _OVERNIGHT_MARKET:
        raise KISUsageError("미국 오버나이트 거래 정정·취소는 미국 거래소만 지원한다.")
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "OVRS_EXCG_CD": order_exchange,
        "PDNO": original_report.symbol,
        "ORGN_ODNO": str(original_report.order_id),
        "RVSE_CNCL_DVSN_CD": "02" if action == "cancel" else "01",
        "ORD_QTY": format_wire_decimal(quantity),
        "OVRS_ORD_UNPR": "0" if limit_price is None else format_wire_decimal(limit_price),
        "CTAC_TLNO": "",
        "MGCO_APTM_ODNO": "",
        "ORD_SVR_DVSN_CD": "0",
    }
    return WireRequest("POST", _OVERNIGHT_CHANGE_PATH, _OVERNIGHT_CHANGE_TR, body)


def make_change_request(
    *, original_report: ExecutionReport, original_fingerprint: ImmediateOrderFingerprint,
    action: ChangeAction, quantity: Decimal, limit_price: Decimal | None,
    cano: str, product_code: str, environment: Environment,
) -> WireRequest:
    """해외주식 정정·취소 요청을 거래소별 공식 TR 와이어로 조립한다(KIS 공식: 시장별 TR)."""
    # 지정가 전용 자산이라 가격형상 규칙을 공유 헬퍼로 강제한다(정정=>가격>0, 취소=>가격 없음).
    reject_bad_change_price_shape(action, limit_price)
    try:
        order_exchange, market = _ORDER_EXCHANGE[original_fingerprint.exchange]
    except KeyError:
        raise KISUsageError(
            f"해외 정정·취소를 지원하지 않는 거래소코드: {original_fingerprint.exchange!r}."
        ) from None
    if original_fingerprint.order_type != "limit":
        raise KISUsageError("해외 정정·취소는 지정가 원주문만 지원한다.")
    if action == "modify" and market in _CHANGE_CANCEL_ONLY_MARKETS:
        raise KISUsageError(
            f"{market} 시장({original_fingerprint.exchange})은 KIS 공식상 취소만 지원한다"
            " -- 정정은 불가하니 취소 후 재주문하라."
        )
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "OVRS_EXCG_CD": order_exchange,
        "PDNO": original_report.symbol,
        "ORGN_ODNO": str(original_report.order_id),
        "RVSE_CNCL_DVSN_CD": "02" if action == "cancel" else "01",
        "ORD_QTY": format_wire_decimal(quantity),
        "OVRS_ORD_UNPR": "0" if limit_price is None else format_wire_decimal(limit_price),
        "MGCO_APTM_ODNO": "",
        "ORD_SVR_DVSN_CD": "0",
    }
    return WireRequest("POST", _CHANGE_PATH, _CHANGE_TR[(market, environment)], body)


# --- 재조회(해외 주문체결내역) ---------------------------------------------
_CCNL_PATH = "/uapi/overseas-stock/v1/trading/inquire-ccnl"
_CCNL_TR = {"real": "TTTS3035R", "paper": "VTTS3035R"}
#: 해외 체결내역 매매구분코드. 매수=02, 매도=01(도메스틱과 다르다).
_SIDE_CODE = {"buy": "02", "sell": "01"}
_MAX_CCNL_PAGES = 100
#: 재조회 날짜창(일). 해외는 현지시각 기준이라 KST-오늘이 거래소 현지일과 최대 ±1일 어긋날 수
#: 있어 하루만 뒤로 본다. 더 넓히면 과거의 동일지문 주문이 가짜 단일매칭될 위험이 커진다.
_LOOKBACK_DAYS = 1


def reconcile(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment, now: datetime | None = None,
    force_refresh: bool = False,
) -> ExecutionReport | None:
    """미확인 해외 주문의 실제 상태를 체결내역에서 재조회한다 -- **보수적**(도메스틱과 동형).

    완료 리포트가 있으면 반환. in-flight 면 체결내역을 지문으로 스캔해 정확히 1건이면 확정(단 그 행에
    쓸 수 있는 ODNO 가 없으면 비종료는 ``None`` 유지 -- order_id=None 리포트의 refresh 교착 방지; 종료
    체결은 ODNO 없어도 확정), 0건이면
    ``None``(재전송 금지 유지), 2건 이상이면 :class:`KISError`. **자동 해제는 절대 하지 않는다.**
    ODNO 로는 서버 **검색**이 안 돼(KIS 명세) 기본은 지문(종목/매매/수량/단가)으로 맞춘다. 단
    ``force_refresh`` 재조회로 prior 에 확정 ODNO 가 있으면, 반환된 행의 ``odno`` 로 **로컬 필터링**해
    그 ODNO(정정/취소 행 제외)로 자기 행을 집어 형제 오귀속을 막는다 -- 지문은 ODNO 없는 타임아웃
    복구 전용. ``now`` 는 조회 날짜창의 기준시각(주입하면 결정적; 생략 시 현재 KST).

    ``force_refresh`` 면 저장된 완료 리포트가 있어도 단락하지 않고 다시 스캔한다(ChangeAction
    재조회가 원주문의 현재 상태를 캐시가 아니라 브로커에서 확인하려고 쓴다)."""
    prior, fingerprint = store.snapshot_for(client_order_id)  # 리포트+지문 원자 스냅샷(세대 일관)
    if prior is not None and not force_refresh:
        return prior
    if fingerprint is None:
        raise KISUsageError(
            f"모르는 client_order_id: {client_order_id!r} (이 계좌로 전송한 적이 없다)."
        )
    if not isinstance(fingerprint, ImmediateOrderFingerprint):  # 즉시주문(정규/오버나이트) reconcile 경로
        raise KISError(
            f"client_order_id {client_order_id!r} 의 지문이 즉시주문이 아니다"
            f"({type(fingerprint).__name__}, 내부 상태 불일치)."
        )
    if fingerprint.session == "overnight":
        # 미국 오버나이트 거래 체결은 정규 체결내역(inquire-ccnl)에 담기지 않으므로, 여기서 단일 매칭되는 행은
        # 반드시 '정규 세션' 주문이다 -> 주간 주문을 그 행으로 확정하면 오확정이다. 자동 확정하지 않고
        # in-flight 를 유지한다(오버나이트 거래 전용 체결조회 미구현 -- 수동 확인 필요). 단 prior 가
        # 종료상태면 재스캔으로 바뀔 수 없으니 그 확정 리포트를 돌려준다 -- refresh 가 완료된 오버나이트
        # 주문을 '미해소'로 퇴행시켜 이미 체결된 주문을 재전송하게 두지 않는다(주간 경로와 대칭).
        if prior is not None and prior.status in TERMINAL_ORDER_STATUSES:
            return prior
        return None
    try:
        rows = _fetch_ccnl(
            transport, symbol=fingerprint.symbol, exchange=fingerprint.exchange,
            cano=cano, product_code=product_code, environment=environment, now=now,
        )
    except TransportTimeoutError as err:
        raise OrderTimeoutError(
            f"해외 재조회(체결내역) 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
            f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
            client_order_id=client_order_id,
        ) from err
    want_odno = normalize_order_number(prior.order_id) if prior is not None and prior.order_id else ""
    if want_odno:
        # 해외 ccnl 은 ODNO 로 **서버 조회**는 안 되지만 반환된 행에는 ``odno`` 가 실려, prior 의 확정
        # ODNO 로 **로컬 필터링**은 된다. force_refresh 재조회(ChangeAction 이 원주문 현재상태 확인에 씀)는
        # 그 ODNO 로 자기 행을 집어, 동일 종목·수량·가격의 형제 행을 이 주문으로 오귀속하지 않게 한다
        # (형제 ODNO 가 store.record 돼 이후 cancel 이 살아있는 형제를 겨냥하는 phantom 확정 방지). ODNO 로
        # 집더라도 정정/취소 행(rvse_cncl_dvsn 01/02)은 제외해 원주문 행만 매칭한다(같은 odno 로 돌아오는
        # 변경 행 오확정 방지). 저장 ODNO 가 전부-0/빈값으로 정규화돼 비면 아래 elif/else: prior 없으면
        # 지문(타임아웃 복구), prior 있으면 0건 처리(지문 폴백 금지 -- 국내 reconcile 과 대칭).
        matches = [row for row in rows
                   if normalize_order_number(str(row.get("odno", ""))) == want_odno
                   and str(row.get("rvse_cncl_dvsn", "")).strip() not in ("01", "02")]
    elif prior is None:
        matches = _filter_matching_ccnl_rows(rows, fingerprint)  # prior 없는 타임아웃 복구 전용
    else:
        # prior 는 있는데 쓸 수 있는 ODNO 가 없다 -- 지문 폴백 형제 오확정 방지, 0건 -> 종료-prior/None.
        matches = []
    if len(matches) > 1:
        raise KISError(
            f"주문 {client_order_id} 의 지문과 일치하는 체결내역이 {len(matches)}건이라 자동 확정 "
            f"불가하다(KIS가 client_order_id를 돌려주지 않음). 수동 확인이 필요하다."
        )
    if not matches:  # 0건: 미접수인지 반영 지연인지 당일창 밖인지 단정 불가.
        # prior 가 종료상태면 재스캔으로
        # 바뀔 수 없으니 그 확정 리포트를 돌려준다 -- CLI/MCP 상시 refresh 가 당일창 밖 완료 주문을
        # '미해소'로 퇴행시키지 않게(국내 reconcile 과 대칭). 비종료 prior 는 미확정 유지(None).
        if prior is not None and prior.status in TERMINAL_ORDER_STATUSES:
            return prior
        return None
    report = _ccnl_report(client_order_id, fingerprint, matches[0])
    if not report.is_terminal and normalize_order_number(str(matches[0].get("odno", ""))) == "":
        return None  # 비종료 + ODNO 없음 -> order_id=None 리포트는 refresh 교착 유발; 종료는 ODNO 없어도 확정
    if not store.record_if_unchanged(report, fingerprint, expected=(prior, fingerprint)):
        return store.report_for(client_order_id)  # 읽기~기록 사이 동시 재바인딩 -> 옛 세대로 덮어쓰지 않음
    return report


def _fetch_ccnl(
    transport: Transport, *, symbol: str, exchange: str,
    cano: str, product_code: str, environment: Environment, now: datetime | None = None,
) -> list[Mapping[str, Any]]:
    """해외 체결내역을 연속조회 소진까지 읽어 행을 돌려준다(순수 I/O). 에러 응답은 fail-closed.
    ``now`` 주입 시 날짜창이 결정적(테스트용); 생략 시 현재 KST."""
    order_exchange = _ORDER_EXCHANGE.get(exchange, (exchange, ""))[0]
    stamp = datetime.now(_KST) if now is None else now
    start = f"{stamp - timedelta(days=_LOOKBACK_DAYS):%Y%m%d}"
    end = f"{stamp:%Y%m%d}"
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_CCNL_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "PDNO": symbol, "ORD_STRT_DT": start, "ORD_END_DT": end,
            "SLL_BUY_DVSN": "00", "CCLD_NCCS_DVSN": "00", "OVRS_EXCG_CD": order_exchange,
            "SORT_SQN": "DS", "ORD_DT": "", "ORD_GNO_BRNO": "", "ODNO": "",
            "CTX_AREA_NK200": ctx_nk, "CTX_AREA_FK200": ctx_fk,
        }
        resp = transport.request(
            method="GET", path=_CCNL_PATH, tr_id=_CCNL_TR[environment],
            params=params, idempotent=True, tr_cont=tr_cont,  # 읽기 -- 타임아웃 재시도 안전
        )
        if not resp.ok:
            raise KISError(
                f"해외 재조회(체결내역) 실패: {resp.msg1}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        page = resp.body.get("output")
        if isinstance(page, (list, tuple)):
            for row in page:
                if not isinstance(row, Mapping):  # 성공 응답인데 행이 매핑 아님 -> fail-closed
                    raise KISError(
                        f"해외 재조회(체결내역) output 에 매핑이 아닌 행: {row!r}",
                        rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
                    )
            rows.extend(page)
        elif page not in (None, ""):  # 미체결(없음)은 None/"" 로 정상 -- 그 외 형식은 조용히 0건 처리 금지
            raise KISError(
                f"해외 재조회(체결내역) output 형식 오류(list 가 아님): {page!r}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        prev_nk = ctx_nk
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        # 커서가 진전 불가(빈 커서/반복 커서)인데 응답이 '더 있음'(tr_cont=F/M)이면 확정 불가 --
        # 부분 스캔으로 확정하면 미접수 오독->수동 재주문(이중체결), 또는 빈 커서가 CTX 를 리셋해
        # 1페이지 재적재->이중집계(지문 2건 오진단). fail-closed 로 올려 재시도/수동확인을 유도한다.
        if resp.tr_cont in ("F", "M") and (not ctx_nk or ctx_nk == prev_nk):
            raise KISError(
                "재조회 커서가 진전하지 않는데 응답이 '더 있음'(tr_cont=F/M) -- "
                "부분/모순 응답으로 확정하지 않는다(재시도/수동확인 요망).",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        # 여기 도달 = 모순 아님(빈/반복 커서 + F/M 은 위에서 걸렀다). 종료 센티널 ^^ 는 tr_cont=F/M 이어도
        # KIS 의 정상 '더 없음' 신호(_response.py 계약)라 raise 대상이 아니다 -- 반복/^^ 커서면 더 없음 확정.
        if ctx_nk == _CONTINUATION_END or (ctx_nk and ctx_nk == prev_nk):
            break
        # 재조회는 조기 종료 금지(체결 누락->오재주문 위험): tr_cont 정본 종료(D/E/공백)이면서
        # 연속조회 커서도 소진됐을 때만 마지막 페이지로 확정(둘 중 하나라도 남으면 계속 스캔).
        if resp.tr_cont not in ("F", "M") and not ctx_nk:
            break
        tr_cont = "N"
    else:
        raise KISError(
            f"해외 재조회 스캔이 {_MAX_CCNL_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 스캔으로 확정하지 않는다. 재시도하거나 수동 확인하라."
        )
    return rows


def _filter_matching_ccnl_rows(
    rows: list[Mapping[str, Any]], fingerprint: ImmediateOrderFingerprint
) -> list[Mapping[str, Any]]:
    """체결내역 행 중 요청 지문과 맞는 것만(순수). 종목+매매구분+주문수량, 지정가는 주문단가까지 비교."""
    symbol, side = fingerprint.symbol, fingerprint.side
    quantity = Decimal(fingerprint.quantity)
    limit_price = Decimal(fingerprint.limit_price) if fingerprint.limit_price else None
    want_side = _SIDE_CODE[side]
    matched = []
    for row in rows:
        # 정정(01)/취소(02) 행은 원주문(orgn_odno)을 참조하는 별개 행이라 원주문 지문 매칭에서 제외
        # -- 원주문 행만 맞춰 가짜 다중매칭을 줄인다.
        if str(row.get("rvse_cncl_dvsn", "")).strip() in ("01", "02"):
            continue
        if str(row.get("pdno", "")) != symbol:
            continue
        if str(row.get("sll_buy_dvsn_cd", "")) != want_side:
            continue
        if _parse_decimal(row.get("ft_ord_qty")) != quantity:
            continue
        if limit_price is not None:  # 지정가는 주문단가가 있고 같아야 한다(없으면 제외, 안전 방향)
            row_price = row.get("ft_ord_unpr3")
            if row_price in (None, "") or _parse_decimal(row_price) != limit_price:
                continue
        matched.append(row)
    return matched


def _ccnl_report(
    client_order_id: str, fingerprint: ImmediateOrderFingerprint, row: Mapping[str, Any]
) -> ExecutionReport:
    ordered = _parse_decimal(row.get("ft_ord_qty"))
    filled = _parse_decimal(row.get("ft_ccld_qty"))
    rejected = str(row.get("rjct_rson", "")).strip() or str(row.get("prcs_stat_name", "")) == "거부"
    # 이 엔드포인트의 처리상태(prcs_stat_name)는 완료/거부/전송뿐이라 CANCELED 값이 없다(KIS 명세). 취소는
    # 별개 행이라 위 필터에서 제외되므로, 취소된 원주문은 마지막 working 상태(전송->NEW)로 읽힌다.
    if rejected and filled == 0:
        status = OrderStatus.REJECTED
    elif ordered > 0 and filled >= ordered:
        status = OrderStatus.FILLED
    elif filled > 0:
        status = OrderStatus.PARTIALLY_FILLED
    else:
        status = OrderStatus.NEW
    avg = _parse_decimal(row.get("ft_ccld_unpr3"))
    return ExecutionReport(
        client_order_id=client_order_id,
        order_id=str(row.get("odno")) if row.get("odno") else None,
        symbol=fingerprint.symbol,
        side=fingerprint.side,
        status=status,
        filled_quantity=filled,
        average_price=avg if filled > 0 and avg > 0 else None,
        recorded_at=datetime.now(_KST),
        _raw=row,
    )


def _parse_decimal(value: object) -> Decimal:
    """KIS 문자열 수치 -> Decimal. 공백/None 은 0, 값이 있는데 파싱 실패면 fail-closed(:class:`KISError`).

    ``"nan"``/``"inf"`` 는 파싱되지만 비유한값이라 이후 수량·단가 비교가 무너져 오확정을 부른다 --
    :meth:`Decimal.is_finite` 로 fail-closed 한다."""
    if value is None or value == "":
        return Decimal(0)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as err:
        raise KISError(f"해외 재조회 응답의 수치 파싱 실패: {value!r}") from err
    if not number.is_finite():
        raise KISError(f"해외 재조회 응답의 수치가 유한하지 않다(NaN/Infinity): {value!r}")
    return number


# --- 주문 조회 (미체결/알고 주문·체결) -- account 잔고조회와 분리 ---
_OPEN_ORDERS_PATH = "/uapi/overseas-stock/v1/trading/inquire-nccs"
_OPEN_ORDERS_TR = "TTTS3018R"           # 모의투자 미지원(실전만)

_ALGO_ORDNO_PATH = "/uapi/overseas-stock/v1/trading/algo-ordno"
_ALGO_ORDNO_TR = "TTTS6058R"            # 모의투자 미지원
_ALGO_CCNL_PATH = "/uapi/overseas-stock/v1/trading/inquire-algo-ccnl"
_ALGO_CCNL_TR = "TTTS6059R"             # 모의투자 미지원


def fetch_open_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    market: str | None = None,
) -> list[OverseasOpenOrder]:
    """해외 미체결 주문 전체(연속조회 소진까지). ``market`` 생략(``None``)이면 전체 시장 그룹을 순회해
    합친다. **모의투자 미지원**(demo면 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError("해외 미체결내역 조회는 모의투자 미지원이다(실전 계좌만).")
    if market is None:
        out: list[OverseasOpenOrder] = []
        for group in _MARKETS:
            out.extend(fetch_open_orders(
                transport, cano=cano, product_code=product_code, environment=environment,
                market=group,
            ))
        return out
    try:
        exchange, currency = _MARKETS[market]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 해외 시장: {market!r} ({'/'.join(_MARKETS)})."
        ) from None
    rows = _fetch_paginated_rows(
        transport,
        path=_OPEN_ORDERS_PATH, tr_id=_OPEN_ORDERS_TR,
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code, "OVRS_EXCG_CD": exchange,
            "SORT_SQN": "DS", "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        },
        output_key="output", max_pages=_MAX_PAGES, ctx_width=200,
        cap_message=(
            f"해외 미체결 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        ),
    )
    return _parse_open_orders(rows, default_currency=currency)

def _parse_open_orders(
    rows: list[Mapping[str, Any]], *, default_currency: str
) -> list[OverseasOpenOrder]:
    orders: list[OverseasOpenOrder] = []
    for row in rows:
        order_id = str(row.get("odno", "")).strip()
        if not order_id:  # 빈 행 skip
            continue
        currency = str(row.get("tr_crcy_cd", "")).strip() or default_currency
        orders.append(
            OverseasOpenOrder(
                symbol=str(row.get("pdno", "")).strip(),
                name=str(row.get("prdt_name", "")).strip(),
                exchange=str(row.get("ovrs_excg_cd", "")).strip(),
                order_id=order_id,
                side=_side_from_code(row.get("sll_buy_dvsn_cd")),
                order_quantity=_decimal_or_zero(row, "ft_ord_qty"),
                filled_quantity=_decimal_or_zero(row, "ft_ccld_qty"),
                unfilled_quantity=_decimal_or_zero(row, "nccs_qty"),
                order_price=_money(row, "ft_ord_unpr3", currency),
                _raw=row,
            )
        )
    return orders

def fetch_algo_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    trade_date: str = "",
) -> list[OverseasAlgoOrder]:
    """해외 지정가(TWAP/VWAP 등 알고) 주문 목록. 각 건의 ``order_id``/``branch_number`` 로 체결내역을
    조회한다(:func:`fetch_algo_executions`). ``trade_date``(YYYYMMDD)는 거래일자로, 알고주문은
    당일 집행이라 생략하면 오늘(KST)을 쓴다. 응답 키가 대문자다(algo 패밀리 TTTS6058R/6059R 규약).
    **모의투자 미지원**."""
    if environment == "paper":
        raise KISUsageError("해외 지정가주문번호조회(algo-ordno)는 모의투자 미지원 -- 실전에서만.")
    if trade_date and not (len(trade_date) == 8 and trade_date.isdigit()):
        raise KISUsageError(f"trade_date 는 YYYYMMDD 형식이어야 한다: {trade_date!r}")
    trad_dt = trade_date or datetime.now(_KST).strftime("%Y%m%d")
    rows = _fetch_paginated_rows(
        transport,
        path=_ALGO_ORDNO_PATH, tr_id=_ALGO_ORDNO_TR,
        base_params={"CANO": cano, "ACNT_PRDT_CD": product_code, "TRAD_DT": trad_dt,
                     "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""},
        output_key="output", max_pages=_MAX_PAGES, ctx_width=200,
        cap_message="해외 지정가주문번호조회가 페이지 상한에 도달했으나 연속조회가 남아있다.",
    )
    return [
        OverseasAlgoOrder(
            order_id=str(row.get("ODNO", "")).strip(),
            trade_type_name=str(row.get("TRAD_DVSN_NAME", "")).strip(),
            symbol=str(row.get("PDNO", "")).strip(),
            name=str(row.get("ITEM_NAME", "")).strip(),
            order_quantity=_decimal_or_zero(row, "FT_ORD_QTY"),
            order_price=_decimal_or_zero(row, "FT_ORD_UNPR3"),
            filled_quantity=_decimal_or_zero(row, "FT_CCLD_QTY"),
            split_attribute=str(row.get("SPLT_BUY_ATTR_NAME", "")).strip(),
            branch_number=str(row.get("ORD_GNO_BRNO", "")).strip(),
            _raw=row,
        )
        for row in rows if str(row.get("ODNO", "")).strip()
    ]

def _algo_currency(row: Mapping[str, Any]) -> str:
    """알고 체결행의 거래통화(TR_CRCY). algo(TWAP/VWAP)는 미국 전용이라 비면 USD 로 본다."""
    return str(row.get("TR_CRCY", "")).strip() or "USD"


def fetch_algo_executions(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    order_date: str, order_id: str, branch_number: str = "",
) -> list[OverseasAlgoExecution]:
    """한 해외 알고주문(``order_id``)의 체결내역. ``order_date``(YYYYMMDD)는 주문일자, ``branch_number``
    는 주문채번지점번호(:func:`fetch_algo_orders` 의 ``branch_number``). 응답 키가 대문자다. **모의투자 미지원**."""
    if environment == "paper":
        raise KISUsageError("해외 지정가체결내역조회(inquire-algo-ccnl)는 모의투자 미지원 -- 실전에서만.")
    rows = _fetch_paginated_rows(
        transport,
        path=_ALGO_CCNL_PATH, tr_id=_ALGO_CCNL_TR,
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "ORD_DT": order_date, "ORD_GNO_BRNO": branch_number, "ODNO": order_id,
            "TTLZ_ICLD_YN": "", "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        },
        output_key="output", max_pages=_MAX_PAGES, ctx_width=200,
        cap_message="해외 지정가체결내역조회가 페이지 상한에 도달했으나 연속조회가 남아있다.",
    )
    return [
        OverseasAlgoExecution(
            sequence=str(row.get("CCLD_SEQ", "")).strip(),
            executed_at=_parse_hhmmss(row.get("CCLD_BTWN")),
            symbol=str(row.get("PDNO", "")).strip(),
            name=str(row.get("ITEM_NAME", "")).strip(),
            filled_quantity=_decimal_or_zero(row, "FT_CCLD_QTY"),
            price=_money(row, "FT_CCLD_UNPR3", _algo_currency(row)),
            amount=_money(row, "FT_CCLD_AMT3", _algo_currency(row)),
            _raw=row,
        )
        for row in rows if str(row.get("CCLD_SEQ", "")).strip()
    ]


# --- 체결기준현재잔고 (CTRP6504R) -----------------------------------------

