"""국내주식 예약주문 엔진 (내부) -- 조회/발주/정정취소.

예약주문은 다음 영업일(또는 지정 기간) 아침 동시호가에 집행되는 예약이라 즉시체결 주문
(:mod:`kis_trader._domestic.orders`)과 라이프사이클이 다르다(예약순번 rsvn_ord_seq 로 식별,
집행 전 체결 없음). 그래서 즉시주문 안전코어(place/reconcile)를 재사용하지 않고 이 모듈이
예약 전용 흐름을 담는다. 발주/정정취소(뮤테이션)는 별도 슬라이스에서 dedup·무재시도로 추가한다.

KIS URL/TR-ID (전부 모의투자 미지원):
- 조회: ``GET .../trading/order-resv-ccnl`` (``CTSC0004R``).
- 발주: ``POST .../trading/order-resv`` (``CTSC0008U``).
- 정정취소: ``POST .../trading/order-resv-rvsecncl`` (취소 ``CTSC0009U`` / 정정 ``CTSC0013U``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from ..._internal._wire import decimal_or_zero, format_wire_decimal, optional_decimal
from ...errors import (
    AccountNotOrderableError,
    KISError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
)
from ...order import (
    OrderType,
    ReservedOrderFingerprint,
    Side,
    coerce_decimal,
    validate_yyyymmdd,
)
from ...report import ExecutionReport, OrderStatus
from ...reserved_order import ReservedOrder
from ...store import Claimed, Completed, Conflict, InFlight, OrderStore
from ...transport import Environment, Transport, TransportTimeout
from ._parse import _side_from_code

if TYPE_CHECKING:
    from ..._literals import Numeric

_KST = timezone(timedelta(hours=9))

_INQUIRE_PATH = "/uapi/domestic-stock/v1/trading/order-resv-ccnl"
_INQUIRE_TR = "CTSC0004R"          # 모의투자 미지원
_PLACE_PATH = "/uapi/domestic-stock/v1/trading/order-resv"
_PLACE_TR = "CTSC0008U"            # 모의투자 미지원
_CHANGE_PATH = "/uapi/domestic-stock/v1/trading/order-resv-rvsecncl"
_CANCEL_TR = "CTSC0009U"           # 예약취소, 모의투자 미지원
_MODIFY_TR = "CTSC0013U"           # 예약정정, 모의투자 미지원
#: 예약주문 조회 연속조회 페이지 상한. 닿으면 fail-closed.
_MAX_PAGES = 100
_SIDE_CODE = {"buy": "02", "sell": "01"}
#: 우리 주문타입 -> ORD_DVSN_CD(예약). 00 지정가 / 01 시장가.
_ORD_DVSN_CD = {"limit": "00", "market": "01"}
#: 처리구분 -> PRCS_DVSN_CD. all:전체/processed:처리내역/unprocessed:미처리내역.
_PROCESS_FILTER = {"all": "0", "processed": "1", "unprocessed": "2"}
#: 예약주문 지문의 exchange 네임스페이스 -- 즉시주문(XKRX)/해외와 분리해 dedup 충돌을 막고
#: reconcile 을 예약 경로로 라우팅한다(즉시주문의 `action:` 접두 선례와 같은 방식).
_RESERVED_EXCHANGE = "reserved"
#: 현금 주문대상잔고구분코드(신용/대여 예약은 미지원 -- 현금만).
_CASH_BALANCE_DIVISION = "10"
#: reconcile 시 예약주문조회 날짜창. KIS 명세상 RSVN_ORD_ORD_DT 가 예약 '발주일'인지 '집행 예정일'인지
#: 모호해(요청예시 ctx 는 발주일에 가깝고, 응답 rsvn_ord_ord_dt 는 집행일에 가깝다) 양방향으로 스캔한다
#: -- 어느 쪽이든 최근 예약을 포함하도록. 실 API 왕복으로 필드 의미를 확정하면 창을 좁힌다.
_RECONCILE_LOOKBACK_DAYS = 7
_RECONCILE_FORWARD_DAYS = 31


class _ReservedTerms(NamedTuple):
    """예약 발주/정정이 공유하는 정규화된 주문 항목 -- 방향·수량·주문구분·단가를 한 번의 검증으로 확정한다."""

    order_type: str            # "limit"(지정가 00) / "market"(시장가 01)
    quantity: Decimal
    limit_price: Decimal | None


def _coerce_reserved_order_terms(
    *, side: Side, quantity: Numeric, limit_price: Numeric | None, end_date: str | None
) -> _ReservedTerms:
    """예약 발주·정정이 공유하는 항목 검증/정규화(순수) -- 방향, 수량(양의 정수), 단가(있으면 유한·양수),
    종료일(있으면 실재 YYYYMMDD)을 **한 경로**로 확정한다. 발주와 정정이 각자 검증하다 종료일 검증이
    어긋나던 것을 하나로 모은다. ``limit_price`` 없으면 시장가(01), 있으면 지정가(00)."""
    if side not in _SIDE_CODE:
        raise KISUsageError(f"side 는 buy/sell 이어야 한다: {side!r}")
    qty = coerce_decimal(quantity, "quantity")
    if qty <= 0 or qty != qty.to_integral_value():
        raise KISUsageError(f"예약주문 수량은 0보다 큰 정수(주)여야 한다: {qty}")
    order_type = "limit" if limit_price is not None else "market"
    limit = None
    if limit_price is not None:
        limit = coerce_decimal(limit_price, "limit_price")
        if not limit.is_finite() or limit <= 0:
            raise KISUsageError(f"limit_price 는 0보다 큰 유한값이어야 한다: {limit_price!r}")
    if end_date is not None:
        validate_yyyymmdd(end_date, "end_date")  # 실재 달력 날짜(신용 loan_date 와 동일 강도)
    return _ReservedTerms(order_type=order_type, quantity=qty, limit_price=limit)


def _make_reserved_order_fields(
    *, cano: str, product_code: str, symbol: str, side: Side, terms: _ReservedTerms,
    end_date: str | None,
) -> dict[str, str]:
    """예약 발주·정정 와이어 바디의 **공통** 필드(순수). 정정은 여기에 대상 지정 키(순번/조직번호/
    주문일자/연락처)를 삽입 순서대로 덧붙인다 -- 두 경로의 공통 부분이 바이트 동일하게 나간다."""
    return {
        "CANO": cano, "ACNT_PRDT_CD": product_code, "PDNO": symbol,
        "ORD_QTY": format_wire_decimal(terms.quantity),
        "ORD_UNPR": "0" if terms.limit_price is None else format_wire_decimal(terms.limit_price),
        "SLL_BUY_DVSN_CD": _SIDE_CODE[side],
        "ORD_DVSN_CD": _ORD_DVSN_CD[terms.order_type],
        "ORD_OBJT_CBLC_DVSN_CD": _CASH_BALANCE_DIVISION,
        "LOAN_DT": "", "RSVN_ORD_END_DT": end_date or "",
    }


def fetch_reserved_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str, process: str = "all",
) -> list[ReservedOrder]:
    """예약주문 조회(연속조회 소진까지). ``start``/``end`` 는 예약주문일자 기간(YYYYMMDD),
    ``process`` = all/processed/unprocessed. 유효(취소 안 된) 예약만 준다. **모의투자 미지원**."""
    if environment == "paper":
        raise KISUsageError("예약주문조회(order-resv-ccnl)는 모의투자 미지원 -- 실전에서만.")
    try:
        process_code = _PROCESS_FILTER[process]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 process: {process!r} ({'/'.join(_PROCESS_FILTER)})."
        ) from None
    validate_yyyymmdd(start, "start")   # 조회 기간은 실재하는 YYYYMMDD 8자리여야 한다(발주 날짜와 동일 강도)
    validate_yyyymmdd(end, "end")
    rows = _walk_reserved(
        transport, cano=cano, product_code=product_code, start=start, end=end, process_code=process_code
    )
    return [_parse_reserved(row) for row in rows if str(row.get("rsvn_ord_seq", "")).strip()]


def _walk_reserved(
    transport: Transport, *, cano: str, product_code: str, start: str, end: str, process_code: str
) -> list[Mapping[str, Any]]:
    """예약주문조회를 연속조회 소진까지 읽어 원본 행을 돌려준다(순수 I/O)."""
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        params = {
            "RSVN_ORD_ORD_DT": start, "RSVN_ORD_END_DT": end, "RSVN_ORD_SEQ": "",
            "TMNL_MDIA_KIND_CD": "00", "CANO": cano, "ACNT_PRDT_CD": product_code,
            "PRCS_DVSN_CD": process_code, "CNCL_YN": "Y", "PDNO": "", "SLL_BUY_DVSN_CD": "",
            "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_INQUIRE_PATH, tr_id=_INQUIRE_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        if not resp.ok:
            raise KISError(
                f"예약주문조회 실패: {resp.msg1}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        page = resp.body.get("output")
        if not isinstance(page, list):  # 빈 내역도 배열 -> 부재/비배열은 손상
            raise KISError(
                "예약주문조회 응답의 output 이 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        # 재조회는 조기 종료 금지(예약 누락->오확정->이중발주 위험): tr_cont 정본 종료(D/E/공백)
        # 이면서 연속조회 커서도 소진됐을 때만 마지막 페이지로 확정한다(둘 중 하나라도 남으면 계속
        # 스캔). 즉시/해외 재조회(_fetch_daily_orders/_fetch_ccnl)와 같은 보수적 종료.
        if resp.tr_cont not in ("F", "M") and not ctx_nk:
            break
        tr_cont = "N"
    else:
        raise KISError(
            f"예약주문조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다."
        )
    return rows


# --- 예약주문 발주 (뮤테이션, 안전 흐름) -----------------------------------
def place_reserved_order(
    transport: Transport, store: OrderStore, *,
    symbol: str, side: Side, quantity: Numeric, limit_price: Numeric | None = None,
    end_date: str | None = None, client_order_id: str, orderable: bool = True,
    cano: str, product_code: str, environment: Environment,
) -> ExecutionReport:
    """현금 예약주문을 안전 규칙으로 발주한다 -- 즉시주문(place)과 같은 이중발주 방지·재시도 금지·
    보수적 재조회를, 예약 라이프사이클(rsvn_ord_seq·체결개념 없음)에 맞춰 구현한다.

    ``limit_price`` 를 주면 지정가(00), 없으면 시장가(01). ``end_date``(YYYYMMDD, 실재 날짜)는 예약 유효
    종료일(현재 이후 여부는 브로커가 검증, 생략 시 브로커 기본). 반환 :class:`ExecutionReport` 의
    ``order_id`` 는 예약주문순번(rsvn_ord_seq), ``status`` 는 :attr:`OrderStatus.PENDING_NEW`(접수됨·
    미집행). **모의투자 미지원**, 현금 예약만(신용/대여 예약 미지원).

    조회전용 계좌(``orderable=False``, 퇴직연금 등)면 :class:`AccountNotOrderableError`, 잘못된 인자/계좌
    미설정은 :class:`KISUsageError`, 접수 거부는 :class:`OrderRejectedError`, 접수 불명(타임아웃)은
    :class:`OrderTimeoutError`(:func:`reconcile_reserved_order` 로 확인), 순번 부재/예상밖 상태는
    :class:`OrderError`.
    """
    if not orderable:
        raise AccountNotOrderableError(
            "이 계좌는 API 주문이 불가하다(퇴직연금 IRP/DC 등 조회전용). 일반/연금저축 계좌를 쓰라."
        )
    if environment == "paper":
        raise KISUsageError("예약주문(order-resv)은 모의투자 미지원 -- 실전에서만.")
    terms = _coerce_reserved_order_terms(
        side=side, quantity=quantity, limit_price=limit_price, end_date=end_date
    )

    # end_date 는 예약의 정체성 일부(유효 종료일이 다르면 다른 주문)라 지문에 정직한 필드로 담는다
    # (exchange="reserved" 네임스페이스가 즉시주문과 분리한다).
    fingerprint = ReservedOrderFingerprint(
        symbol=symbol, side=side, order_type=cast(OrderType, terms.order_type),
        quantity=format_wire_decimal(terms.quantity),
        limit_price="" if terms.limit_price is None else format_wire_decimal(terms.limit_price),
        end_date=end_date or "", exchange=_RESERVED_EXCHANGE,
    )
    body = _make_reserved_order_fields(
        cano=cano, product_code=product_code, symbol=symbol, side=side, terms=terms, end_date=end_date,
    )

    claim = store.try_claim(client_order_id, fingerprint)
    if isinstance(claim, Completed):
        return claim.report
    if isinstance(claim, Conflict):
        raise KISUsageError(
            f"client_order_id {client_order_id!r} 는 이미 다른 주문에 사용됐다. 새 id를 발행하라."
        )
    if isinstance(claim, InFlight):
        raise KISUsageError(
            f"예약주문 {client_order_id} 은 전송됐으나 결과가 확인되지 않았다. "
            f"kis.orders.reconcile({client_order_id!r}) 로 재조회한 뒤 판단하라."
        )
    if not isinstance(claim, Claimed):
        raise OrderError(f"예상치 못한 claim 결과: {claim!r}")

    try:
        resp = transport.request(
            method="POST", path=_PLACE_PATH, tr_id=_PLACE_TR, body=body, idempotent=False
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"예약주문 전송 시간초과 -- 접수 여부가 불명이다. 재전송하지 말고 "
            f"kis.orders.reconcile({client_order_id!r}) 로 재조회하라.",
            client_order_id=client_order_id,
        ) from err

    if not resp.ok:
        store.clear_in_flight(client_order_id)
        raise OrderRejectedError(
            f"예약주문 접수 거부: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    sequence = _extract_sequence(resp.body)
    if not sequence:
        raise OrderError(
            "예약주문 접수 응답(rt_cd=0)에 예약주문순번(rsvn_ord_seq)이 없다 -- 재조회 불가.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    report = _make_reserved_order_report(
        client_order_id, sequence=sequence, symbol=symbol, side=side, raw=resp.body
    )
    store.record(report, fingerprint)
    return report


def reconcile_reserved_order(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment,
) -> ExecutionReport | None:
    """미확인 예약주문을 **예약주문조회**로 재조회한다 -- 보수적. 완료 리포트가 있으면 반환.
    in-flight 면 최근 창의 (처리/미처리 모두) 유효 예약을 지문으로 매칭: 정확히 1건이면 확정, 0건이면
    미접수로 단정하지 않고 ``None``(재전송 금지 유지), 2건 이상이면 :class:`KISError`."""
    prior = store.report_for(client_order_id)
    if prior is not None:
        return prior
    fingerprint = store.fingerprint_for(client_order_id)
    if fingerprint is None:
        raise KISUsageError(
            f"모르는 client_order_id: {client_order_id!r} (이 계좌로 전송한 적이 없다)."
        )
    fingerprint = cast(ReservedOrderFingerprint, fingerprint)  # 예약주문 reconcile 경로
    today = datetime.now(_KST).date()
    start = f"{today - timedelta(days=_RECONCILE_LOOKBACK_DAYS):%Y%m%d}"
    end = f"{today + timedelta(days=_RECONCILE_FORWARD_DAYS):%Y%m%d}"
    try:
        # 처리/미처리 모두(all) -- 집행 완료된 예약도 '접수됐음' 은 확정할 수 있어야 한다(취소 제외는
        # fetch 의 CNCL_YN="Y" 가 이미 처리).
        rows = _walk_reserved(
            transport, cano=cano, product_code=product_code, start=start, end=end,
            process_code=_PROCESS_FILTER["all"],
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"예약주문 재조회 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
            f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
            client_order_id=client_order_id,
        ) from err
    matches = _filter_matching_reserved(rows, fingerprint)
    if len(matches) > 1:
        raise KISError(
            f"예약주문 {client_order_id} 의 지문과 일치하는 예약이 {len(matches)}건이라 "
            f"자동 확정 불가하다. 수동 확인이 필요하다."
        )
    if not matches:  # 미접수인지 반영 지연/창 밖인지 단정 불가 -> in-flight 유지
        return None
    sequence = str(matches[0].get("rsvn_ord_seq") or "").strip()
    if not sequence:  # 순번 없는 행으론 정정·취소가 불가 -> 확정하지 않고 in-flight 유지
        return None
    report = _make_reserved_order_report(
        client_order_id, sequence=sequence, symbol=fingerprint.symbol, side=fingerprint.side,
        raw=matches[0],
    )
    store.record(report, fingerprint)
    return report


# --- 예약주문 정정/취소 (뮤테이션) ----------------------------------------
def cancel_reserved_order(
    transport: Transport, *, sequence: str, order_date: str | None = None,
    cano: str, product_code: str, environment: Environment,
) -> None:
    """예약주문을 취소한다 -- ``sequence`` 는 예약주문순번(:attr:`ExecutionReport.order_id`). 정상
    처리(nrml_prcs_yn=Y)면 조용히 반환(정정취소 응답엔 리포트로 만들 값이 없어 반환값이 없다).

    취소는 순번 대상의 멱등적 연산(이미 취소/처리면 브로커가 거부)이라 즉시주문 dedup 스토어를 거치지
    않되, 타임아웃(처리 불명)엔 재전송하지 않는다. ``order_date``(YYYYMMDD)는 선택이며(KIS 명세상 순번만
    필수) 같은 순번이 여러 날에 재사용될 때 대상을 좁히는 용도다. **모의투자 미지원**. 실패는
    :class:`KISUsageError`(demo/빈 순번/잘못된 order_date)·:class:`OrderRejectedError`(rt_cd!=0)·
    :class:`KISError`(정상처리 아님)·:class:`OrderTimeoutError`(타임아웃)."""
    if environment == "paper":
        raise KISUsageError("예약주문 취소(order-resv-rvsecncl)는 모의투자 미지원 -- 실전에서만.")
    if not str(sequence).strip():
        raise KISUsageError("취소할 예약주문순번(sequence)이 필요하다.")
    if order_date is not None:
        validate_yyyymmdd(order_date, "order_date")
    body = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "RSVN_ORD_SEQ": str(sequence).strip(),
        "RSVN_ORD_ORGNO": "", "RSVN_ORD_ORD_DT": order_date or "",
    }
    _send_change(transport, _CANCEL_TR, body, sequence, action="취소")


def modify_reserved_order(
    transport: Transport, *, sequence: str, symbol: str, side: Side, quantity: Numeric,
    limit_price: Numeric | None = None, end_date: str | None = None, order_date: str | None = None,
    cano: str, product_code: str, environment: Environment,
) -> None:
    """예약주문을 정정한다 -- 브로커 규격상 **전체 재지정**(종목/방향/수량/단가/종료일)을 요구한다.
    ``sequence`` 로 대상을 지목한다. 정상 처리면 조용히 반환(응답에 새 순번/리포트가 없다), 아니면 예외.
    **모의투자 미지원**, 국내 현금 예약만.

    주의: ``limit_price`` 를 생략하면 **시장가**가 된다(기존 단가 유지가 아니라 시장가 전환). 정정 후
    브로커가 순번을 바꿀 수 있으므로(응답은 새 순번을 주지 않는다) 이후 정정·취소가 필요하면
    :func:`fetch_reserved_orders` 로 현재 순번을 재확인하라. 정정도 순번 대상의 절대 재지정이라 dedup
    스토어를 거치지 않되 타임아웃엔 재전송하지 않는다. 실패 예외는 :func:`cancel_reserved_order` 와 같고,
    인자 검증(side/quantity/limit_price/end_date)은 :class:`KISUsageError`."""
    if environment == "paper":
        raise KISUsageError("예약주문 정정(order-resv-rvsecncl)은 모의투자 미지원 -- 실전에서만.")
    if not str(sequence).strip():
        raise KISUsageError("정정할 예약주문순번(sequence)이 필요하다.")
    terms = _coerce_reserved_order_terms(
        side=side, quantity=quantity, limit_price=limit_price, end_date=end_date
    )
    if order_date is not None:
        validate_yyyymmdd(order_date, "order_date")
    body = _make_reserved_order_fields(
        cano=cano, product_code=product_code, symbol=symbol, side=side, terms=terms, end_date=end_date,
    )
    # 정정은 대상 예약을 지목하는 키(순번/조직번호/주문일자)와 연락처를 공통 바디 뒤에 삽입 순서대로 덧붙인다.
    body.update({
        "CTAC_TLNO": "",
        "RSVN_ORD_SEQ": str(sequence).strip(),
        "RSVN_ORD_ORGNO": "", "RSVN_ORD_ORD_DT": order_date or "",
    })
    _send_change(transport, _MODIFY_TR, body, sequence, action="정정")


def _send_change(
    transport: Transport, tr_id: str, body: Mapping[str, Any], sequence: str, *, action: str
) -> None:
    """예약 정정/취소 와이어 전송 -- 무재시도. 정상처리(nrml_prcs_yn=Y)면 반환, 아니면 예외."""
    try:
        resp = transport.request(
            method="POST", path=_CHANGE_PATH, tr_id=tr_id, body=dict(body), idempotent=False
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"예약주문 {action} 요청 시간초과 -- 처리 여부가 불명이다. 재전송하지 말고 예약주문조회로 "
            f"상태를 확인하라(예약순번 {sequence}).",
            client_order_id=str(sequence),   # 이 흐름엔 client_order_id 가 없어 예약순번을 넣는다
        ) from err
    if not resp.ok:
        raise OrderRejectedError(
            f"예약주문 {action} 요청 거부: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    # nrml_prcs_yn 위치가 KIS 응답예시로 확정되지 않아(layout=output 하위) output 과 본문 최상위를
    # 모두 본다 -- 레이아웃과 응답예시가 어긋날 때를 헤지.
    output = resp.body.get("output")
    if isinstance(output, list):
        # 정정/취소 응답의 output 은 단일 확인 건이다. 다건이면(예상밖) 어느 행이 이 요청의 결과인지
        # 특정할 수 없어 output[0] 을 확인으로 읽으면 오확정 위험 -- fail-closed 로 수동 확인을 안내한다.
        if len(output) > 1:
            raise KISError(
                f"예약주문 {action} 응답의 output 이 다건({len(output)})이라 단일 확인으로 읽을 수 없다 "
                f"-- 순번 {sequence}. 예약주문조회로 상태를 확인하라.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        output = output[0] if output else {}
    normal = ""
    if isinstance(output, Mapping) and str(output.get("nrml_prcs_yn", "")).strip():
        normal = str(output.get("nrml_prcs_yn", "")).strip()
    elif str(resp.body.get("nrml_prcs_yn", "")).strip():
        normal = str(resp.body.get("nrml_prcs_yn", "")).strip()
    if normal.upper() != "Y":
        raise KISError(
            f"예약주문 {action} 가 정상 처리되지 않았다(nrml_prcs_yn={normal!r}) -- 순번 {sequence}. "
            f"예약주문조회로 상태를 확인하라.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )


def _filter_matching_reserved(
    rows: list[Mapping[str, Any]], fingerprint: ReservedOrderFingerprint
) -> list[Mapping[str, Any]]:
    """예약주문조회 행 중 요청 지문과 맞는 것만(순수). 종목+매매구분+주문구분+수량, 지정가면 단가,
    지문에 종료일이 있으면 예약종료일(rsvn_end_dt)까지 비교한다. 순번 없는 행은 매칭 불가라 제외."""
    quantity = Decimal(fingerprint.quantity)
    limit_price = Decimal(fingerprint.limit_price) if fingerprint.limit_price else None
    want_side = _SIDE_CODE[fingerprint.side]
    want_dvsn = _ORD_DVSN_CD.get(fingerprint.order_type)
    want_end_date = fingerprint.end_date   # 예약 지문의 유효 종료일(없으면 "")
    matched = []
    for row in rows:
        if not str(row.get("rsvn_ord_seq", "")).strip():  # 순번 없는 패딩 행 -- 매칭 불가
            continue
        if str(row.get("pdno", "")).strip() != fingerprint.symbol:
            continue
        if str(row.get("sll_buy_dvsn_cd", "")).strip() != want_side:
            continue
        if want_dvsn is not None and str(row.get("ord_dvsn_cd", "")).strip() not in ("", want_dvsn):
            continue
        if decimal_or_zero(row.get("ord_rsvn_qty")) != quantity:
            continue
        if limit_price is not None:
            row_price = row.get("ord_rsvn_unpr")
            if row_price in (None, "") or decimal_or_zero(row_price) != limit_price:
                continue
        # 종료일을 명시했으면 그 예약만(브로커 기본으로 채워진 다른 종료일 예약과 구분). 미명시("")면
        # 브로커 기본값을 예측할 수 없으므로 종료일로 좁히지 않는다.
        if want_end_date and str(row.get("rsvn_end_dt", "")).strip() != want_end_date:
            continue
        matched.append(row)
    return matched


def _make_reserved_order_report(
    client_order_id: str, *, sequence: str, symbol: str, side: Side, raw: Mapping[str, Any]
) -> ExecutionReport:
    return ExecutionReport(
        client_order_id=client_order_id,
        order_id=sequence,               # 예약주문순번(rsvn_ord_seq) -- 정정·취소 지목용
        symbol=symbol,
        side=side,
        status=OrderStatus.PENDING_NEW,  # 접수됨·미집행(향후 동시호가 집행)
        filled_quantity=Decimal(0),
        average_price=None,
        recorded_at=datetime.now(_KST),
        _raw=raw,
    )


def _extract_sequence(body: Mapping[str, Any]) -> str:
    """발주 응답에서 예약주문순번을 뽑는다. KIS 명세상 output 은 단일 건 -- 다건이면(예상밖) 특정 불가라
    빈 문자열로 취급해 발주 경로의 '순번 없음 -> in-flight 유지·raise' 로 흘린다(임의 귀속 금지)."""
    out = body.get("output")
    if isinstance(out, list):
        if len(out) != 1:
            return ""
        out = out[0]
    if isinstance(out, Mapping):
        return str(out.get("rsvn_ord_seq") or "").strip()
    return ""


def _parse_reserved(row: Mapping[str, Any]) -> ReservedOrder:
    return ReservedOrder(
        sequence=str(row.get("rsvn_ord_seq", "")).strip(),
        order_date=_parse_date(row.get("rsvn_ord_ord_dt")),
        received_date=_parse_date(row.get("rsvn_ord_rcit_dt")),
        symbol=str(row.get("pdno", "")).strip(),
        name=str(row.get("kor_item_shtn_name", "")).strip(),
        side=_side_from_code(row.get("sll_buy_dvsn_cd")),
        order_type_name=str(row.get("ord_dvsn_name", "")).strip(),
        reserved_quantity=_decimal_or_zero(row, "ord_rsvn_qty"),
        filled_quantity=_decimal_or_zero(row, "tot_ccld_qty"),
        order_price=_decimal_or_zero(row, "ord_rsvn_unpr"),
        status=str(row.get("prcs_rslt", "")).strip(),
        reject_reason=str(row.get("rjct_rson2", "")).strip(),
        executed_order_id=str(row.get("odno", "")).strip(),
        reservation_end_date=_parse_date(row.get("rsvn_end_dt")),
        _raw=row,
    )


def _decimal_or_zero(row: Mapping[str, Any], key: str) -> Decimal:
    amount = optional_decimal(row.get(key), key)
    return Decimal(0) if amount is None else amount


def _parse_date(value: object) -> date | None:
    """``"20220523"`` -> ``date(2022, 5, 23)``. 공백/형식오류면 None(fail-soft)."""
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()  # noqa: DTZ007
    except ValueError:
        return None
