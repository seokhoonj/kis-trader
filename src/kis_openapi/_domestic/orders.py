"""국내주식 주문 실행 엔진 (내부) -- 안전 규칙 구현.

사용자면(종목 핸들 buy/sell, kis.orders.reconcile)이 이 함수들을 호출한다. 안전 불변식(이중체결
구조적 불가·쓰기 재시도 금지·보수적 재조회)은 여기와 :class:`~kis_openapi.store.OrderStore`
가 함께 보장한다. KIS 주문/체결조회 와이어 매핑은 이 안에 갇힌다.

:func:`place` 흐름:
1. 계좌 주문가능 여부와 와이어 변환을 **먼저** 확인(불가/미구현이면 claim 전에 중단).
2. :meth:`OrderStore.try_claim` 로 원자적 판정/확보: 완료면 리포트 replay, 같은 id 다른 지문은
   충돌 거부, in-flight 면 재조회 요구, 새로 확보되면 전송.
3. 전송. 타임아웃이면 재시도 없이 in-flight 유지 후 :class:`OrderTimeoutError`.
4. 접수 거부(rt_cd!=0)면 in-flight 해제 후 :class:`OrderRejectedError`(체결 아님).
5. 접수됐으나 ODNO 없으면 재조회 불가 -> in-flight 유지하고 raise.
6. 성공이면 리포트(NEW)를 기록하고 반환.

:func:`reconcile` 는 미확인 주문을 일별체결조회로 재조회한다 -- **보수적**: 스캔이 비거나
모호하면 미접수로 단정하지 않고 in-flight 유지(부재는 미접수의 증거가 아니다).

KIS URL/TR-id (국내주식):
- 현금주문: ``POST .../trading/order-cash`` 실전 매수 ``TTTC0012U`` / 매도 ``TTTC0011U``,
  모의 매수 ``VTTC0012U`` / 매도 ``VTTC0011U``.
- 일별체결조회(재조회): ``GET .../trading/inquire-daily-ccld`` 실전 ``TTTC0081R`` / 모의 ``VTTC0081R``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, TypeAlias

from ..errors import (
    AccountNotOrderableError,
    KISError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
)
from ..instrument import resolve_market
from ..order import Fingerprint, Order, WireRequest, format_wire_decimal
from ..report import ExecutionReport, OrderStatus
from ..risk import RiskLimits
from ..store import ClaimOutcome, OrderStore
from ..transport import Environment, Transport, TransportTimeout
from . import market_data

_KST = timezone(timedelta(hours=9))

#: 재조회 시 일별체결조회 연속조회(페이지) 상한 -- 무한 루프 방지의 명시적 안전 상한.
_MAX_RECONCILE_PAGES = 100

#: 국내(KRX/KOSDAQ/Nextrade) 시장 식별코드 -- 이 셋은 국내 현금주문으로 라우팅.
_DOMESTIC_MICS = frozenset(("XKRX", "XKOS", "NXTE"))

#: 주문 와이어 요청 조립기: (order, cano, product_code, environment) -> WireRequest.
#: 안전 코어(place)는 시장 중립이고, 도메스틱/해외가 각자 이 형태의 빌더를 준다.
BuildRequest: TypeAlias = Callable[[Order, str, str, Environment], WireRequest]

_ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_ORDER_CREDIT_PATH = "/uapi/domestic-stock/v1/trading/order-credit"
# 신용주문 (모의 미지원): 매도 TTTC0051U / 매수 TTTC0052U.
_ORDER_CREDIT_TR = {"sell": "TTTC0051U", "buy": "TTTC0052U"}
_DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
# (environment, side) -> tr_id
_ORDER_CASH_TR = {
    ("real", "buy"): "TTTC0012U", ("real", "sell"): "TTTC0011U",
    ("demo", "buy"): "VTTC0012U", ("demo", "sell"): "VTTC0011U",
}
_DAILY_CCLD_TR = {"real": "TTTC0081R", "demo": "VTTC0081R"}
_CHANGE_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
_CHANGE_TR = {"real": "TTTC0013U", "demo": "VTTC0013U"}
_ACTION_EXCHANGE_PREFIX = "action:"
# order_type -> KIS ORD_DVSN(주문구분): 00 지정가, 01 시장가
_ORD_DVSN = {"limit": "00", "market": "01"}
# our side -> KIS SLL_BUY_DVSN_CD (01 매도, 02 매수)
_SIDE_CODE = {"buy": "02", "sell": "01"}
_EXCHANGE_ID = {"XKRX": "KRX", "XKOS": "KRX", "NXTE": "NXT"}


def place(
    transport: Transport, store: OrderStore, order: Order, *,
    cano: str, product_code: str, environment: Environment, orderable: bool = True,
    risk: RiskLimits | None = None, build_request: BuildRequest | None = None,
) -> ExecutionReport:
    """주문을 안전 규칙(모듈 docstring 6단계)에 따라 전송한다.

    ``risk`` 를 주면 와이어 전에 사전 리스크 한도를 점검한다. ``build_request`` 는 와이어 요청을
    조립하는 시장별 빌더(기본은 국내 현금주문) -- 이중체결 방지·재시도 금지·재조회 등 안전 코어는
    시장과 무관하게 공유한다. 계좌 가드/수량 정수/리스크 게이트는 모두 :meth:`OrderStore.try_claim`
    **전**에 돈다 -- 거부되면 ``client_order_id`` 를 소비하지도 와이어에 닿지도 않는다.
    """
    build = build_request if build_request is not None else _make_order_cash_request
    client_order_id = order.client_order_id
    fingerprint = order.fingerprint

    if not orderable:
        raise AccountNotOrderableError(
            "이 계좌는 API 주문이 불가하다(퇴직연금 IRP/DC 등 조회전용). 일반/연금저축 계좌를 쓰라."
        )
    # 주식 주문은 주(株) 단위 정수 수량만 -- 소수 수량은 fat-finger(예: 10.5). 와이어 전에 막는다.
    if order.quantity != order.quantity.to_integral_value():
        raise KISUsageError(f"주식 주문 수량은 정수여야 한다(주 단위): {order.quantity}")
    # 사전 리스크 한도(opt-in). 참조가가 필요하면 현재가를 조회한다 -- 조회 실패는 fail-closed
    # (한도 확인 불가 -> 주문 중단; 예외가 그대로 올라가 claim 전에 멈춘다).
    if risk is not None:
        _run_pre_trade_risk(transport, order, risk)
    # 와이어 변환을 먼저 -- 미구현/부적합이면 claim 전에 중단(stuck in-flight 방지).
    method, path, tr_id, body = build(order, cano, product_code, environment)

    outcome, prior = store.try_claim(client_order_id, fingerprint)
    if outcome is ClaimOutcome.COMPLETED:
        if prior is None:  # store 불변식 위반 -- assert 가 아니라(python -O 안전)
            raise OrderError("claim 이 COMPLETED 인데 리포트가 없다(store 불변식 위반).")
        return prior
    if outcome is ClaimOutcome.CONFLICT:
        raise KISUsageError(
            f"client_order_id {client_order_id!r} 는 이미 다른 주문에 사용됐다. 새 id를 발행하라."
        )
    if outcome is ClaimOutcome.IN_FLIGHT:
        raise KISUsageError(
            f"주문 {client_order_id} 은 전송됐으나 결과가 확인되지 않았다. "
            f"kis.orders.reconcile({client_order_id!r}) 로 재조회한 뒤 판단하라."
        )
    if outcome is not ClaimOutcome.CLAIMED:  # 전송은 좁게 가드 -- CLAIMED 만 와이어에 닿는다
        raise OrderError(f"예상치 못한 claim outcome: {outcome!r}")

    try:
        resp = transport.request(
            method=method, path=path, tr_id=tr_id, body=body, idempotent=False
        )
    except TransportTimeout as err:
        # 재시도 금지: 체결 여부 불명. in-flight 유지, 재조회를 요구한다.
        raise OrderTimeoutError(
            f"주문 전송 시간초과 -- 체결 여부가 불명이다. 재전송하지 말고 "
            f"kis.orders.reconcile({client_order_id!r}) 로 재조회하라.",
            client_order_id=client_order_id,
        ) from err

    if not resp.ok:
        # 접수 거부 -- 주문이 들어가지 않았다. in-flight 해제, 명확히 raise.
        store.clear_in_flight(client_order_id)
        raise OrderRejectedError(
            f"주문 접수 거부: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )

    output = _extract_output_mapping(resp.body)
    order_id = output.get("ODNO")
    if not order_id:
        # 접수(rt_cd=0)인데 거래소 주문번호 없음 -> 재조회 불가. in-flight 유지하고 raise.
        raise OrderError(
            "접수 응답(rt_cd=0)에 거래소 주문번호(ODNO)가 없다 -- 재조회 불가.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )

    report = ExecutionReport(
        client_order_id=client_order_id,
        order_id=str(order_id),
        symbol=order.symbol,
        side=order.side,
        status=OrderStatus.NEW,          # 접수됨 -- 체결은 이후 리포트/재조회로 확인
        filled_quantity=Decimal(0),
        average_price=None,
        submitted_at=datetime.now(_KST),
        _raw=resp.body,
    )
    store.record(report, fingerprint)
    return report


def reconcile(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment,
) -> ExecutionReport | None:
    """미확인 주문의 실제 상태를 브로커에 재조회한다 -- **보수적**(모듈 docstring 참고).

    완료 리포트가 있으면 반환. in-flight 면 일별체결조회를 스캔해 지문과 맞는 주문을 찾는다:
    정확히 1건이면 확정, 0건이면 미접수로 단정하지 않고 ``None``(재전송 금지 유지), 2건 이상이면
    :class:`KISError`(수동 확인). 스캔 실패(에러/타임아웃)는 in-flight 유지한 채 예외. 모르는 id면
    :class:`KISUsageError`. **자동 해제는 절대 하지 않는다.**
    """
    prior = store.report_for(client_order_id)
    if prior is not None:
        return prior
    fingerprint = store.fingerprint_for(client_order_id)
    if fingerprint is None:  # 완료도 in-flight 도 아님 -> 모르는 id
        raise KISUsageError(
            f"모르는 client_order_id: {client_order_id!r} (이 계좌로 전송한 적이 없다)."
        )
    try:
        rows = _fetch_daily_orders(transport, fingerprint.symbol, cano=cano,
                                   product_code=product_code, environment=environment)
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"재조회(일별체결조회) 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
            f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
            client_order_id=client_order_id,
        ) from err
    matches = _filter_matching_daily_rows(rows, fingerprint)
    if len(matches) > 1:
        raise KISError(
            f"주문 {client_order_id} 의 지문과 일치하는 당일 주문이 {len(matches)}건이라 "
            f"자동 확정 불가하다(KIS가 client_order_id를 돌려주지 않음). 수동 확인이 필요하다."
        )
    if not matches:  # 0건: 미접수인지 반영 지연인지 단정 불가 -> in-flight 유지, 재전송 금지
        return None
    report = _execution_report_from_daily_row(client_order_id, fingerprint, matches[0])
    store.record(report, fingerprint)
    return report


def submit_change(
    transport: Transport,
    store: OrderStore,
    *,
    original_client_order_id: str,
    request_id: str,
    action: str,
    quantity: Decimal,
    price: Decimal | None,
    cano: str,
    product_code: str,
    environment: Environment,
    build_request: Callable[..., WireRequest] | None = None,
) -> ExecutionReport:
    """접수된 주문을 정정하거나 취소한다. 변경 요청도 별도 멱등키로 중복 전송을 막는다."""
    original_report = store.report_for(original_client_order_id)
    original_fingerprint = store.fingerprint_for(original_client_order_id)
    if original_report is None or original_fingerprint is None:
        raise KISUsageError(f"확정된 원주문을 찾을 수 없다: {original_client_order_id!r}")
    if not original_report.order_id:
        raise KISUsageError("원주문에 거래소 주문번호가 없어 정정·취소할 수 없다.")
    if original_report.is_terminal:
        raise KISUsageError(
            f"종료 상태 주문은 정정·취소할 수 없다: {original_report.status.value}"
        )
    if action not in {"cancel", "modify"}:
        raise KISUsageError(f"지원하지 않는 주문 변경: {action!r}")
    if quantity <= 0 or quantity != quantity.to_integral_value():
        raise KISUsageError(f"정정·취소 수량은 양의 정수여야 한다: {quantity}")
    if action == "modify" and (price is None or price <= 0):
        raise KISUsageError("정정 주문에는 0보다 큰 price가 필요하다.")
    if action == "cancel" and price is not None:
        raise KISUsageError("취소 주문에는 price를 지정할 수 없다.")

    action_fingerprint = Fingerprint(
        symbol=f"{original_client_order_id}:{original_report.order_id}",
        side=original_fingerprint.side,
        order_type=original_fingerprint.order_type,
        quantity=format_wire_decimal(quantity),
        limit_price="" if price is None else format_wire_decimal(price),
        stop_price=action,
        time_in_force=original_fingerprint.time_in_force,
        exchange=f"{_ACTION_EXCHANGE_PREFIX}{original_fingerprint.exchange}",
    )
    builder = build_request or _make_domestic_change_request
    request = builder(
        original_report=original_report,
        original_fingerprint=original_fingerprint,
        action=action,
        quantity=quantity,
        price=price,
        cano=cano,
        product_code=product_code,
        environment=environment,
    )
    outcome, prior = store.try_claim(request_id, action_fingerprint)
    if outcome is ClaimOutcome.COMPLETED:
        if prior is None:
            raise OrderError("변경 요청 claim이 COMPLETED인데 리포트가 없다.")
        return prior
    if outcome is ClaimOutcome.CONFLICT:
        raise KISUsageError(f"request_id {request_id!r}는 이미 다른 요청에 사용됐다.")
    if outcome is ClaimOutcome.IN_FLIGHT:
        raise KISUsageError(
            f"변경 요청 {request_id}의 결과가 아직 확인되지 않았다. 재전송하지 말라."
        )
    if outcome is not ClaimOutcome.CLAIMED:
        raise OrderError(f"예상치 못한 claim outcome: {outcome!r}")
    try:
        resp = transport.request(
            method=request.method,
            path=request.path,
            tr_id=request.tr_id,
            body=request.body,
            idempotent=False,
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"주문 {action} 요청 시간초과 -- 처리 여부가 불명이다. 재전송하지 말라.",
            client_order_id=request_id,
        ) from err
    if not resp.ok:
        store.clear_in_flight(request_id)
        raise OrderRejectedError(
            f"주문 {action} 요청 거부: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    output = _extract_output_mapping(resp.body)
    report = ExecutionReport(
        client_order_id=request_id,
        order_id=str(output.get("ODNO") or original_report.order_id),
        symbol=original_report.symbol,
        side=original_report.side,
        status=OrderStatus.PENDING_CANCEL if action == "cancel" else OrderStatus.PENDING_REPLACE,
        filled_quantity=original_report.filled_quantity,
        average_price=original_report.average_price,
        submitted_at=datetime.now(_KST),
        _raw=resp.body,
    )
    store.record(report, action_fingerprint)
    return report


def _make_domestic_change_request(
    *, original_report: ExecutionReport, original_fingerprint: Fingerprint,
    action: str, quantity: Decimal, price: Decimal | None,
    cano: str, product_code: str, environment: Environment,
) -> WireRequest:
    original_output = _extract_output_mapping(original_report._raw)
    organization_number = str(
        original_output.get("KRX_FWDG_ORD_ORGNO")
        or original_output.get("ord_gno_brno")
        or ""
    ).strip()
    if not organization_number:
        raise KISUsageError(
            "원주문 리포트에 한국거래소전송주문조직번호가 없어 정정·취소할 수 없다."
        )
    order_division = _ORD_DVSN.get(original_fingerprint.order_type)
    if order_division is None:
        raise KISUsageError("원주문의 주문구분을 정정·취소 와이어로 변환할 수 없다.")
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "KRX_FWDG_ORD_ORGNO": organization_number,
        "ORGN_ODNO": str(original_report.order_id),
        "ORD_DVSN": order_division,
        "RVSE_CNCL_DVSN_CD": "02" if action == "cancel" else "01",
        "ORD_QTY": format_wire_decimal(quantity),
        "ORD_UNPR": (
            original_fingerprint.limit_price
            if price is None and original_fingerprint.limit_price
            else "0" if price is None else format_wire_decimal(price)
        ),
        "QTY_ALL_ORD_YN": "Y" if action == "cancel" else "N",
        "EXCG_ID_DVSN_CD": _EXCHANGE_ID[original_fingerprint.exchange],
    }
    return WireRequest("POST", _CHANGE_PATH, _CHANGE_TR[environment], body)


# --- 사전 리스크 한도 ------------------------------------------------------
def _run_pre_trade_risk(transport: Transport, order: Order, risk: RiskLimits) -> None:
    """리스크 한도를 점검한다. 참조가(현재가)가 필요하면 시세를 조회해 넘긴다 -- 조회 실패는
    잡지 않고 그대로 올린다(fail-closed: 한도를 확인 못 하면 주문을 보내지 않는다)."""
    reference_price = None
    if risk._needs_reference_price(order):
        quote = market_data.fetch_quote(
            transport, symbol=order.symbol, market=resolve_market(order.symbol)
        )
        reference_price = quote.last
    risk.check(order, reference_price=reference_price)


# --- 와이어 매핑(국내 현금주문) --------------------------------------------
def _make_order_cash_request(
    order: Order, cano: str, product_code: str, environment: Environment
) -> WireRequest:
    if order.exchange not in _DOMESTIC_MICS:
        raise NotImplementedError(
            f"해외주문은 아직 지원하지 않는다(exchange={order.exchange!r})."
        )
    if order.order_type not in _ORD_DVSN:
        raise NotImplementedError(
            f"{order.order_type} 주문은 아직 와이어 매핑이 없다(현재 시장가/지정가만)."
        )
    if order.time_in_force != "day":
        # 국내 현금주문 와이어엔 아직 TIF 매핑이 없다 -- 조용히 day 로 보내지 않고 fail-closed
        # (ioc/fok 를 day 로 처리하면 사용자 의도와 다른 주문이 체결된다).
        raise NotImplementedError(
            f"time_in_force={order.time_in_force!r} 는 아직 미구현이다(국내 현금주문은 현재 day 만)."
        )
    tr_id = _ORDER_CASH_TR[(environment, order.side)]
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "PDNO": order.symbol,
        "ORD_DVSN": _ORD_DVSN[order.order_type],
        "ORD_QTY": _format_optional_wire_decimal(order.quantity),
        "ORD_UNPR": "0" if order.order_type == "market" else _format_optional_wire_decimal(order.limit_price),
        "EXCG_ID_DVSN_CD": "KRX",
    }
    return WireRequest("POST", _ORDER_CASH_PATH, tr_id, body)


def _make_credit_order_request(
    order: Order, cano: str, product_code: str, environment: Environment
) -> WireRequest:
    """국내 신용(융자/대주) 주문 와이어. 안전 코어(place)가 ``build_request`` 로 주입해 쓴다 --
    현금주문과 같은 즉시체결·ODNO 응답이라 dedup/재시도금지/reconcile 은 그대로 공유된다.

    **모의투자 미지원**, **국내 KRX 만**, 지정가/시장가·day 만. credit_type/loan_date 는 :class:`Order`
    생성 시점에 검증·확정되므로(신규=오늘, 상환=대상 대출일자), 이 빌더는 주문의 순수 함수다."""
    if environment == "demo":
        raise KISUsageError("신용주문(order-credit)은 모의투자 미지원 -- 실전에서만.")
    if order.exchange not in _DOMESTIC_MICS:
        raise KISUsageError(f"신용주문은 국내 주식만 지원한다(exchange={order.exchange!r}).")
    if order.credit_type is None or order.loan_date is None:  # Order 가 보장 -- 라우팅 방어
        raise OrderError("신용주문 빌더에 credit_type/loan_date 없는 주문이 들어왔다(라우팅 오류).")
    if order.order_type not in _ORD_DVSN:
        raise NotImplementedError(
            f"{order.order_type} 신용주문은 아직 와이어 매핑이 없다(현재 시장가/지정가만)."
        )
    if order.time_in_force != "day":
        raise NotImplementedError(
            f"time_in_force={order.time_in_force!r} 신용주문은 미구현이다(현재 day 만)."
        )
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "PDNO": order.symbol,
        "SLL_TYPE": "",                       # 공란(원장 지시)
        "CRDT_TYPE": order.credit_type,
        "LOAN_DT": order.loan_date,
        "ORD_DVSN": _ORD_DVSN[order.order_type],
        "ORD_QTY": _format_optional_wire_decimal(order.quantity),
        "ORD_UNPR": "0" if order.order_type == "market" else _format_optional_wire_decimal(order.limit_price),
        "RSVN_ORD_YN": "N",
        "EXCG_ID_DVSN_CD": "KRX",
    }
    return WireRequest("POST", _ORDER_CREDIT_PATH, _ORDER_CREDIT_TR[order.side], body)


def _fetch_daily_orders(
    transport: Transport, symbol: str, *, cano: str, product_code: str, environment: Environment
) -> list[Mapping[str, Any]]:
    """당일 일별체결조회를 연속조회 소진까지 읽어 한 종목의 모든 주문 행을 돌려준다(순수 I/O).

    에러 응답(rt_cd!=0)은 :class:`KISError` 로 올린다 -- 빈 결과로 오인해 '미접수'로 단정하면
    이중체결로 이어진다. 상한까지 갔는데 연속조회가 남으면 부분 스캔을 전부로 오인하지 않도록
    fail-closed(다중일치 가드 무력화 방지).
    """
    today = f"{datetime.now(_KST):%Y%m%d}"
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_RECONCILE_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "INQR_STRT_DT": today, "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00", "PDNO": symbol, "ORD_GNO_BRNO": "", "ODNO": "",
            "CCLD_DVSN": "00", "INQR_DVSN": "00", "INQR_DVSN_1": "", "INQR_DVSN_3": "00",
            "EXCG_ID_DVSN_CD": "", "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_DAILY_CCLD_PATH, tr_id=_DAILY_CCLD_TR[environment],
            params=params, idempotent=True, tr_cont=tr_cont,  # 읽기 -- 타임아웃에 재시도해도 안전
        )
        if not resp.ok:
            raise KISError(
                f"재조회(일별체결조회) 실패: {resp.msg1}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        page = resp.body.get("output1")
        rows.extend(page if isinstance(page, list) else [])  # 리스트 아니면 무시
        ctx_nk = str(resp.body.get("ctx_area_nk100") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk100") or "").strip()
        # 재조회는 조기 종료 금지(체결 누락->오재주문 위험): tr_cont 정본 종료(D/E/공백)이면서
        # 연속조회 커서도 소진됐을 때만 마지막 페이지로 확정한다(둘 중 하나라도 남으면 계속 스캔).
        if resp.tr_cont not in ("F", "M") and not ctx_nk:
            break
        tr_cont = "N"  # 다음 페이지는 연속조회
    else:
        raise KISError(
            f"재조회 스캔이 {_MAX_RECONCILE_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 스캔으로 확정하지 않는다. 재시도하거나 수동 확인하라."
        )
    return rows


def _filter_matching_daily_rows(
    rows: list[Mapping[str, Any]], fingerprint: Fingerprint
) -> list[Mapping[str, Any]]:
    """일별체결조회 행 중 요청 지문과 맞는 것만(순수). 종목+매매구분+주문구분+수량, 지정가면
    단가까지 비교해 무관한 동일수량 주문의 오귀속을 줄인다. 신용/현금은 행의 대출일자(loan_dt)로
    가른다 -- 안 그러면 같은 종목·수량·가격의 현금 체결이 미접수 신용주문을 phantom 확정할 수 있다."""
    symbol, side, order_type = fingerprint.symbol, fingerprint.side, fingerprint.order_type
    quantity = Decimal(fingerprint.quantity)
    limit_price = Decimal(fingerprint.limit_price) if fingerprint.limit_price else None
    want_side = _SIDE_CODE[side]
    want_dvsn = _ORD_DVSN.get(order_type)
    want_loan_date = fingerprint.loan_date        # 신용이면 대출일자, 현금이면 ""
    matched = []
    for row in rows:
        if str(row.get("pdno", "")) != symbol:
            continue
        if str(row.get("sll_buy_dvsn_cd", "")) != want_side:
            continue
        if want_dvsn is not None and str(row.get("ord_dvsn_cd", "")) not in ("", want_dvsn):
            continue
        # 신용/현금 구분: 신용주문 지문(loan_date != "")은 행의 loan_dt 가 그 대출일자와 같아야,
        # 현금주문 지문("")은 행에 대출일자가 없어야 매칭한다(cash<->credit 오확정 방지).
        if _normalize_loan_date(row.get("loan_dt")) != want_loan_date:
            continue
        if parse_response_decimal(row.get("ord_qty")) != quantity:
            continue
        # 지정가 주문은 단가가 있고 같아야 한다(fail-closed) -- 단가 없는 행 통과 시 무관한
        # 동일수량 주문을 오귀속할 수 있다. 단가 없으면 제외(안전 방향).
        if limit_price is not None:
            row_price = row.get("ord_unpr")
            if row_price in (None, "") or parse_response_decimal(row_price) != limit_price:
                continue
        matched.append(row)
    return matched


def _execution_report_from_daily_row(
    client_order_id: str, fingerprint: Fingerprint, row: Mapping[str, Any]
) -> ExecutionReport:
    ordered = parse_response_decimal(row.get("ord_qty"))
    filled = parse_response_decimal(row.get("tot_ccld_qty"))
    rejected = parse_response_decimal(row.get("rjct_qty"))
    if str(row.get("cncl_yn", "")).upper() == "Y":
        status = OrderStatus.CANCELED
    elif rejected > 0 and filled == 0:
        status = OrderStatus.REJECTED
    elif ordered > 0 and filled >= ordered:
        status = OrderStatus.FILLED
    elif filled > 0:
        status = OrderStatus.PARTIALLY_FILLED
    else:
        status = OrderStatus.NEW
    avg = parse_response_decimal(row.get("avg_prvs"))
    return ExecutionReport(
        client_order_id=client_order_id,
        order_id=str(row.get("odno")) if row.get("odno") else None,
        symbol=fingerprint.symbol,
        side=fingerprint.side,
        status=status,
        filled_quantity=filled,
        average_price=avg if filled > 0 and avg > 0 else None,
        submitted_at=datetime.now(_KST),
        _raw=row,
    )


def _normalize_loan_date(value: object) -> str:
    """일별체결조회 행의 대출일자를 지문의 loan_date 정본과 맞춘다 -- 공백/0채움("00000000")은
    '대출 없음(현금)'을 뜻하므로 ``""`` 로 본다."""
    text = str(value or "").strip()
    return "" if text.strip("0") == "" else text


def _extract_output_mapping(body: Mapping[str, Any]) -> Mapping[str, Any]:
    out = body.get("output", body)
    return out if isinstance(out, Mapping) else {}


def parse_response_decimal(value: object) -> Decimal:
    """KIS 문자열 수치를 Decimal 로. 공백/None 은 0. 값이 있는데 파싱 실패면 :class:`KISError`
    로 fail-closed -- 신뢰 못 할 숫자를 0으로 조작하면 재조회가 체결을 '미체결'로 오판한다."""
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as err:
        raise KISError(f"재조회 응답의 수치 파싱 실패: {value!r}") from err


def _format_optional_wire_decimal(value: Decimal | None) -> str:
    """주문 단가/수량을 KIS 와이어 정본 문자열로(``None`` -> "0")."""
    if value is None:
        return "0"
    return format_wire_decimal(value)
