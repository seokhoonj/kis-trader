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
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal

from ..._internal._datetime import _KST
from ..._internal._wire import format_wire_decimal
from ...errors import KISError, KISUsageError, OrderError, OrderTimeoutError
from ...order import (
    _DERIVATIVE_EXCHANGE,
    ChangeAction,
    ImmediateOrderFingerprint,
    Order,
    WireRequest,
)
from ...report import ExecutionReport, OrderStatus
from ...transport import TransportTimeout

if TYPE_CHECKING:
    from ...store import OrderStore
    from ...transport import Environment, Transport

_PLACE_PATH = "/uapi/domestic-futureoption/v1/trading/order"

#: (session, environment) -> tr_id. 야간(STTN)은 모의 미지원이라 real 만 있다.
_PLACE_TR: dict[str, dict[str, str]] = {
    "regular": {"real": "TTTO1101U", "paper": "VTTO1101U"},
    "night": {"real": "STTN1101U"},
}

_ACCOUNT_ORD_PROCESS = "02"                  # ORD_PRCS_DVSN_CD 고정(주문전송)
_SIDE_CODE = {"sell": "01", "buy": "02"}     # SLL_BUY_DVSN_CD (매수/매도 동일 TR)

_CHANGE_PATH = "/uapi/domestic-futureoption/v1/trading/order-rvsecncl"
#: (session, environment) -> tr_id. 주간 정정취소(TTTO/VTTO1103U)와 야간 정정취소(TTTN1103U,
#: 모의 미지원). 주간 빌더(:func:`make_change_request`)는 야간 지문을 fail-closed 로 거부하고,
#: 야간은 전용 빌더(:func:`make_night_change_request`)가 잔량 재지정 계약으로 조립한다.
_CHANGE_TR: dict[str, dict[str, str]] = {
    "regular": {"real": "TTTO1103U", "paper": "VTTO1103U"},
    "night": {"real": "TTTN1103U"},
}

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
        raise KISUsageError(f"파생 주문 수량은 계약 단위 정수여야 한다: {order.quantity}")
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


def make_change_request(
    *, original_report: ExecutionReport, original_fingerprint: ImmediateOrderFingerprint,
    action: ChangeAction, quantity: Decimal, limit_price: Decimal | None,
    cano: str, product_code: str, environment: Environment,
) -> WireRequest:
    """국내 파생(XKFE) 주간 정정·취소 요청 와이어(order-rvsecncl TTTO1103U/VTTO1103U) -- 안전
    코어(:func:`~kis_trader.domestic._engine.orders.submit_change`)에 ``build_request`` 로 주입한다.

    파생은 원주문 지목에 ``ORGN_ODNO`` 만 쓴다 -- 국내주식의 ``KRX_FWDG_ORD_ORGNO``(조직번호) 계약을
    재사용하지 않는다. 가격형상 규칙은 지정가 전용 자산의 공유 헬퍼(``reject_bad_change_price_shape``)가
    아니라 파생 전용 규칙을 인라인으로 강제한다: 취소는 단가·수량 확정 고정값(전량취소 ``ORD_QTY="0"``),
    지정가 정정은 새 단가(>0)를 요구하고 원지문에서 세 주문구분 코드를 :func:`_resolve_fo_codes` 로
    산출한다. 주간 코어는 지정가 정정만 지원하므로 원주문이 시장가/최유리면 fail-closed(취소 후 재주문
    유도). 야간(STTN1103U, 잔량 재지정)은 Task 7 -- 야간 지문은 여기서 막는다."""
    session = "night" if original_fingerprint.session == "night" else "regular"
    if session == "night":
        # 야간 정정취소(STTN1103U)는 잔량 재지정 계약이 달라 별도 경로(Task 7)다. 주간 빌더로
        # 조용히 흘리면 잘못된 TR/수량으로 나가므로 fail-closed 로 올린다.
        raise KISUsageError("파생 야간(STTN) 정정·취소는 별도 경로다 -- 주간 빌더로 처리할 수 없다.")
    try:
        tr_id = _CHANGE_TR[session][environment]
    except KeyError:
        raise KISUsageError(
            f"파생 정정·취소 TR 을 찾지 못했다: session={session} / {environment}."
        ) from None
    origin_odno = str(original_report.order_id)
    if action == "cancel":
        # 취소 확정 고정값(주간 전량취소): 단가·호가유형·체결조건을 원주문과 무관하게 고정하고
        # ORD_QTY="0"(전량), RMN_QTY_YN="Y". limit_price 는 무시하고 UNIT_PRICE=0.
        ord_dvsn, nmpr_type, krx_cndt, unit_price, ord_qty, rmn_qty = (
            "01", "01", "0", "0", "0", "Y"
        )
        rvse_cncl = "02"
    else:  # modify -- 주간 코어는 지정가 정정만
        base = original_fingerprint.division or original_fingerprint.order_type
        if original_fingerprint.order_type == "market" or base in ("market", "immediate_limit"):
            raise KISUsageError("시장가/최유리 정정은 미지원 -- 취소 후 재주문")
        if limit_price is None or limit_price <= 0:
            raise KISUsageError(f"파생 지정가 정정은 새 지정가(>0)가 필요하다: {limit_price!r}")
        ord_dvsn, nmpr_type, krx_cndt = _resolve_fo_codes(
            order_type=original_fingerprint.order_type,
            division=original_fingerprint.division,
            time_in_force=original_fingerprint.time_in_force,
        )
        unit_price = format_wire_decimal(limit_price)
        ord_qty = format_wire_decimal(quantity)
        rmn_qty = "N"                              # 일부(지정 수량)
        rvse_cncl = "01"
    body = {
        "ORD_PRCS_DVSN_CD": _ACCOUNT_ORD_PROCESS,
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "RVSE_CNCL_DVSN_CD": rvse_cncl,
        "ORGN_ODNO": origin_odno,
        "ORD_QTY": ord_qty,
        "UNIT_PRICE": unit_price,
        "NMPR_TYPE_CD": nmpr_type,
        "KRX_NMPR_CNDT_CD": krx_cndt,
        "RMN_QTY_YN": rmn_qty,
        "FUOP_ITEM_DVSN_CD": "",                   # 주간은 공란(야간은 make_night_change_request)
        "ORD_DVSN_CD": ord_dvsn,
    }
    return WireRequest("POST", _CHANGE_PATH, tr_id, body)


def make_night_change_request(
    *, original_report: ExecutionReport, original_fingerprint: ImmediateOrderFingerprint,
    action: ChangeAction, quantity: Decimal, limit_price: Decimal | None,
    cano: str, product_code: str, environment: Environment,
) -> WireRequest:
    """국내 파생(XKFE) **야간** 정정·취소 요청 와이어(order-rvsecncl ``STTN1103U``) -- 안전
    코어(:func:`~kis_trader.domestic._engine.orders.submit_change`)에 ``build_request`` 로 주입한다.

    주간 빌더(:func:`make_change_request`)와 바디 형상은 같되 야간의 두 계약이 다르다:

    1. ``ORD_QTY`` 는 **실잔량**이다 -- 주간 전량취소가 ``"0"`` 인 것과 달리 야간은 0/공백이 금지라
       실제 남은 수량을 실어야 한다(KIS 명세). 야간은 부분 정정·취소가 불가(잔량 전체가 대상)하므로
       ``RMN_QTY_YN="Y"``. 이 ``quantity`` 는 호출자(:meth:`~kis_trader.client.KISClient._change_order`)가
       와이어 직전 ``inquire-ngt-ccnl`` 로 신선 조회한 잔량이다 -- 이 빌더는 순수 유지(값만 받는다).
    2. ``FUOP_ITEM_DVSN_CD`` 는 원지문의 상품구분(01 선물 / 02 콜 / 03 풋)으로 야간엔 필수다.

    야간은 모의투자 미지원이라 ``paper`` 면 fail-closed. 취소는 단가·호가유형 확정 고정값, 지정가
    정정은 원지문에서 세 주문구분 코드를 산출하고 새 단가를 싣는다(주간과 동일). 원주문이 시장가/
    최유리면 정정 미지원(취소 후 재주문 유도). 주간 지문이 이 빌더로 새면 라우팅 오류라 fail-closed."""
    if original_fingerprint.session != "night":
        raise KISUsageError("파생 주간 지문이 야간 빌더로 들어왔다 -- 라우팅 오류(주간 빌더를 쓰라).")
    if environment == "paper":
        raise KISUsageError("파생 야간(STTN) 정정·취소는 모의투자 미지원 -- 실전에서만.")
    tr_id = _CHANGE_TR["night"]["real"]
    origin_odno = str(original_report.order_id)
    fuop_item = original_fingerprint.derivative_item
    if action == "cancel":
        # 취소 확정 고정값 -- 단가·호가유형·체결조건을 원주문과 무관하게 고정한다. 야간은 ORD_QTY 를
        # 0 으로 두지 못하므로(잔량 필수) 주입된 신선 잔량을 그대로 싣는다(아래 body).
        ord_dvsn, nmpr_type, krx_cndt, unit_price = "01", "01", "0", "0"
        rvse_cncl = "02"
    else:  # modify -- 코어는 지정가 정정만(주간과 대칭)
        base = original_fingerprint.division or original_fingerprint.order_type
        if original_fingerprint.order_type == "market" or base in ("market", "immediate_limit"):
            raise KISUsageError("시장가/최유리 정정은 미지원 -- 취소 후 재주문")
        if limit_price is None or limit_price <= 0:
            raise KISUsageError(f"파생 지정가 정정은 새 지정가(>0)가 필요하다: {limit_price!r}")
        ord_dvsn, nmpr_type, krx_cndt = _resolve_fo_codes(
            order_type=original_fingerprint.order_type,
            division=original_fingerprint.division,
            time_in_force=original_fingerprint.time_in_force,
        )
        unit_price = format_wire_decimal(limit_price)
        rvse_cncl = "01"
    body = {
        "ORD_PRCS_DVSN_CD": _ACCOUNT_ORD_PROCESS,
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "RVSE_CNCL_DVSN_CD": rvse_cncl,
        "ORGN_ODNO": origin_odno,
        "ORD_QTY": format_wire_decimal(quantity),  # 야간은 실잔량 필수(0/공백 금지) -- 주입값
        "UNIT_PRICE": unit_price,
        "NMPR_TYPE_CD": nmpr_type,
        "KRX_NMPR_CNDT_CD": krx_cndt,
        "RMN_QTY_YN": "Y",                          # 야간은 잔량 전체가 대상(부분 불가)
        "FUOP_ITEM_DVSN_CD": fuop_item,             # 야간 필수(01 선물 / 02 콜 / 03 풋)
        "ORD_DVSN_CD": ord_dvsn,
    }
    return WireRequest("POST", _CHANGE_PATH, tr_id, body)


def _extract_fo_output(body: Mapping[str, Any]) -> Mapping[str, Any]:
    """파생 주문 응답에서 ``output``(object)만 엄격히 뽑는다 -- 커널의 top-level 폴백을 쓰지 않는다.

    ``output`` 키가 없거나 Mapping 이 아니면 :class:`~kis_trader.errors.OrderError` 로 fail-closed
    한다(top-level 로 폴백해 엉뚱한 ODNO 를 읽으면 재조회 대상이 어긋나 오확정으로 이어진다)."""
    out = body.get("output")
    if not isinstance(out, Mapping):
        raise OrderError("파생 주문 응답에 output(object)이 없다 -- top-level 폴백 금지, 재조회 불가.")
    return out


# --- 재조회(국내 파생 일별체결내역) -----------------------------------------
_DAY_INQUIRY_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-ccnl"
#: (야간)선물옵션 주문체결내역조회 -- 야간 체결은 06:10 이관 전까지 주간 테이블에 없어 이 조회로 본다.
_NIGHT_INQUIRY_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-ngt-ccnl"
#: session -> environment -> tr_id. 주간(정규)은 실전/모의, 야간(STTN5201R)은 실전 전용.
_INQUIRY_TR: dict[str, dict[str, str]] = {
    "regular": {"real": "TTTO5201R", "paper": "VTTO5201R"},
    "night": {"real": "STTN5201R"},
}
#: 재조회 연속조회(페이지) 상한 -- 무한 루프 방지의 명시적 안전 상한.
_MAX_INQUIRY_PAGES = 100
#: 야간 union 재조회의 날짜창 폭 -- claim 앵커일(T) 기준 T-0 ~ T+N 영업일(주말 롤). 야간 체결은
#: 주간 테이블로 이관되고 주문일자가 T+1(금->월)이라, 넉넉한 창으로 이관 후 행까지 덮는다. 넉넉한
#: 창의 비용은 다중매칭 가능성뿐이고 그 결과는 KISError(안전 방향)다.
_NIGHT_WINDOW_BUSINESS_DAYS = 3
#: 구 v7 레코드(claim 시각 미상 "")의 폴백 창 -- now 기준 T-N ~ 오늘(달력일). 여전히 다중매칭
#: 가드가 오확정을 막는다.
_NIGHT_LEGACY_LOOKBACK_DAYS = 7


def reconcile(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment, now: datetime | None = None,
) -> ExecutionReport | None:
    """미확인 국내 파생(XKFE) 주간 주문의 실제 상태를 일별체결내역에서 재조회한다 -- **보수적**.

    완료 리포트가 있으면 반환. in-flight 면 일별체결내역(inquire-ccnl)을 지문으로 스캔해 정확히
    1건이면 확정, 0건이면 ``None``(재전송 금지 유지), 2건 이상이면 :class:`KISError`. **자동 해제는
    절대 하지 않는다.** 매칭은 원장 output1 에 실재하는 ``nmpr_type_cd``(호가유형) 기반이다 -- 그
    행에는 ``ord_dvsn_cd`` 가 없어 발주와 같은 리졸버(:func:`_resolve_fo_codes`)의 NMPR_TYPE_CD 로
    맞춘다. ``now`` 는 조회 날짜창의 기준시각(주입하면 결정적; 생략 시 현재 KST).

    주간 조회창은 기준시각(``now``)의 국내(KST) **당일 하루**뿐이라, 주간 주문을 그 이후의 다른
    달력일에 재조회하면 그 하루 창에 걸리지 않아 ``None``(재전송 금지의 in-flight 유지)이 된다 --
    자동 해제 없이 수동 확인으로 드러나는 안전 방향이다.

    야간(STTN) 주문은 주간 체결내역에 담기지 않아 여기서 확정하면 오확정이다 -- 야간 전용 조회
    (union 스캔)는 별도 경로라, 야간 지문이면 fail-closed 로 올려 수동 확인을 유도한다."""
    prior = store.report_for(client_order_id)
    if prior is not None:
        return prior
    fingerprint = store.fingerprint_for(client_order_id)
    if fingerprint is None:
        raise KISUsageError(
            f"모르는 client_order_id: {client_order_id!r} (이 계좌로 전송한 적이 없다)."
        )
    if not isinstance(fingerprint, ImmediateOrderFingerprint):
        raise KISError(
            f"client_order_id {client_order_id!r} 의 지문이 즉시주문이 아니다"
            f"({type(fingerprint).__name__}, 내부 상태 불일치)."
        )
    if fingerprint.session == "night":
        # 야간(STTN)은 실전 전용이라 paper 야간 지문 도달은 지문 손상 신호다(애초에 paper 야간 발주가
        # 불가) -- 조용히 조회하지 않고 명확히 fail-closed.
        if environment == "paper":
            raise KISUsageError(
                f"파생 야간(STTN) 주문 {client_order_id!r} 이 모의(paper) 세션에 있다 -- 야간은 실전 "
                f"전용이라 이는 지문 손상 신호다(자동 확정하지 않는다)."
            )
        # 야간 체결은 06:10 경 주간 테이블로 이관되고 주문일자가 T+1 이라, 야간 테이블(STTN5201R)과
        # 주간 테이블(TTTO5201R)을 날짜창으로 함께 훑는 union 스캔으로 확인한다(:func:`_night_union_reconcile`).
        return _night_union_reconcile(
            transport, store, client_order_id, fingerprint,
            cano=cano, product_code=product_code, environment=environment, now=now,
        )
    try:
        rows = _fetch_day_ccnl(
            transport, symbol=fingerprint.symbol, cano=cano,
            product_code=product_code, environment=environment, now=now,
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"파생 재조회(일별체결내역) 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
            f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
            client_order_id=client_order_id,
        ) from err
    matches = _filter_matching_day_rows(rows, fingerprint)
    if len(matches) > 1:
        raise KISError(
            f"주문 {client_order_id} 의 지문과 일치하는 체결내역이 {len(matches)}건이라 자동 확정 "
            f"불가하다(KIS가 client_order_id를 돌려주지 않음). 수동 확인이 필요하다."
        )
    if not matches:  # 0건: 미접수인지 반영 지연인지 단정 불가 -> in-flight 유지
        return None
    report = _fo_report(client_order_id, fingerprint, matches[0])
    store.record(report, fingerprint)
    return report


def _fetch_day_ccnl(
    transport: Transport, *, symbol: str, cano: str, product_code: str,
    environment: Environment, now: datetime | None = None,
    strt_ord_dt: str | None = None, end_ord_dt: str | None = None,
) -> list[Mapping[str, Any]]:
    """파생 주간 일별체결내역을 연속조회 소진까지 읽어 행을 돌려준다(순수 I/O). 에러 응답/오형상은
    fail-closed -- 빈 결과로 오인해 '미접수'로 단정하면 이중체결로 이어진다. 날짜창은 ``strt_ord_dt``/
    ``end_ord_dt`` 를 주면 그 창을(야간 union 의 다리 B), 없으면 ``now``(주입 시 결정적, 없으면 현재
    KST) 기준 국내 당일 하루를 본다(주간 재조회). 주간 END 는 포함(inclusive)이다."""
    if strt_ord_dt is None or end_ord_dt is None:
        stamp = datetime.now(_KST) if now is None else now
        strt_ord_dt = end_ord_dt = f"{stamp:%Y%m%d}"
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_INQUIRY_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "STRT_ORD_DT": strt_ord_dt, "END_ORD_DT": end_ord_dt,
            "SLL_BUY_DVSN_CD": "00", "CCLD_NCCS_DVSN": "00", "SORT_SQN": "DS",
            "STRT_ODNO": "", "PDNO": symbol, "MKET_ID_CD": "00",
            "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_DAY_INQUIRY_PATH, tr_id=_INQUIRY_TR["regular"][environment],
            params=params, idempotent=True, tr_cont=tr_cont,  # 읽기 -- 타임아웃 재시도 안전
        )
        if not resp.ok:
            raise KISError(
                f"파생 재조회(일별체결내역) 실패: {resp.msg1}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        page = resp.body.get("output1")
        if not isinstance(page, list):
            raise KISError(
                "파생 재조회 응답의 output1 이 리스트가 아니다 -- 부분/오응답으로 확정하지 않는다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        for row in page:
            if not isinstance(row, Mapping):
                raise KISError(
                    "파생 재조회 응답의 output1 에 매핑이 아닌 원소가 있다 -- 오응답으로 확정하지 않는다.",
                    rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
                )
        rows.extend(page)
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        # 재조회는 조기 종료 금지(체결 누락->오재주문 위험): tr_cont 정본 종료(D/E/공백)이면서
        # 연속조회 커서도 소진됐을 때만 마지막 페이지로 확정(둘 중 하나라도 남으면 계속 스캔).
        if resp.tr_cont not in ("F", "M") and not ctx_nk:
            break
        tr_cont = "N"
    else:
        raise KISError(
            f"파생 재조회 스캔이 {_MAX_INQUIRY_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 스캔으로 확정하지 않는다. 재시도하거나 수동 확인하라."
        )
    return rows


def _filter_matching_day_rows(
    rows: list[Mapping[str, Any]], fingerprint: ImmediateOrderFingerprint
) -> list[Mapping[str, Any]]:
    """일별체결내역 행 중 요청 지문과 맞는 것만(순수). 종목+매매구분+호가유형(nmpr_type_cd)+주문수량,
    지정가는 주문단가(ord_idx)까지 비교한다. 호가유형은 발주와 **같은** 리졸버의 NMPR_TYPE_CD 로 --
    이 행에는 ord_dvsn_cd 가 없어 그걸 요구하면 매칭이 전부 사라진다. 정정/취소 행(원주문번호를
    참조 -- orgn_odno != 0)은 원주문 지문 매칭에서 제외해 가짜 다중매칭을 줄인다.

    한계: 대조에 쓰는 ``nmpr_type_cd``(호가유형)는 유효기간 변형이 공유한다 -- day/ioc/fok 은 같은
    NMPR_TYPE_CD 로 풀려, 종목·수량·가격이 같고 ``time_in_force`` 만 다른 두 주문은 이 매처가
    구별하지 못한다. 흔한 경우(두 행이 모두 존재)엔 다중매칭이 되어 :class:`KISError`(안전 방향)로
    떨어진다."""
    symbol, side = fingerprint.symbol, fingerprint.side
    quantity = Decimal(fingerprint.quantity)
    limit_price = Decimal(fingerprint.limit_price) if fingerprint.limit_price else None
    want_side = _SIDE_CODE[side]
    want_nmpr = _resolve_fo_codes(
        order_type=fingerprint.order_type, division=fingerprint.division,
        time_in_force=fingerprint.time_in_force,
    )[1]
    matched = []
    for row in rows:
        if not _is_original_odno(row.get("orgn_odno")):
            continue
        if str(row.get("pdno", "")) != symbol:
            continue
        if str(row.get("sll_buy_dvsn_cd", "")) != want_side:
            continue
        if str(row.get("nmpr_type_cd", "")) != want_nmpr:
            continue
        if _parse_decimal(row.get("ord_qty")) != quantity:
            continue
        if limit_price is not None:  # 지정가는 주문단가가 있고 같아야 한다(없으면 제외, 안전 방향)
            row_price = row.get("ord_idx")
            if row_price in (None, "") or _parse_decimal(row_price) != limit_price:
                continue
        matched.append(row)
    return matched


# --- 야간(NIGHT) union 재조회 + 신선 잔량 --------------------------------
def _night_union_reconcile(
    transport: Transport, store: OrderStore, client_order_id: str,
    fingerprint: ImmediateOrderFingerprint, *,
    cano: str, product_code: str, environment: Environment, now: datetime | None,
) -> ExecutionReport | None:
    """야간(STTN) 주문을 두 테이블의 union 으로 재조회한다(:func:`reconcile` 의 야간 분기).

    야간 체결은 06:10 경 주간 테이블로 이관되고 주문일자가 T+1 이라, **다리 A**(야간 테이블
    ``inquire-ngt-ccnl``)와 **다리 B**(주간 테이블 ``inquire-ccnl``)를 claim 앵커 날짜창으로 함께
    훑는다. **all-or-nothing**: 어느 다리든 실패(rt_cd!=0/오형상)면 다른 다리 결과와 무관하게
    :class:`KISError` -- 부분 스캔을 성공으로 오인하지 않는다. 테이블별 독립 매칭 후 병합:
    A만/B만 1건이면 확정, A 1건+B 1건이면 ``(odno, ord_dt)`` **둘 다** 같을 때만 "이관 중 동일 주문"
    으로 병합 확정(하나라도 다르면 모호 -> KISError), 어느 테이블이든 2건 이상이면 KISError, 양측
    0건이면 ``None``(이관 과도기의 일시 부재 가능 -- in-flight 유지). odno 단독으로는 절대 dedup 하지
    않는다(교차 테이블 odno 유일성 미확정)."""
    anchor = store.claim_time_for(client_order_id)
    start, end_inclusive = _night_date_window(anchor, now)
    fuop = _fuop_dvsn_from_symbol(fingerprint.symbol)
    try:
        # 두 다리를 모두 먼저 소진 조회한다(all-or-nothing) -- 한 다리라도 raise 하면 병합에 이르지
        # 않는다. 야간 END 는 배타(exclusive)라 포함 끝일 다음날까지 준다.
        rows_a = _fetch_night_ccnl(
            transport, symbol=fingerprint.symbol, cano=cano, product_code=product_code,
            environment=environment, fuop_dvsn=fuop,
            strt_ord_dt=start, end_ord_dt=_plus_one_day(end_inclusive),
        )
        rows_b = _fetch_day_ccnl(
            transport, symbol=fingerprint.symbol, cano=cano, product_code=product_code,
            environment=environment, strt_ord_dt=start, end_ord_dt=end_inclusive,
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"파생 야간 재조회(union 스캔) 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
            f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
            client_order_id=client_order_id,
        ) from err
    matches_a = _filter_matching_night_rows(rows_a, fingerprint)
    matches_b = _filter_matching_day_rows(rows_b, fingerprint)
    if len(matches_a) > 1 or len(matches_b) > 1:
        raise KISError(
            f"야간 주문 {client_order_id} 의 지문과 일치하는 체결내역이 야간 {len(matches_a)}건/주간 "
            f"{len(matches_b)}건이라 자동 확정 불가하다(KIS가 client_order_id를 돌려주지 않음). "
            f"수동 확인이 필요하다."
        )
    if matches_a and matches_b:
        row_a, row_b = matches_a[0], matches_b[0]
        same = (str(row_a.get("odno")) == str(row_b.get("odno"))
                and str(row_a.get("ord_dt")) == str(row_b.get("ord_dt")))
        if not same:
            raise KISError(
                f"야간 주문 {client_order_id} 이 야간/주간 테이블에서 서로 다른 (odno, ord_dt) 로 "
                f"보인다 -- 이관 중 동일 주문으로 병합할 수 없다(모호). 수동 확인이 필요하다."
            )
        row = row_b                         # (odno, ord_dt) 동일 -> 이관 완료본(주간)을 정본으로
    elif matches_a:
        row = matches_a[0]                  # 이관 전(야간 테이블에만)
    elif matches_b:
        row = matches_b[0]                  # 이관 후(주간 테이블에만)
    else:
        return None                         # 양측 0건 -- 미접수로 단정하지 않는다(in-flight 유지)
    report = _fo_report(client_order_id, fingerprint, row)
    store.record(report, fingerprint)
    return report


def fetch_night_remaining(
    transport: Transport, *, order_id: str, symbol: str, cano: str, product_code: str,
    environment: Environment, anchor: str | None, now: datetime | None = None,
) -> Decimal:
    """야간 정정·취소 직전 ``inquire-ngt-ccnl`` 로 원주문(``order_id``)의 **신선 잔량**을 읽는다.

    야간 취소·정정은 ``ORD_QTY`` 에 실잔량이 필수인데(0/공백 금지) 로컬 리포트의 잔량은 부분체결 직후
    stale 할 수 있어, 와이어 직전에 이 조회로 확정한다. 원주문 odno 는 접수 시 확정된 값이라 **odno
    단독 매칭**이 정확하다(재조회와 달리 대상이 명확). 정확히 1건이 아니거나(0행/다행) 조회 실패(rt_cd!=0/
    오형상)면 :class:`KISError` 로 fail-closed -- 취소 와이어에 닿지 않는다. 읽기 전용이라 claim 전에
    수행할 수 있다.

    실잔량은 **주문수량(``ord_qty``) - 총체결수량(``tot_ccld_qty``)** 으로 계산한다 -- STTN5201R
    응답예시가 원장에서 비어 있어 단일 ``qty`` 필드가 잔량인지 주문수량인지 확증되지 않아, 그 필드를
    믿고 취소 수량으로 실으면 잘못된 수량이 나갈 수 있다. 두 확정 필드의 차분은 모호하지 않다. 둘 중
    하나라도 없으면(누락/공백) 실잔량을 확정할 수 없어 fail-closed 한다. (야간 output1 의 정확한
    필드명은 라이브 응답으로 재확인이 필요하다 -- 원장 응답예시 대조.)"""
    start, end_inclusive = _night_date_window(anchor, now)
    rows = _fetch_night_ccnl(
        transport, symbol=symbol, cano=cano, product_code=product_code,
        environment=environment, fuop_dvsn=_fuop_dvsn_from_symbol(symbol),
        strt_ord_dt=start, end_ord_dt=_plus_one_day(end_inclusive),
    )
    matched = [row for row in rows if str(row.get("odno")) == str(order_id)]
    if len(matched) != 1:
        raise KISError(
            f"야간 잔량 조회에서 원주문 {order_id} 행이 정확히 1건이 아니다({len(matched)}건) -- "
            f"신선 잔량을 확정할 수 없어 정정·취소를 중단한다(와이어 미접촉)."
        )
    row = matched[0]
    ordered, filled = row.get("ord_qty"), row.get("tot_ccld_qty")
    if ordered in (None, "") or filled in (None, ""):
        raise KISError(
            f"야간 잔량 조회 행에 주문수량/총체결수량이 없어 실잔량(주문-체결)을 확정할 수 없다 -- "
            f"원주문 {order_id} 정정·취소를 중단한다(와이어 미접촉)."
        )
    return _parse_decimal(ordered) - _parse_decimal(filled)


def _fetch_night_ccnl(
    transport: Transport, *, symbol: str, cano: str, product_code: str, environment: Environment,
    fuop_dvsn: str, strt_ord_dt: str, end_ord_dt: str,
) -> list[Mapping[str, Any]]:
    """(야간)선물옵션 주문체결내역을 연속조회 소진까지 읽어 행을 돌려준다(순수 I/O). 주간 리더와 같은
    fail-closed 규칙 -- 에러 응답/output1 비리스트/원소 비-Mapping 은 :class:`KISError`(빈 결과로 오인
    금지). 야간은 실전 전용이라 TR 은 ``STTN5201R``, ``FUOP_DVSN_CD``(선물/옵션)와 ``SCRN_DVSN`` 이
    붙고 ``END_ORD_DT`` 는 배타(exclusive)다(호출자가 포함 끝일 +1 을 넘긴다)."""
    tr_id = _INQUIRY_TR["night"]["real"]
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_INQUIRY_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "STRT_ORD_DT": strt_ord_dt, "END_ORD_DT": end_ord_dt,
            "SLL_BUY_DVSN_CD": "00", "CCLD_NCCS_DVSN": "00", "SORT_SQN": "DS",
            "STRT_ODNO": "", "PDNO": symbol, "MKET_ID_CD": "00", "FUOP_DVSN_CD": fuop_dvsn,
            "SCRN_DVSN": "02", "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_NIGHT_INQUIRY_PATH, tr_id=tr_id,
            params=params, idempotent=True, tr_cont=tr_cont,  # 읽기 -- 타임아웃 재시도 안전
        )
        if not resp.ok:
            raise KISError(
                f"파생 야간 재조회(주문체결내역) 실패: {resp.msg1}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        page = resp.body.get("output1")
        if not isinstance(page, list):
            raise KISError(
                "파생 야간 재조회 응답의 output1 이 리스트가 아니다 -- 부분/오응답으로 확정하지 않는다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        for row in page:
            if not isinstance(row, Mapping):
                raise KISError(
                    "파생 야간 재조회 응답의 output1 에 매핑이 아닌 원소가 있다 -- 오응답으로 확정하지 않는다.",
                    rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
                )
        rows.extend(page)
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        # 조기 종료 금지(체결 누락->오재주문 위험): tr_cont 정본 종료(D/E/공백)이면서 연속조회 커서도
        # 소진됐을 때만 마지막 페이지로 확정(둘 중 하나라도 남으면 계속 스캔).
        if resp.tr_cont not in ("F", "M") and not ctx_nk:
            break
        tr_cont = "N"
    else:
        raise KISError(
            f"파생 야간 재조회 스캔이 {_MAX_INQUIRY_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 스캔으로 확정하지 않는다. 재시도하거나 수동 확인하라."
        )
    return rows


def _filter_matching_night_rows(
    rows: list[Mapping[str, Any]], fingerprint: ImmediateOrderFingerprint
) -> list[Mapping[str, Any]]:
    """(야간)주문체결내역 행 중 요청 지문과 맞는 것만(순수). 주간 매처(:func:`_filter_matching_day_rows`)와
    같은 구조지만 야간 output1 의 필드명이 달라 **확정 필드만** 대조한다: 종목(``pdno``)·매매구분
    (``sll_buy_dvsn_cd``)·주문수량(``ord_qty``), 지정가는 주문가격(``ord_idx4``)까지. 야간 output1 에는
    호가유형코드(``nmpr_type_cd``)가 없고 이름(``nmpr_type_name``)만 있어(라이브 미검증) 호가유형은
    대조하지 않는다 -- 대신 더 적은 필드로 좁히므로 오확정보다 다중매칭(→ KISError, 안전 방향)으로
    떨어진다. 정정/취소 행(``orgn_odno`` != 0)은 원주문 매칭에서 제외한다."""
    symbol, side = fingerprint.symbol, fingerprint.side
    quantity = Decimal(fingerprint.quantity)
    limit_price = Decimal(fingerprint.limit_price) if fingerprint.limit_price else None
    want_side = _SIDE_CODE[side]
    matched = []
    for row in rows:
        if not _is_original_odno(row.get("orgn_odno")):
            continue
        if str(row.get("pdno", "")) != symbol:
            continue
        if str(row.get("sll_buy_dvsn_cd", "")) != want_side:
            continue
        if _parse_decimal(row.get("ord_qty")) != quantity:
            continue
        if limit_price is not None:  # 지정가는 주문가격(ord_idx4)이 있고 같아야 한다(없으면 제외, 안전 방향)
            row_price = row.get("ord_idx4")
            if row_price in (None, "") or _parse_decimal(row_price) != limit_price:
                continue
        matched.append(row)
    return matched


def _fuop_dvsn_from_symbol(symbol: str) -> Literal["01", "02"]:
    """야간 조회의 ``FUOP_DVSN_CD`` 를 심볼 길이로 정한다 -- 6자리는 선물("01"), 9자리는 옵션("02").
    그 밖의 길이는 예상 밖 심볼 형상이라 :class:`KISError` 로 fail-closed(엉뚱한 구분으로 훑지 않는다)."""
    length = len(symbol)
    if length == 6:
        return "01"
    if length == 9:
        return "02"
    raise KISError(
        f"야간 조회 선물옵션구분(FUOP_DVSN_CD)을 심볼 길이로 정할 수 없다: {symbol!r} (6=선물/9=옵션)."
    )


def _night_date_window(anchor: str | None, now: datetime | None) -> tuple[str, str]:
    """야간 union/신선조회의 날짜창 (STRT_ORD_DT, 포함 끝일 END) YYYYMMDD 를 정한다.

    창은 **제출 시점**(store v8 의 in-flight claim 시각)을 앵커로 잡아 재기동 후에도 T/T+1 을 정확히
    복원한다: 기준일 T = claim 의 달력일(KST), 창 = T-0 ~ T+``_NIGHT_WINDOW_BUSINESS_DAYS`` 영업일
    (주말 롤). 구 v7 레코드(claim 시각 미상 "")·손상 앵커는 폴백으로 ``now`` 기준 최광폭 창
    (T-``_NIGHT_LEGACY_LOOKBACK_DAYS`` ~ 오늘)을 쓴다 -- 넉넉한 창의 비용은 다중매칭(→ KISError)뿐이다."""
    stamp = datetime.now(_KST) if now is None else now
    base: date | None = None
    if anchor:
        try:
            base = datetime.fromisoformat(anchor).astimezone(_KST).date()
        except ValueError:
            base = None
    if base is None:
        end = stamp.astimezone(_KST).date()
        start = end - timedelta(days=_NIGHT_LEGACY_LOOKBACK_DAYS)
        return f"{start:%Y%m%d}", f"{end:%Y%m%d}"
    end = _add_business_days(base, _NIGHT_WINDOW_BUSINESS_DAYS)
    return f"{base:%Y%m%d}", f"{end:%Y%m%d}"


def _add_business_days(start: date, business_days: int) -> date:
    """``start`` 에서 영업일(월~금) ``business_days`` 만큼 뒤 날짜(주말은 건너뛴다). 정밀 휴장
    캘린더는 이후 슬라이스 -- 여기선 주말 롤만(넉넉한 창이라 휴일 미반영은 안전 방향)."""
    current = start
    added = 0
    while added < business_days:
        current += timedelta(days=1)
        if current.weekday() < 5:      # 월(0)~금(4)
            added += 1
    return current


def _plus_one_day(yyyymmdd: str) -> str:
    """포함 끝일(YYYYMMDD)의 다음날 -- 야간 조회 ``END_ORD_DT`` 는 배타(exclusive)라 포함시키려면 +1일."""
    parsed = datetime.strptime(yyyymmdd, "%Y%m%d").date()  # noqa: DTZ007 -- 날짜 산술만
    return f"{parsed + timedelta(days=1):%Y%m%d}"


def _fo_report(
    client_order_id: str, fingerprint: ImmediateOrderFingerprint, row: Mapping[str, Any]
) -> ExecutionReport:
    """매칭된 체결내역 행을 표준 리포트로. 상태 사다리: 거부(rjct_qty>0 & 미체결) -> 전량체결 ->
    일부체결 -> 접수(NEW). 체결가 필드가 없어(주문단가 ord_idx 만) 평균가는 남기지 않는다."""
    ordered = _parse_decimal(row.get("ord_qty"))
    filled = _parse_decimal(row.get("tot_ccld_qty"))
    rejected = _parse_decimal(row.get("rjct_qty"))
    if rejected > 0 and filled == 0:
        status = OrderStatus.REJECTED
    elif ordered > 0 and filled >= ordered:
        status = OrderStatus.FILLED
    elif filled > 0:
        status = OrderStatus.PARTIALLY_FILLED
    else:
        status = OrderStatus.NEW
    return ExecutionReport(
        client_order_id=client_order_id,
        order_id=str(row.get("odno")) if row.get("odno") else None,
        symbol=fingerprint.symbol,
        side=fingerprint.side,
        status=status,
        filled_quantity=filled,
        average_price=None,
        recorded_at=datetime.now(_KST),
        _raw=row,
    )


def _is_original_odno(value: object) -> bool:
    """행의 원주문번호가 원주문 센티넬(0-채움/빈값)인지 -- 아니면 정정/취소 행이라 매칭에서 뺀다."""
    return str(value or "").strip().strip("0") == ""


def _parse_decimal(value: object) -> Decimal:
    """KIS 문자열 수치 -> Decimal. 공백/None 은 0, 값이 있는데 파싱 실패면 fail-closed(:class:`KISError`).

    ``"nan"``/``"inf"`` 는 파싱되지만 비유한값이라 이후 수량·단가 비교가 무너져 오확정을 부른다 --
    :meth:`Decimal.is_finite` 로 fail-closed 한다."""
    if value is None or value == "":
        return Decimal(0)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as err:
        raise KISError(f"파생 재조회 응답의 수치 파싱 실패: {value!r}") from err
    if not number.is_finite():
        raise KISError(f"파생 재조회 응답의 수치가 유한하지 않다(NaN/Infinity): {value!r}")
    return number
