"""해외주식 예약주문 엔진 (내부) -- 미국(NAS/NYS/AMS) + 아시아(홍콩/상해/심천/일본/베트남).

해외 예약주문은 정규장 시작 전에 걸어두는 예약으로, 해외예약주문번호(ovrs_rsvn_odno)로 식별한다.
미국은 발주/취소 TR 이 분리돼 있고(매수 TTTT3014U/매도 TTTT3016U, 취소 TTTT3017U), 아시아는
매수·매도·취소가 발주 TR 하나(TTTS3013U, ``RVSE_CNCL_DVSN_CD`` 00 발주/02 취소)를 공유한다.
발주/취소는 모의(V*) TR 이 있지만 예약주문조회(order-resv-list)는 **실전전용**이라 목록·재조회는
모의(paper)에서 fail-closed 한다.

KIS URL/TR-ID:
- 발주(+아시아 취소): ``POST .../trading/order-resv`` (미국 ``TTTT3014U``/``TTTT3016U``,
  아시아 공용 ``TTTS3013U``; 모의 ``VTTT3014U``/``VTTT3016U``/``VTTS3013U``).
- 미국 취소: ``POST .../trading/order-resv-ccnl`` (``TTTT3017U``/``VTTT3017U``).
- 조회: ``GET .../trading/order-resv-list`` (미국 ``TTTT3039R``, 아시아 ``TTTS3014R``; 실전전용).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from ..._internal._wire import decimal_or_zero, format_wire_decimal, optional_decimal
from ...errors import (
    AccountNotOrderableError,
    KISError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
)
from ...order import ReservedOrderFingerprint, Side, coerce_decimal, validate_yyyymmdd
from ...report import ExecutionReport, OrderStatus
from ...store import Claimed, Completed, Conflict, InFlight, OrderStore
from ...transport import Environment, Transport, TransportTimeout
from ..entities.orders import OverseasReservedOrder
from ._parse import _side_from_code
from .orders import _ORDER_EXCHANGE

if TYPE_CHECKING:
    from ..._literals import Numeric

_KST = timezone(timedelta(hours=9))

_LIST_PATH = "/uapi/overseas-stock/v1/trading/order-resv-list"
#: 시장 -> 예약주문조회 TR. 둘 다 모의투자 미지원(실전전용).
_LIST_TR = {"US": "TTTT3039R", "ASIA": "TTTS3014R"}
_PLACE_PATH = "/uapi/overseas-stock/v1/trading/order-resv"
#: 시장 -> 매매구분 -> 환경 -> 발주 TR. 미국은 매수/매도 TR 이 분리, 아시아는 매수·매도(·취소)가
#: 하나("any")를 공용한다. 발주는 모의(V*) 지원 -- 실전전용은 조회(:data:`_LIST_TR`)뿐이다.
_PLACE_TR = {
    "US":   {"buy":  {"real": "TTTT3014U", "paper": "VTTT3014U"},
             "sell": {"real": "TTTT3016U", "paper": "VTTT3016U"}},
    "ASIA": {"any":  {"real": "TTTS3013U", "paper": "VTTS3013U"}},
}
_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-resv-ccnl"
#: 환경 -> 미국 예약취소 TR. 아시아는 전용 취소 엔드포인트가 없어(원장: "미제공") 발주 TR 로 취소한다.
_US_CANCEL_TR = {"real": "TTTT3017U", "paper": "VTTT3017U"}
#: 연속조회 페이지 상한. 닿으면 fail-closed.
_MAX_PAGES = 100
_SIDE_CODE = {"buy": "02", "sell": "01"}
_US_MARKET = "US"
#: 아시아 예약주문 대상 시장(`_ORDER_EXCHANGE` 의 시장 그룹 값) -- 홍콩/상해/심천/일본/베트남.
_ASIA_MARKETS = frozenset({"HK", "SH", "SZ", "JP", "VN"})
#: 아시아 거래소 -> PRDT_TYPE_CD(상품유형코드, 홍콩 제외 고정). 홍콩은 통화별(501/543/558)이라 별도.
_ASIA_PRDT_TYPE_CD = {"TSE": "515", "SHS": "551", "SZS": "552", "HNX": "507", "HSX": "508"}
#: 홍콩(HKS) 예약의 통화별 PRDT_TYPE_CD -- HKD 기본, CNY/USD 는 명시 override.
_HK_PRDT_TYPE_CD = {"HKD": "501", "CNY": "543", "USD": "558"}
_ORD_DVSN_LIMIT = "00"             # 지정가
#: 예약 지문의 exchange 네임스페이스 -- 즉시/주간/국내예약과 분리해 reconcile 을 해외예약 경로로 라우팅.
_RESERVED_EXCHANGE = "overseas-reserved"
#: 아시아 예약 지문의 exchange 네임스페이스 -- 미국과 분리해 취소/재조회를 아시아 경로(TTTS3013U/
#: TTTS3014R)로 라우팅한다.
_ASIA_RESERVED_EXCHANGE = "overseas-reserved-asia"
#: reconcile 창(양방향). KIS 명세상 예약 조회일자 필드 의미가 모호해 앞뒤로 스캔한다(실 API 검증 후 축소).
_RECONCILE_LOOKBACK_DAYS = 7
_RECONCILE_FORWARD_DAYS = 31


def _asia_prdt_type_cd(exchange: str, currency: str) -> str:
    """아시아 예약주문의 PRDT_TYPE_CD(상품유형코드)를 거래소코드에서 파생한다(순수). ``currency`` 는
    홍콩(HKS)에만 의미가 있다(501 HKD / 543 CNY / 558 USD) -- 그 외 거래소에 비-HKD 를 주면
    fail-closed(조용히 무시하면 의도한 통화와 다른 상품유형으로 발주된다)."""
    if exchange == "HKS":
        try:
            return _HK_PRDT_TYPE_CD[currency]
        except KeyError:
            raise KISUsageError(
                f"홍콩 예약주문의 통화는 HKD/CNY/USD 여야 한다: {currency!r}"
            ) from None
    if currency != "HKD":
        raise KISUsageError(
            f"통화 지정은 홍콩(HKS) 예약주문에만 유효하다: {exchange!r}, {currency!r}"
        )
    try:
        return _ASIA_PRDT_TYPE_CD[exchange]
    except KeyError:
        raise KISUsageError(f"아시아 예약주문을 지원하지 않는 거래소코드: {exchange!r}") from None


def fetch_reserved_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str, market: Literal["US", "ASIA"] = "US",
) -> list[OverseasReservedOrder]:
    """해외주식 예약주문 조회(연속조회 소진까지). ``start``/``end`` 는 기간(YYYYMMDD), ``market`` 은
    조회 TR 선택("US"=TTTT3039R / "ASIA"=TTTS3014R -- 필터 공백이라 그 시장 전체가 나온다).
    **모의투자 미지원**(두 TR 모두 실전전용)."""
    if environment == "paper":
        raise KISUsageError("해외 예약주문조회(order-resv-list)는 모의투자 미지원 -- 실전에서만.")
    validate_yyyymmdd(start, "start")   # 조회 기간은 실재하는 YYYYMMDD 8자리여야 한다
    validate_yyyymmdd(end, "end")
    rows = _walk(transport, cano, product_code, start, end, market=market)
    return [_parse(row) for row in rows if str(row.get("ovrs_rsvn_odno", "")).strip()]


def _walk(
    transport: Transport, cano: str, product_code: str, start: str, end: str, *,
    market: Literal["US", "ASIA"] = "US",
) -> list[Mapping[str, Any]]:
    """해외 예약주문 조회를 연속조회 소진까지 읽어 원본 행을 돌려준다(순수 I/O). ``market`` 이 조회
    TR 을 고른다 -- 미국/아시아가 같은 URL 을 TR 만 달리해 쓴다."""
    try:
        list_tr = _LIST_TR[market]
    except KeyError:
        raise KISUsageError(f"해외 예약주문 조회를 지원하지 않는 시장이다: {market!r}") from None
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "INQR_STRT_DT": start, "INQR_END_DT": end,
            "INQR_DVSN_CD": "00",                     # KIS 명세: 00 = 전체(집행/미집행 모두)
            "PRDT_TYPE_CD": "", "OVRS_EXCG_CD": "",   # 공백 = 그 시장(TR) 전체
            "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_LIST_PATH, tr_id=list_tr,
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


# --- 해외예약 발주 (뮤테이션, 안전 흐름) -----------------------------------
def place_overseas_reserved_order(
    transport: Transport, store: OrderStore, *,
    symbol: str, side: Side, quantity: Numeric, limit_price: Numeric, exchange: str,
    currency: str = "HKD",
    client_order_id: str, orderable: bool = True,
    cano: str, product_code: str, environment: Environment,
) -> ExecutionReport:
    """해외주식 예약주문을 안전 규칙으로 발주한다 -- 즉시주문(place)과 같은 이중발주 방지·재시도
    금지·보수적 재조회를, 예약 라이프사이클(예약번호·체결개념 없음)에 맞춰 구현한다. **지정가만**
    (``limit_price`` 필수). ``exchange`` 의 시장이 와이어를 가른다: 미국(NAS/NYS/AMS)은 매수/매도
    TR 분리 body(ORD_DVSN), 아시아(홍콩/상해/심천/일본/베트남)는 공용 TR(TTTS3013U) body
    (SLL_BUY_DVSN_CD + RVSE_CNCL_DVSN_CD=00 + 거래소에서 파생한 PRDT_TYPE_CD). ``currency`` 는
    홍콩(HKS) 예약의 상품유형 선택(HKD/CNY/USD)에만 쓰이며 그 외 거래소에 비-HKD 를 주면
    fail-closed. 발주는 모의(V* TR)를 지원한다.

    반환 :class:`ExecutionReport` 의 ``order_id`` 는 해외예약주문번호(미국 발주 Output ODNO = 취소 시
    OVRS_RSVN_ODNO / 아시아 OVRS_RSVN_ODNO), ``receipt_date`` 는 아시아 접수일자(RSVN_ORD_RCIT_DT;
    미국은 ``None``), ``status`` 는 :attr:`OrderStatus.PENDING_NEW`. 조회전용 계좌면
    :class:`AccountNotOrderableError`, 접수 거부는 :class:`OrderRejectedError`, 접수 불명(타임아웃)은
    :class:`OrderTimeoutError`(:func:`reconcile_overseas_reserved_order` /
    :func:`reconcile_asia_reserved_order` 로 확인 -- 단 재조회는 실전전용). 미지원 거래소·잘못된
    side/수량/가격/통화·client_order_id 재사용은 :class:`KISUsageError`, 순번 부재는 :class:`OrderError`.
    """
    if not orderable:
        raise AccountNotOrderableError(
            "이 계좌는 API 주문이 불가하다(퇴직연금 IRP/DC 등 조회전용). 일반/연금저축 계좌를 쓰라."
        )
    if side not in _SIDE_CODE:
        raise KISUsageError(f"side 는 buy/sell 이어야 한다: {side!r}")
    try:
        order_exchange, region = _ORDER_EXCHANGE[exchange]
    except KeyError:
        raise KISUsageError(
            f"해외 예약주문을 지원하지 않는 거래소코드: {exchange!r}."
        ) from None
    qty = coerce_decimal(quantity, "quantity")
    if qty <= 0 or qty != qty.to_integral_value():
        raise KISUsageError(f"예약주문 수량은 0보다 큰 정수(주)여야 한다: {qty}")
    limit = coerce_decimal(limit_price, "limit_price")
    if not limit.is_finite() or limit <= 0:
        raise KISUsageError(f"limit_price 는 0보다 큰 유한값이어야 한다: {limit_price!r}")

    if region == _US_MARKET:
        return _place_us_reserved(
            transport, store, symbol=symbol, side=side, qty=qty, limit=limit,
            order_exchange=order_exchange, exchange=exchange, currency=currency,
            client_order_id=client_order_id, cano=cano, product_code=product_code,
            environment=environment,
        )
    if region in _ASIA_MARKETS:
        return _place_asia_reserved(
            transport, store, symbol=symbol, side=side, qty=qty, limit=limit,
            order_exchange=order_exchange, exchange=exchange, currency=currency,
            client_order_id=client_order_id, cano=cano, product_code=product_code,
            environment=environment,
        )
    # _ORDER_EXCHANGE 전 항목이 미국/아시아라 현재는 도달 불가 -- 맵 확장 대비 fail-closed
    raise KISUsageError(f"해외 예약주문을 지원하지 않는 시장이다: {exchange!r}({region}).")


def _place_us_reserved(
    transport: Transport, store: OrderStore, *,
    symbol: str, side: Side, qty: Decimal, limit: Decimal, order_exchange: str,
    exchange: str, currency: str, client_order_id: str,
    cano: str, product_code: str, environment: Environment,
) -> ExecutionReport:
    """미국(NAS/NYS/AMS) 예약 발주 와이어를 만들어 공용 안전 셸에 넘긴다 -- 매수/매도 분리 TR +
    ORD_DVSN 지정가 body. 통화는 홍콩 전용이라 미국에 비-HKD 를 주면 fail-closed(조용히 무시하면
    의도 오해). 응답은 예약번호 ODNO 만 돌려주고 접수일자는 없다."""
    if currency != "HKD":
        raise KISUsageError(
            f"통화 지정은 홍콩(HKS) 예약주문에만 유효하다: {exchange!r}, {currency!r}"
        )
    fingerprint = ReservedOrderFingerprint(
        symbol=symbol, side=side, order_type="limit",
        quantity=format_wire_decimal(qty), limit_price=format_wire_decimal(limit),
        end_date="", exchange=_RESERVED_EXCHANGE,
    )
    body = {
        "CANO": cano, "ACNT_PRDT_CD": product_code, "PDNO": symbol,
        "OVRS_EXCG_CD": order_exchange,
        "FT_ORD_QTY": format_wire_decimal(qty),
        "FT_ORD_UNPR3": format_wire_decimal(limit),
        "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": _ORD_DVSN_LIMIT,
    }
    return _submit_reserved(
        transport, store, client_order_id, fingerprint, body,
        _PLACE_TR["US"][side][environment], _extract_us_reserved,
    )


def _place_asia_reserved(
    transport: Transport, store: OrderStore, *,
    symbol: str, side: Side, qty: Decimal, limit: Decimal, order_exchange: str,
    exchange: str, currency: str, client_order_id: str,
    cano: str, product_code: str, environment: Environment,
) -> ExecutionReport:
    """아시아(홍콩/상해/심천/일본/베트남) 예약 발주 와이어를 만들어 공용 안전 셸에 넘긴다 -- 공용 TR
    (TTTS3013U) + SLL_BUY_DVSN_CD + RVSE_CNCL_DVSN_CD=00 + 거래소·통화에서 파생한 PRDT_TYPE_CD.
    응답은 예약번호(OVRS_RSVN_ODNO)와 접수일자(RSVN_ORD_RCIT_DT)를 돌려준다."""
    prdt_type_cd = _asia_prdt_type_cd(exchange, currency)
    fingerprint = ReservedOrderFingerprint(
        symbol=symbol, side=side, order_type="limit",
        quantity=format_wire_decimal(qty), limit_price=format_wire_decimal(limit),
        end_date="", exchange=_ASIA_RESERVED_EXCHANGE, overseas_exchange=exchange,
        currency=currency,
    )
    body = {
        "CANO": cano, "ACNT_PRDT_CD": product_code, "PDNO": symbol,
        "SLL_BUY_DVSN_CD": _SIDE_CODE[side], "RVSE_CNCL_DVSN_CD": "00",
        "PRDT_TYPE_CD": prdt_type_cd, "OVRS_EXCG_CD": order_exchange,
        "FT_ORD_QTY": format_wire_decimal(qty),
        "FT_ORD_UNPR3": format_wire_decimal(limit),
        "ORD_SVR_DVSN_CD": "0",
    }
    return _submit_reserved(
        transport, store, client_order_id, fingerprint, body,
        _PLACE_TR["ASIA"]["any"][environment], _extract_asia_reserved,
    )


def _submit_reserved(
    transport: Transport, store: OrderStore, client_order_id: str,
    fingerprint: ReservedOrderFingerprint, body: dict[str, str], tr_id: str,
    extract_reservation: Callable[[Mapping[str, Any]], tuple[str, str | None]],
) -> ExecutionReport:
    """해외예약 발주의 공용 안전 셸 -- 이중발주 방지(claim)·무재시도 타임아웃·거부 처리·기록을 미국/
    아시아 공통으로 한 곳에서 처리한다(복제하면 두 경로의 이중발주 방어가 어긋날 수 있어 반드시 단일).
    시장별 body/TR/지문은 호출부(:func:`_place_us_reserved`/:func:`_place_asia_reserved`)가 만들고,
    응답의 예약번호/접수일자 추출만 ``extract_reservation`` 으로 주입받는다(미국은 접수일자 None)."""
    claim = store.try_claim(client_order_id, fingerprint)
    if isinstance(claim, Completed):
        return claim.report
    if isinstance(claim, Conflict):
        raise KISUsageError(
            f"client_order_id {client_order_id!r} 는 이미 다른 주문에 사용됐다. 새 id를 발행하라."
        )
    if isinstance(claim, InFlight):
        raise KISUsageError(
            f"해외 예약주문 {client_order_id} 은 전송됐으나 결과가 확인되지 않았다. "
            f"kis.orders.reconcile({client_order_id!r}) 로 재조회한 뒤 판단하라."
        )
    if not isinstance(claim, Claimed):
        raise OrderError(f"예상치 못한 claim 결과: {claim!r}")

    try:
        resp = transport.request(
            method="POST", path=_PLACE_PATH, tr_id=tr_id, body=body, idempotent=False
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
    reserved_id, receipt_date = extract_reservation(resp.body)
    if not reserved_id:
        raise OrderError(
            "해외 예약주문 접수 응답(rt_cd=0)에 예약주문번호(ODNO/OVRS_RSVN_ODNO)가 없다 -- 재조회 불가.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    report = _make_report(client_order_id, reserved_id, fingerprint.symbol, fingerprint.side,
                          resp.body, receipt_date=receipt_date)
    store.record(report, fingerprint)
    return report


def _extract_us_reserved(body: Mapping[str, Any]) -> tuple[str, str | None]:
    """미국 예약 발주 응답에서 (예약번호 ODNO, 접수일자=None) -- 미국은 접수일자를 돌려주지 않는다."""
    return _extract_reserved_id(body), None


def _extract_asia_reserved(body: Mapping[str, Any]) -> tuple[str, str | None]:
    """아시아 예약 발주 응답에서 (예약번호 OVRS_RSVN_ODNO, 접수일자 RSVN_ORD_RCIT_DT 또는 None --
    접수일자 부재는 취소 시점에 fail-closed 로 드러난다)."""
    reserved_id, receipt = _extract_asia_reservation(body)
    return reserved_id, (receipt or None)


def reconcile_overseas_reserved_order(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment, now: datetime | None = None,
) -> ExecutionReport | None:
    """미확인 미국 해외예약주문을 **예약주문조회**로 재조회한다 -- 보수적. 완료 리포트가 있으면 반환.
    in-flight 면 최근 창의 예약을 지문으로 매칭: 정확히 1건이면 확정, 0건이면 ``None``(재전송 금지 유지),
    2건 이상이면 :class:`KISError`. ``now`` 는 날짜창 기준시각(주입하면 결정적; 생략 시 현재 KST).
    조회 TR 이 실전전용이라 모의(paper)면 :class:`KISUsageError` 로 fail-closed(in-flight 유지)."""
    if environment == "paper":
        raise KISUsageError(
            "해외 예약주문 재조회는 예약주문조회(실전전용) 기반이라 모의투자 미지원 -- 실전에서만."
        )
    return _reconcile_reserved(
        transport, store, client_order_id, cano=cano, product_code=product_code,
        market="US", now=now,
    )


def reconcile_asia_reserved_order(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment, now: datetime | None = None,
) -> ExecutionReport | None:
    """미확인 아시아 해외예약주문을 아시아 예약주문조회(TTTS3014R)로 재조회한다 -- 매칭 규칙은
    :func:`reconcile_overseas_reserved_order` 와 같은 보수적 계약(정확히 1건 확정 / 0건 ``None`` /
    2건 이상 :class:`KISError`). 조회 TR 이 실전전용이라 모의(paper)면 :class:`KISUsageError` 로
    fail-closed(in-flight 유지, 재전송 금지)."""
    if environment == "paper":
        raise KISUsageError(
            "아시아 예약주문 재조회는 실전전용(TTTS3014R 모의투자 미지원) -- 실전에서만."
        )
    return _reconcile_reserved(
        transport, store, client_order_id, cano=cano, product_code=product_code,
        market="ASIA", now=now,
    )


def _reconcile_reserved(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, market: Literal["US", "ASIA"], now: datetime | None,
) -> ExecutionReport | None:
    """미국/아시아 공용 예약 재조회 코어 -- 완료 리포트 우선, in-flight 면 최근 창을 지문 매칭.
    확정 리포트엔 행의 접수일자(RSVN_ORD_RCIT_DT)를 실어 이후 취소(아시아)가 재조회 없이 가능하다."""
    prior = store.report_for(client_order_id)
    if prior is not None:
        return prior
    fingerprint = store.fingerprint_for(client_order_id)
    if fingerprint is None:
        raise KISUsageError(
            f"모르는 client_order_id: {client_order_id!r} (이 계좌로 전송한 적이 없다)."
        )
    if not isinstance(fingerprint, ReservedOrderFingerprint):  # 해외 예약주문 reconcile 경로
        raise KISError(
            f"client_order_id {client_order_id!r} 의 지문이 예약주문이 아니다"
            f"({type(fingerprint).__name__}, 내부 상태 불일치)."
        )
    today = (now or datetime.now(_KST)).date()
    start = f"{today - timedelta(days=_RECONCILE_LOOKBACK_DAYS):%Y%m%d}"
    end = f"{today + timedelta(days=_RECONCILE_FORWARD_DAYS):%Y%m%d}"
    try:
        rows = _walk(transport, cano, product_code, start, end, market=market)
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
    receipt_date = str(matches[0].get("rsvn_ord_rcit_dt") or "").strip() or None
    report = _make_report(client_order_id, reserved_id, fingerprint.symbol, fingerprint.side,
                          matches[0], receipt_date=receipt_date)
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
    주문 dedup 스토어를 거치지 않되, 타임아웃(처리 불명)엔 재전송하지 않는다. 모의(VTTT3017U)를
    지원한다. 실패는 :class:`KISUsageError`(빈 값/잘못된 날짜)·:class:`OrderRejectedError`(rt_cd!=0)·
    :class:`OrderTimeoutError`(타임아웃)·:class:`KISError`(정상 응답인데 취소 확인번호 부재/불일치)."""
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
            method="POST", path=_CANCEL_PATH, tr_id=_US_CANCEL_TR[environment], body=body,
            idempotent=False,
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


# --- 아시아 해외예약 취소 (뮤테이션, store 경유) ---------------------------
def cancel_asia_reserved_order(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment,
) -> ExecutionReport:
    """아시아 해외예약주문을 취소한다 -- 아시아는 전용 취소 엔드포인트가 없어(원장: "미제공") 발주
    TR(TTTS3013U, ``RVSE_CNCL_DVSN_CD``=02)에 **원주문 전체 + 예약번호/접수일자**를 다시 실어 취소한다.
    그 원주문 값은 store 의 지문(symbol/side/수량/단가/해석된 거래소)과 리포트(``order_id``=예약번호,
    ``receipt_date``=접수일자)에서 복원하므로 ``kis.orders.cancel(client_order_id)`` 로 라우팅된다.
    **지정가 예약 전용**, 전량 취소만. 모의(VTTS3013U)를 지원한다.

    ``PRDT_TYPE_CD`` 는 지문의 거래소·통화에서 재파생한다 -- 통화가 지문에 영속되므로 CNY/USD
    (543/558)로 발주한 홍콩 예약의 취소도 발주와 같은 코드로 재현된다. 반환 리포트의 ``status`` 는
    :attr:`OrderStatus.PENDING_CANCEL`. 모르는/비-아시아 예약 id 는 :class:`KISUsageError`,
    예약번호·접수일자가 리포트에 없으면 :class:`KISError`(재조회 유도), 거부는
    :class:`OrderRejectedError`, 타임아웃(처리 불명)은 :class:`OrderTimeoutError`(재전송 금지),
    정상 응답인데 취소 확인번호가 부재/불일치면 :class:`KISError`."""
    report = store.report_for(client_order_id)
    fingerprint = store.fingerprint_for(client_order_id)
    if (
        report is None
        or not isinstance(fingerprint, ReservedOrderFingerprint)
        or fingerprint.exchange != _ASIA_RESERVED_EXCHANGE
    ):
        raise KISUsageError(f"모르는 아시아 해외예약주문: {client_order_id!r}")
    if not report.order_id or not report.receipt_date:
        raise KISError(
            f"아시아 예약 취소에 필요한 예약번호/접수일자가 리포트에 없다 -- "
            f"kis.orders.reconcile({client_order_id!r}) 로 재조회한 뒤 다시 취소하라."
        )
    try:
        order_exchange, region = _ORDER_EXCHANGE[fingerprint.overseas_exchange]
    except KeyError:
        raise KISError(
            f"아시아 예약주문 {client_order_id!r} 지문의 거래소코드를 해석할 수 없다"
            f"({fingerprint.overseas_exchange!r}, 내부 상태 불일치)."
        ) from None
    if region not in _ASIA_MARKETS:
        raise KISError(
            f"아시아 예약주문 {client_order_id!r} 지문의 거래소가 아시아가 아니다"
            f"({fingerprint.overseas_exchange!r} -> {region}, 내부 상태 불일치)."
        )
    body = {
        "CANO": cano, "ACNT_PRDT_CD": product_code, "PDNO": fingerprint.symbol,
        "SLL_BUY_DVSN_CD": _SIDE_CODE[fingerprint.side], "RVSE_CNCL_DVSN_CD": "02",
        "PRDT_TYPE_CD": _asia_prdt_type_cd(fingerprint.overseas_exchange, fingerprint.currency),
        "OVRS_EXCG_CD": order_exchange,
        "FT_ORD_QTY": fingerprint.quantity, "FT_ORD_UNPR3": fingerprint.limit_price,
        "ORD_SVR_DVSN_CD": "0",
        "OVRS_RSVN_ODNO": report.order_id, "RSVN_ORD_RCIT_DT": report.receipt_date,
    }
    try:
        resp = transport.request(
            method="POST", path=_PLACE_PATH, tr_id=_PLACE_TR["ASIA"]["any"][environment],
            body=body, idempotent=False,
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"아시아 예약주문 취소 요청 시간초과 -- 처리 여부가 불명이다. 재전송하지 말고 "
            f"예약주문조회로 상태를 확인하라(예약번호 {report.order_id}).",
            client_order_id=client_order_id,
        ) from err
    if not resp.ok:
        raise OrderRejectedError(
            f"아시아 예약주문 취소 요청 거부: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    # 취소 응답의 유일한 업무 필드는 에코된 OVRS_RSVN_ODNO -- rt_cd=0 이어도 이 번호가 요청과 다르거나
    # 비어 있으면 실제 취소가 안 된 것으로 보고 fail-closed(미국 예약 취소의 확인번호 검증과 동형).
    echoed = _extract_ovrs_rsvn_odno(resp.body)
    if echoed != report.order_id:
        raise KISError(
            f"아시아 예약주문 취소 응답의 확인번호가 요청과 일치하지 않는다(응답 {echoed!r} != 요청 "
            f"{report.order_id!r}) -- 예약주문조회로 상태를 확인하라.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    cancelled = _make_report(client_order_id, report.order_id, fingerprint.symbol,
                             fingerprint.side, resp.body)
    return replace(cancelled, status=OrderStatus.PENDING_CANCEL, receipt_date=report.receipt_date)


def _filter_matching(
    rows: list[Mapping[str, Any]], fingerprint: ReservedOrderFingerprint, today: date
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
        if decimal_or_zero(row.get("ft_ord_qty")) != quantity:
            continue
        if limit_price is not None:
            row_price = row.get("ft_ord_unpr3")
            if row_price in (None, "") or decimal_or_zero(row_price) != limit_price:
                continue
        matched.append(row)
    return matched


def _make_report(
    client_order_id: str, reserved_id: str, symbol: str, side: Side, raw: Mapping[str, Any],
    *, receipt_date: str | None = None,
) -> ExecutionReport:
    return ExecutionReport(
        client_order_id=client_order_id,
        order_id=reserved_id,            # 해외예약주문번호(취소 시 OVRS_RSVN_ODNO)
        symbol=symbol,
        side=side,
        status=OrderStatus.PENDING_NEW,  # 접수됨·미집행
        filled_quantity=Decimal(0),
        average_price=None,
        recorded_at=datetime.now(_KST),
        receipt_date=receipt_date,       # 예약 접수일자(아시아 취소가 대상 특정에 소비)
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


def _extract_asia_reservation(body: Mapping[str, Any]) -> tuple[str, str]:
    """아시아 발주 응답에서 (예약주문번호, 접수일자)를 뽑는다. KIS 명세상 TTTS3013U 응답 output 은
    ``OVRS_RSVN_ODNO`` + ``RSVN_ORD_RCIT_DT``. 부재/비객체/다건이면 특정 불가라 빈 문자열 쌍."""
    out = body.get("output")
    if isinstance(out, list):
        if len(out) != 1:
            return "", ""
        out = out[0]
    if isinstance(out, Mapping):
        return (
            str(out.get("OVRS_RSVN_ODNO") or "").strip(),
            str(out.get("RSVN_ORD_RCIT_DT") or "").strip(),
        )
    return "", ""


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
        side=_side_from_code(row.get("sll_buy_dvsn_cd")),
        status=str(row.get("ovrs_rsvn_ord_stat_cd_name", "")).strip(),
        exchange=str(row.get("ovrs_excg_cd", "")).strip(),
        quantity=_decimal_or_zero(row, "ft_ord_qty"),
        order_price=_decimal_or_zero(row, "ft_ord_unpr3"),
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
