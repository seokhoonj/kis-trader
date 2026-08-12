"""미국 해외주식 예약주문 엔진 (내부) -- 조회(+발주/취소는 별 슬라이스).

미국 예약주문은 정규장 시작 전에 걸어두는 예약으로, 해외예약주문번호(ovrs_rsvn_odno)로 식별한다.
아시아(일/중/홍/베) 예약은 request/response 규격이 다른 별 프로토콜(TTTS3013U/TTTS3014R)이라 여기서
다루지 않는다. 전부 **모의투자 미지원**.

KIS URL/TR-id:
- 조회: ``GET .../trading/order-resv-list`` (미국 ``TTTT3039R``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .._domestic.orders import parse_response_decimal as _parse_response_decimal
from .._wire import format_wire_decimal, optional_decimal
from ..errors import (
    AccountNotOrderableError,
    KISError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
)
from ..order import Fingerprint, Side, coerce_decimal, validate_yyyymmdd
from ..overseas_items import OverseasReservedOrder
from ..report import ExecutionReport, OrderStatus
from ..store import ClaimOutcome, OrderStore
from ..transport import Environment, Transport, TransportTimeout
from .orders import _ORDER_EXCHANGE

_KST = timezone(timedelta(hours=9))

_LIST_PATH = "/uapi/overseas-stock/v1/trading/order-resv-list"
_LIST_TR = "TTTT3039R"             # 미국, 모의투자 미지원
_PLACE_PATH = "/uapi/overseas-stock/v1/trading/order-resv"
_PLACE_TR = {"buy": "TTTT3014U", "sell": "TTTT3016U"}  # 미국, 모의투자 미지원
_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-resv-ccnl"
_CANCEL_TR = "TTTT3017U"           # 미국 예약취소, 모의투자 미지원
#: 연속조회 페이지 상한. 닿으면 fail-closed.
_MAX_PAGES = 100
_SIDE = {"01": "sell", "02": "buy"}
_SIDE_CODE = {"buy": "02", "sell": "01"}
_US_MARKET = "US"
_ORD_DVSN_LIMIT = "00"             # 지정가
#: 예약 지문의 exchange 네임스페이스 -- 즉시/주간/국내예약과 분리해 reconcile 을 해외예약 경로로 라우팅.
_RESERVED_EXCHANGE = "overseas-reserved"
#: reconcile 창(양방향). KIS 명세상 예약 조회일자 필드 의미가 모호해 앞뒤로 스캔한다(실 API 검증 후 축소).
_RECONCILE_LOOKBACK_DAYS = 7
_RECONCILE_FORWARD_DAYS = 31


def fetch_reserved_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str,
) -> list[OverseasReservedOrder]:
    """미국 해외주식 예약주문 조회(연속조회 소진까지). ``start``/``end`` 는 기간(YYYYMMDD).
    거래소/상품유형 공백=미국 전체. **모의투자 미지원**."""
    if environment == "demo":
        raise KISUsageError("해외 예약주문조회(order-resv-list)는 모의투자 미지원 -- 실전에서만.")
    rows = _walk(transport, cano, product_code, start, end)
    return [_parse(row) for row in rows if str(row.get("ovrs_rsvn_odno", "")).strip()]


def _walk(
    transport: Transport, cano: str, product_code: str, start: str, end: str
) -> list[Mapping[str, Any]]:
    """미국 예약주문 조회를 연속조회 소진까지 읽어 원본 행을 돌려준다(순수 I/O)."""
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "INQR_STRT_DT": start, "INQR_END_DT": end,
            "INQR_DVSN_CD": "00",                     # KIS 명세: 00 = 전체(집행/미집행 모두)
            "PRDT_TYPE_CD": "", "OVRS_EXCG_CD": "",   # 공백 = 미국 전체
            "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_LIST_PATH, tr_id=_LIST_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        if not resp.ok:
            raise KISError(
                f"해외 예약주문조회 실패: {resp.msg1}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        page = resp.body.get("output")
        if not isinstance(page, list):  # 빈 내역도 배열 -> 부재/비배열은 손상
            raise KISError(
                "해외 예약주문조회 응답의 output 이 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        # 재조회는 조기 종료 금지(예약 누락->오확정->이중발주 위험): tr_cont 정본 종료이면서 연속조회
        # 커서도 소진됐을 때만 멈춘다(둘 중 하나라도 남으면 계속 스캔). 즉시/국내 예약 재조회와 동형.
        if resp.tr_cont not in ("F", "M") and not ctx_nk:
            break
        tr_cont = "N"
    else:
        raise KISError(
            f"해외 예약주문조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다."
        )
    return rows


# --- 미국 해외예약 발주 (뮤테이션, 안전 흐름) ------------------------------
def place_overseas_reserved_order(
    transport: Transport, store: OrderStore, *,
    symbol: str, side: Side, quantity: object, price: object, exchange: str,
    client_order_id: str, orderable: bool = True,
    cano: str, product_code: str, environment: Environment,
) -> ExecutionReport:
    """미국 해외주식 예약주문을 안전 규칙으로 발주한다 -- 즉시주문(place)과 같은 이중발주 방지·재시도
    금지·보수적 재조회를, 예약 라이프사이클(예약번호·체결개념 없음)에 맞춰 구현한다. **지정가만**
    (``price`` 필수), **미국(NAS/NYS/AMS)만**, **모의투자 미지원**.

    반환 :class:`ExecutionReport` 의 ``order_id`` 는 해외예약주문번호(KIS 명세상 발주 Output ODNO = 취소 시
    OVRS_RSVN_ODNO), ``status`` 는 :attr:`OrderStatus.PENDING_NEW`. 조회전용 계좌면
    :class:`AccountNotOrderableError`, 접수 거부는 :class:`OrderRejectedError`, 접수 불명(타임아웃)은
    :class:`OrderTimeoutError`(:func:`reconcile_overseas_reserved_order` 로 확인). demo·비-미국 거래소·
    잘못된 side/수량/가격·client_order_id 재사용은 :class:`KISUsageError`, 순번 부재는 :class:`OrderError`.
    """
    if not orderable:
        raise AccountNotOrderableError(
            "이 계좌는 API 주문이 불가하다(퇴직연금 IRP/DC 등 조회전용). 일반/연금저축 계좌를 쓰라."
        )
    if environment == "demo":
        raise KISUsageError("해외 예약주문(order-resv)은 모의투자 미지원 -- 실전에서만.")
    if side not in _SIDE_CODE:
        raise KISUsageError(f"side 는 buy/sell 이어야 한다: {side!r}")
    try:
        order_exchange, market = _ORDER_EXCHANGE[exchange]
    except KeyError:
        raise KISUsageError(
            f"해외 예약주문을 지원하지 않는 거래소코드: {exchange!r}."
        ) from None
    if market != _US_MARKET:
        raise KISUsageError(f"해외 예약주문은 미국(NAS/NYS/AMS)만 지원한다: {exchange!r}.")
    qty = coerce_decimal(quantity, "quantity")
    if qty <= 0 or qty != qty.to_integral_value():
        raise KISUsageError(f"예약주문 수량은 0보다 큰 정수(주)여야 한다: {qty}")
    limit_price = coerce_decimal(price, "price")
    if not limit_price.is_finite() or limit_price <= 0:
        raise KISUsageError(f"price 는 0보다 큰 유한값이어야 한다: {price!r}")

    fingerprint = Fingerprint(
        symbol=symbol, side=side, order_type="limit",
        quantity=format_wire_decimal(qty), limit_price=format_wire_decimal(limit_price),
        stop_price="", time_in_force="day", exchange=_RESERVED_EXCHANGE,
    )
    body = {
        "CANO": cano, "ACNT_PRDT_CD": product_code, "PDNO": symbol,
        "OVRS_EXCG_CD": order_exchange,
        "FT_ORD_QTY": format_wire_decimal(qty),
        "FT_ORD_UNPR3": format_wire_decimal(limit_price),
        "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": _ORD_DVSN_LIMIT,
    }

    outcome, prior = store.try_claim(client_order_id, fingerprint)
    if outcome is ClaimOutcome.COMPLETED:
        if prior is None:
            raise OrderError("claim 이 COMPLETED 인데 리포트가 없다(store 불변식 위반).")
        return prior
    if outcome is ClaimOutcome.CONFLICT:
        raise KISUsageError(
            f"client_order_id {client_order_id!r} 는 이미 다른 주문에 사용됐다. 새 id를 발행하라."
        )
    if outcome is ClaimOutcome.IN_FLIGHT:
        raise KISUsageError(
            f"해외 예약주문 {client_order_id} 은 전송됐으나 결과가 확인되지 않았다. "
            f"kis.orders.reconcile({client_order_id!r}) 로 재조회한 뒤 판단하라."
        )
    if outcome is not ClaimOutcome.CLAIMED:
        raise OrderError(f"예상치 못한 claim outcome: {outcome!r}")

    try:
        resp = transport.request(
            method="POST", path=_PLACE_PATH, tr_id=_PLACE_TR[side], body=body, idempotent=False
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"해외 예약주문 전송 시간초과 -- 접수 여부가 불명이다. 재전송하지 말고 "
            f"kis.orders.reconcile({client_order_id!r}) 로 재조회하라.",
            client_order_id=client_order_id,
        ) from err
    if not resp.ok:
        store.clear_in_flight(client_order_id)
        raise OrderRejectedError(
            f"해외 예약주문 접수 거부: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    reserved_id = _extract_reserved_id(resp.body)
    if not reserved_id:
        raise OrderError(
            "해외 예약주문 접수 응답(rt_cd=0)에 예약주문번호(ODNO)가 없다 -- 재조회 불가.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    report = _make_report(client_order_id, reserved_id, symbol, side, resp.body)
    store.record(report, fingerprint)
    return report


def reconcile_overseas_reserved_order(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment, now: datetime | None = None,
) -> ExecutionReport | None:
    """미확인 미국 해외예약주문을 **예약주문조회**로 재조회한다 -- 보수적. 완료 리포트가 있으면 반환.
    in-flight 면 최근 창의 예약을 지문으로 매칭: 정확히 1건이면 확정, 0건이면 ``None``(재전송 금지 유지),
    2건 이상이면 :class:`KISError`. ``now`` 는 날짜창 기준시각(주입하면 결정적; 생략 시 현재 KST)."""
    prior = store.report_for(client_order_id)
    if prior is not None:
        return prior
    fingerprint = store.fingerprint_for(client_order_id)
    if fingerprint is None:
        raise KISUsageError(
            f"모르는 client_order_id: {client_order_id!r} (이 계좌로 전송한 적이 없다)."
        )
    today = (now or datetime.now(_KST)).date()
    start = f"{today - timedelta(days=_RECONCILE_LOOKBACK_DAYS):%Y%m%d}"
    end = f"{today + timedelta(days=_RECONCILE_FORWARD_DAYS):%Y%m%d}"
    try:
        rows = _walk(transport, cano, product_code, start, end)
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"해외 예약주문 재조회 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
            f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
            client_order_id=client_order_id,
        ) from err
    matches = _filter_matching(rows, fingerprint, today)
    if len(matches) > 1:
        raise KISError(
            f"해외 예약주문 {client_order_id} 의 지문과 일치하는 예약이 {len(matches)}건이라 "
            f"자동 확정 불가하다. 수동 확인이 필요하다."
        )
    if not matches:
        return None
    reserved_id = str(matches[0].get("ovrs_rsvn_odno") or "").strip()
    if not reserved_id:  # 예약번호 없는 행으론 취소가 불가 -> 확정하지 않음
        return None
    report = _make_report(client_order_id, reserved_id, fingerprint.symbol, fingerprint.side, matches[0])
    store.record(report, fingerprint)
    return report


# --- 미국 해외예약 취소 (뮤테이션) ----------------------------------------
def cancel_overseas_reserved_order(
    transport: Transport, *, reserved_order_id: str, receipt_date: str,
    cano: str, product_code: str, environment: Environment,
) -> None:
    """미국 해외예약주문을 취소한다 -- ``reserved_order_id`` 는 발주가 돌려준 예약주문번호(:attr:`
    ExecutionReport.order_id`), ``receipt_date``(YYYYMMDD)는 그 예약의 접수일자(예약주문조회의
    ``receipt_date``, 방금 발주분은 발주일). rt_cd 정상이면 조용히 반환(취소 응답엔 리포트로 만들
    값이 없다), 아니면 예외.

    ``receipt_date`` 는 KIS 명세상 **필수**다(해외 취소는 접수일자로 대상을 특정) -- 국내 ``order_date`` 가
    optional 인 것과 다르다. 취소는 예약번호 대상의 멱등 연산(이미 취소/처리면 브로커가 거부)이라 즉시
    주문 dedup 스토어를 거치지 않되, 타임아웃(처리 불명)엔 재전송하지 않는다. **모의투자 미지원**. 실패는
    :class:`KISUsageError`(demo/빈 값/잘못된 날짜)·:class:`OrderRejectedError`(rt_cd!=0)·:class:`OrderTimeoutError`
    (타임아웃)·:class:`KISError`(정상 응답인데 취소 확인번호 부재/불일치)."""
    if environment == "demo":
        raise KISUsageError("해외 예약주문 취소(order-resv-ccnl)는 모의투자 미지원 -- 실전에서만.")
    if not str(reserved_order_id).strip():
        raise KISUsageError("취소할 해외예약주문번호(reserved_order_id)가 필요하다.")
    if not str(receipt_date).strip():
        raise KISUsageError("취소에는 예약 접수일자(receipt_date, YYYYMMDD)가 필요하다.")
    validate_yyyymmdd(receipt_date, "receipt_date")
    body = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "RSVN_ORD_RCIT_DT": receipt_date,
        "OVRS_RSVN_ODNO": str(reserved_order_id).strip(),
    }
    try:
        resp = transport.request(
            method="POST", path=_CANCEL_PATH, tr_id=_CANCEL_TR, body=body, idempotent=False
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"해외 예약주문 취소 요청 시간초과 -- 처리 여부가 불명이다. 재전송하지 말고 "
            f"예약주문조회로 상태를 확인하라(예약번호 {reserved_order_id}).",
            client_order_id=str(reserved_order_id),   # 이 흐름엔 client_order_id 가 없어 예약번호를 넣는다
        ) from err
    if not resp.ok:
        raise OrderRejectedError(
            f"해외 예약주문 취소 요청 거부: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    # 취소 응답의 유일한 업무 필드는 에코된 OVRS_RSVN_ODNO -- rt_cd=0 이어도 이 번호가 요청과 다르거나
    # 비어 있으면 실제 취소가 안 된 것으로 보고 fail-closed(국내의 nrml_prcs_yn 확인에 대응).
    echoed = _extract_ovrs_rsvn_odno(resp.body)
    if echoed != str(reserved_order_id).strip():
        raise KISError(
            f"해외 예약주문 취소 응답의 확인번호가 요청과 일치하지 않는다(응답 {echoed!r} != 요청 "
            f"{str(reserved_order_id).strip()!r}) -- 예약주문조회로 상태를 확인하라.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )


def _filter_matching(
    rows: list[Mapping[str, Any]], fingerprint: Fingerprint, today: date
) -> list[Mapping[str, Any]]:
    """예약주문조회 행 중 요청 지문과 맞는 것만(순수). 종목+매매구분+수량+단가에 더해 **접수일자**로
    좁힌다 -- 해외예약 지문엔 end_date 등 추가 판별자가 없어(국내와 달리) 같은 종목/수량/가격의 옛
    pending 예약이 넓은 창에서 가짜 단일매칭될 수 있다. 방금 발주된 예약은 오늘(±1, KST/US 시차)
    접수되므로 접수일자를 [today-1, today] 로 제한해 충돌창을 좁힌다. 예약번호 없는/취소된 행 제외."""
    quantity = Decimal(fingerprint.quantity)
    limit_price = Decimal(fingerprint.limit_price) if fingerprint.limit_price else None
    want_side = _SIDE_CODE[fingerprint.side]
    recent = {today, today - timedelta(days=1)}
    matched = []
    for row in rows:
        if not str(row.get("ovrs_rsvn_odno", "")).strip():  # 예약번호 없는 행 -- 매칭 불가
            continue
        if str(row.get("cncl_yn", "")).strip().upper() == "Y":  # 취소된 예약은 제외
            continue
        receipt = _parse_date(row.get("rsvn_ord_rcit_dt"))
        if receipt is None or receipt not in recent:  # 방금 발주(오늘±1 접수)만 -- 옛 동일지문 배제
            continue
        if str(row.get("pdno", "")).strip() != fingerprint.symbol:
            continue
        if str(row.get("sll_buy_dvsn_cd", "")).strip() != want_side:
            continue
        if _parse_response_decimal(row.get("ft_ord_qty")) != quantity:
            continue
        if limit_price is not None:
            row_price = row.get("ft_ord_unpr3")
            if row_price in (None, "") or _parse_response_decimal(row_price) != limit_price:
                continue
        matched.append(row)
    return matched


def _make_report(
    client_order_id: str, reserved_id: str, symbol: str, side: Side, raw: Mapping[str, Any]
) -> ExecutionReport:
    return ExecutionReport(
        client_order_id=client_order_id,
        order_id=reserved_id,            # 해외예약주문번호(취소 시 OVRS_RSVN_ODNO)
        symbol=symbol,
        side=side,
        status=OrderStatus.PENDING_NEW,  # 접수됨·미집행
        filled_quantity=Decimal(0),
        average_price=None,
        submitted_at=datetime.now(_KST),
        _raw=raw,
    )


def _extract_reserved_id(body: Mapping[str, Any]) -> str:
    """발주 응답에서 예약주문번호를 뽑는다. KIS 명세상 미국 예약발주(TTTT3014U/3016U) 응답의 output 은
    ``ODNO`` 하나뿐이며 이 값이 취소 시 OVRS_RSVN_ODNO 로 쓰인다. 다건이면 특정 불가라 빈 문자열."""
    out = body.get("output")
    if isinstance(out, list):
        if len(out) != 1:
            return ""
        out = out[0]
    if isinstance(out, Mapping):
        return str(out.get("ODNO") or "").strip()
    return ""


def _extract_ovrs_rsvn_odno(body: Mapping[str, Any]) -> str:
    """취소 응답의 에코된 해외예약주문번호(output.OVRS_RSVN_ODNO). 부재/비객체면 빈 문자열."""
    out = body.get("output")
    if isinstance(out, list):
        out = out[0] if out else {}
    if isinstance(out, Mapping):
        return str(out.get("OVRS_RSVN_ODNO") or "").strip()
    return ""


def _parse(row: Mapping[str, Any]) -> OverseasReservedOrder:
    return OverseasReservedOrder(
        reserved_order_id=str(row.get("ovrs_rsvn_odno", "")).strip(),
        receipt_date=_parse_date(row.get("rsvn_ord_rcit_dt")),
        order_date=_parse_date(row.get("ord_dt")),
        executed_order_id=str(row.get("odno", "")).strip(),
        symbol=str(row.get("pdno", "")).strip(),
        name=str(row.get("prdt_name", "")).strip(),
        side=_SIDE.get(str(row.get("sll_buy_dvsn_cd", "")).strip(), ""),
        status=str(row.get("ovrs_rsvn_ord_stat_cd_name", "")).strip(),
        exchange=str(row.get("ovrs_excg_cd", "")).strip(),
        quantity=_decimal_or_zero(row, "ft_ord_qty"),
        price=_decimal_or_zero(row, "ft_ord_unpr3"),
        filled_quantity=_decimal_or_zero(row, "ft_ccld_qty"),
        canceled=str(row.get("cncl_yn", "")).strip().upper() == "Y",
        unprocessed_reason=str(row.get("nprc_rson_text", "")).strip(),
        _raw=row,
    )


def _decimal_or_zero(row: Mapping[str, Any], key: str) -> Decimal:
    amount = optional_decimal(row.get(key), key)
    return Decimal(0) if amount is None else amount


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()  # noqa: DTZ007
    except ValueError:
        return None
