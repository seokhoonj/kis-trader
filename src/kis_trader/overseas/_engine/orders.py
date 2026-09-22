"""해외주식 주문 요청 조립 (내부) -- (거래소, 매수/매도, 실전/모의) -> TR + 바디.

주문 실행의 안전 규칙(이중체결 방지·쓰기 재시도 금지·보수적 재조회)은 국내와 공유하는 안전
코어(:class:`~kis_trader.store.OrderStore`)가 맡는다. 이 모듈은 그 코어에 넘길 **해외 주문의
와이어 요청**만 조립한다 -- 순수 함수라 오케스트레이션 없이 단독 검증된다.

KIS URL/TR-ID (KIS 명세 대조, sheet '해외주식 주문'):
- 주문: ``POST /uapi/overseas-stock/v1/trading/order``. TR 은 시장 x 매수/매도 x 실전/모의로 갈린다
  (아래 :data:`_ORDER_TR`). 시세 조회의 거래소코드(NAS/NYS/...)와 주문 거래소코드(NASD/NYSE/...)가
  다르므로 :data:`_ORDER_EXCHANGE` 로 매핑한다. 해외 주문은 지정가(ORD_DVSN=00) 중심이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..._internal._datetime import _KST
from ..._internal._response import _CONTINUATION_END, _fetch_paginated_rows
from ..._internal._wire import format_wire_decimal, required_int
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
from ...report import ExecutionReport, OrderStatus
from ...store import OrderStore
from ...transport import Environment, Transport, TransportTimeout
from ..entities.orders import OverseasAlgoExecution, OverseasAlgoOrder, OverseasOpenOrder
from ._parse import _MARKETS, _MAX_PAGES, _decimal_or_zero, _money, _side_from_code

_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"
_CHANGE_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
_CHANGE_TR = {"real": "TTTT1004U", "paper": "VTTT1004U"}

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


def make_order_request(
    order: Order, *, cano: str, product_code: str, environment: Environment
) -> WireRequest:
    """안전 코어(:func:`~kis_trader.domestic._engine.orders.place`)에 넘길 해외 주문 빌더.

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
    """해외주식 정정·취소 요청을 공식 단일 TR 와이어로 조립한다."""
    # 지정가 전용 자산이라 가격형상 규칙을 공유 헬퍼로 강제한다(정정=>가격>0, 취소=>가격 없음).
    reject_bad_change_price_shape(action, limit_price)
    try:
        order_exchange = _ORDER_EXCHANGE[original_fingerprint.exchange][0]
    except KeyError:
        raise KISUsageError(
            f"해외 정정·취소를 지원하지 않는 거래소코드: {original_fingerprint.exchange!r}."
        ) from None
    if original_fingerprint.order_type != "limit":
        raise KISUsageError("해외 정정·취소는 지정가 원주문만 지원한다.")
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
    return WireRequest("POST", _CHANGE_PATH, _CHANGE_TR[environment], body)


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
) -> ExecutionReport | None:
    """미확인 해외 주문의 실제 상태를 체결내역에서 재조회한다 -- **보수적**(도메스틱과 동형).

    완료 리포트가 있으면 반환. in-flight 면 체결내역을 지문으로 스캔해 정확히 1건이면 확정, 0건이면
    ``None``(재전송 금지 유지), 2건 이상이면 :class:`KISError`. **자동 해제는 절대 하지 않는다.**
    ODNO 로는 검색이 안 돼(KIS 명세) 지문(종목/매매/수량/단가)으로 맞춘다. ``now`` 는 조회 날짜창의
    기준시각(주입하면 결정적; 생략 시 현재 KST)."""
    prior = store.report_for(client_order_id)
    if prior is not None:
        return prior
    fingerprint = store.fingerprint_for(client_order_id)
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
        # in-flight 를 유지한다(오버나이트 거래 전용 체결조회 미구현 -- 수동 확인 필요).
        return None
    try:
        rows = _fetch_ccnl(
            transport, symbol=fingerprint.symbol, exchange=fingerprint.exchange,
            cano=cano, product_code=product_code, environment=environment, now=now,
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"해외 재조회(체결내역) 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
            f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
            client_order_id=client_order_id,
        ) from err
    matches = _filter_matching_ccnl_rows(rows, fingerprint)
    if len(matches) > 1:
        raise KISError(
            f"주문 {client_order_id} 의 지문과 일치하는 체결내역이 {len(matches)}건이라 자동 확정 "
            f"불가하다(KIS가 client_order_id를 돌려주지 않음). 수동 확인이 필요하다."
        )
    if not matches:  # 0건: 미접수인지 반영 지연인지 단정 불가 -> in-flight 유지
        return None
    report = _ccnl_report(client_order_id, fingerprint, matches[0])
    store.record(report, fingerprint)
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
        rows.extend(page if isinstance(page, list) else [])
        prev_nk = ctx_nk
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        # 비진전 커서(반복/종료 센티널)면 tr_cont 정본종료 여부와 무관하게 멈춘다 -- 반복=서버가
        # 커서를 안 진전시킴=더 없음이라 fill 누락 없이 안전하고, 같은 페이지 무한 재요청/이중집계를 막는다.
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
                quantity=required_int(row.get("ft_ord_qty"), "ft_ord_qty"),
                filled_quantity=required_int(row.get("ft_ccld_qty"), "ft_ccld_qty"),
                unfilled_quantity=required_int(row.get("nccs_qty"), "nccs_qty"),
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
            trade_type=str(row.get("TRAD_DVSN_NAME", "")).strip(),
            symbol=str(row.get("PDNO", "")).strip(),
            name=str(row.get("ITEM_NAME", "")).strip(),
            quantity=_decimal_or_zero(row, "FT_ORD_QTY"),
            order_price=_decimal_or_zero(row, "FT_ORD_UNPR3"),
            filled_quantity=_decimal_or_zero(row, "FT_CCLD_QTY"),
            split_attribute=str(row.get("SPLT_BUY_ATTR_NAME", "")).strip(),
            branch_number=str(row.get("ORD_GNO_BRNO", "")).strip(),
            _raw=row,
        )
        for row in rows if str(row.get("ODNO", "")).strip()
    ]

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
            quantity=_decimal_or_zero(row, "FT_CCLD_QTY"),
            price=_decimal_or_zero(row, "FT_CCLD_UNPR3"),
            executed_amount=_decimal_or_zero(row, "FT_CCLD_AMT3"),
            _raw=row,
        )
        for row in rows if str(row.get("CCLD_SEQ", "")).strip()
    ]


# --- 체결기준현재잔고 (CTRP6504R) -----------------------------------------

def _parse_hhmmss(value: object) -> time | None:
    text = str(value or "").strip()
    if len(text) != 6 or not text.isdigit():
        return None
    hour, minute, second = int(text[0:2]), int(text[2:4]), int(text[4:6])
    if hour > 23 or minute > 59 or second > 59:
        return None
    return time(hour, minute, second)
