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

from ..._internal._datetime import _claim_date
from ..._internal._datetime import (
    _parse_optional_date as _parse_date,
)
from ..._internal._response import _CONTINUATION_END, _require_mapping_rows
from ..._internal._wire import decimal_or_zero, format_wire_decimal
from ...errors import (
    AccountNotOrderableError,
    KISAuthError,
    KISError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
)
from ...order import (
    AlgoStrategy,
    Order,
    ReservedOrderFingerprint,
    Side,
    coerce_decimal,
    validate_yyyymmdd,
)
from ...report import ExecutionReport, OrderStatus
from ...store import Claimed, Completed, Conflict, InFlight, OrderStore
from ...transport import Environment, Transport, TransportTimeoutError
from ..entities.orders import OverseasReservedOrder
from ._parse import _MAX_PAGES, _decimal_or_zero, _side_from_code
from .orders import _ALGO_TMD_CLOSE, _ORD_DVSN_ALGO, _ORDER_EXCHANGE, resolve_risk_currency

if TYPE_CHECKING:
    from ..._literals import Numeric
    from ...risk import RiskLimits

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
#: 해외 예약주문조회(order-resv-list)의 조회기간 최대 폭(일). KIS 는 start~end 가 7일 이상이면 거부한다
#: ("조회기간 7일 이상 불가" -- 라이브 확인 2026-10-14). 그래서 reconcile 은 접수일자(발주일 근처)에
#: 앵커한 이 폭의 창으로 질의하고, 공개 :func:`fetch_reserved_orders` 는 더 넓은 기간을 조용히 실패시키지
#: 않고 미리 거른다. (end-start 가 6일이면 7일 미만이라 허용된다.)
_RESERVED_QUERY_MAX_SPAN_DAYS = 6


def _asia_prdt_type_cd(exchange: str, currency: str | None) -> str:
    """아시아 예약주문의 PRDT_TYPE_CD(상품유형코드)를 거래소코드에서 파생한다(순수). ``currency`` 는
    홍콩(HKS)에만 의미가 있다(501 HKD / 543 CNY / 558 USD) -- 미지정(``None``)이면 HKD 로 본다.
    그 외 거래소는 통화 선택이 없어 호출 전에 ``currency`` 가 ``None`` 로 걸러져 온다."""
    if exchange == "HKS":
        try:
            return _HK_PRDT_TYPE_CD[currency or "HKD"]
        except KeyError:
            raise KISUsageError(
                f"홍콩 예약주문의 통화는 HKD/CNY/USD 여야 한다: {currency!r}"
            ) from None
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
    # KIS 는 조회기간 7일 이상을 거부한다("조회기간 7일 이상 불가") -- 넓은 기간을 서버에 보내 불투명
    # 오류를 받기 전에, 여기서 명확히 거른다(라이브 확인 2026-10-14).
    span = (datetime.strptime(end, "%Y%m%d") - datetime.strptime(start, "%Y%m%d")).days
    if span < 0:
        raise KISUsageError(f"end 가 start 보다 앞선다: start={start} end={end}")
    if span > _RESERVED_QUERY_MAX_SPAN_DAYS:
        raise KISUsageError(
            f"해외 예약주문조회는 기간이 {_RESERVED_QUERY_MAX_SPAN_DAYS}일 이하여야 한다"
            f"(KIS 가 7일 이상을 거부한다): start={start} end={end} (기간 {span}일). 기간을 좁혀라."
        )
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
        rows.extend(_require_mapping_rows("output", resp))
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
    currency: str | None = None, algo_strategy: AlgoStrategy | None = None,
    client_order_id: str, orderable: bool = True,
    cano: str, product_code: str, environment: Environment, risk: RiskLimits | None = None,
) -> ExecutionReport:
    """해외주식 예약주문을 안전 규칙으로 발주한다 -- 즉시주문(place)과 같은 이중발주 방지·재시도
    금지·보수적 재조회를, 예약 라이프사이클(예약번호·체결개념 없음)에 맞춰 구현한다. **지정가만**
    (``limit_price`` 필수). ``exchange`` 의 시장이 와이어를 가른다: 미국(NAS/NYS/AMS)은 매수/매도
    TR 분리 body(ORD_DVSN), 아시아(홍콩/상해/심천/일본/베트남)는 공용 TR(TTTS3013U) body
    (SLL_BUY_DVSN_CD + RVSE_CNCL_DVSN_CD=00 + 거래소에서 파생한 PRDT_TYPE_CD). ``currency`` 는
    홍콩(HKS) 예약의 상품유형 및 리스크 통화 선택(HKD/CNY/USD)에 쓰이며(미지정이면 HKD) 그 외 거래소에
    ``currency`` 를 주면 fail-closed. 발주는 모의(V* TR)를 지원한다.

    반환 :class:`ExecutionReport` 의 ``order_id`` 는 해외예약주문번호(미국 발주 Output ODNO = 취소 시
    OVRS_RSVN_ODNO / 아시아 OVRS_RSVN_ODNO), ``receipt_date`` 는 아시아 접수일자(RSVN_ORD_RCIT_DT;
    미국은 ``None``), ``status`` 는 :attr:`OrderStatus.PENDING_NEW`. 조회전용 계좌면
    :class:`AccountNotOrderableError`, 접수 거부는 :class:`OrderRejectedError`, 접수 불명(타임아웃)은
    :class:`OrderTimeoutError`. 저장된 유효 예약번호가 없으면 재조회도 None 이므로 수동 확인하고
    재전송하지 않는다(재조회는 실전전용). 미지원 거래소·잘못된
    side/수량/가격/통화·client_order_id 재사용은 :class:`KISUsageError`, 순번 부재는 :class:`OrderError`.
    risk 는 수량/지정 단가 기준 금액을 검사한다. 홍콩 명시 통화 외에는 기존 시장별 검사 통화를 쓰며,
    해당 통화 한도가 없거나 참조가가 필요한 설정이면 조회 없이 거부한다. 완료 replay 는 먼저 반환한다.
    """
    if not orderable:
        raise AccountNotOrderableError(
            "이 계좌는 API 주문이 불가하다(퇴직연금 IRP/DC 등 조회전용). 일반/연금저축 계좌를 쓰라."
        )
    if side not in _SIDE_CODE:
        raise KISUsageError(f"side 는 buy/sell 이어야 한다: {side!r}")
    try:
        order_exchange, order_market = _ORDER_EXCHANGE[exchange]
    except KeyError:
        raise KISUsageError(
            f"해외 예약주문을 지원하지 않는 거래소코드: {exchange!r}."
        ) from None
    if currency is not None and exchange != "HKS":
        raise KISUsageError(
            f"currency 는 홍콩(HKS) 예약주문 전용이다 -- {exchange!r} 에는 주지 마라"
            f"(미국·기타 아시아는 통화 선택이 없다): {currency!r}"
        )
    qty = coerce_decimal(quantity, "quantity")
    if qty <= 0 or qty != qty.to_integral_value():
        raise KISUsageError(f"예약주문 수량은 0보다 큰 정수(주)여야 한다: {qty}")
    limit = coerce_decimal(limit_price, "limit_price")
    if not limit.is_finite() or limit <= 0:
        raise KISUsageError(f"limit_price 는 0보다 큰 유한값이어야 한다: {limit_price!r}")
    if algo_strategy is not None:
        # 예약 algo(TWAP/VWAP)는 미국 실전 전용이다(공지 2025-05-23: 예약은 정규장 종료 집행 고정).
        # 아시아엔 algo 가 없고 모의는 미지원이라 라우팅 전에 fail-closed 한다.
        if algo_strategy not in _ORD_DVSN_ALGO:
            raise KISUsageError(f"지원하지 않는 algo 전략: {algo_strategy!r} (twap/vwap).")
        if order_market != _US_MARKET:
            raise KISUsageError(
                f"예약 algo(TWAP/VWAP)는 미국 거래소만 지원한다(아시아 algo 없음): {exchange!r}."
            )
        if environment == "paper":
            raise KISUsageError("예약 algo(TWAP/VWAP) 주문은 모의투자 미지원 -- 실전에서만.")

    if order_market == _US_MARKET:
        return _place_us_reserved(
            transport, store, symbol=symbol, side=side, qty=qty, limit=limit,
            order_exchange=order_exchange, exchange=exchange, algo_strategy=algo_strategy,
            client_order_id=client_order_id, cano=cano, product_code=product_code,
            environment=environment, risk=risk,
        )
    if order_market in _ASIA_MARKETS:
        return _place_asia_reserved(
            transport, store, symbol=symbol, side=side, qty=qty, limit=limit,
            order_exchange=order_exchange, exchange=exchange, currency=currency,
            client_order_id=client_order_id, cano=cano, product_code=product_code,
            environment=environment, risk=risk,
        )
    # _ORDER_EXCHANGE 전 항목이 미국/아시아라 현재는 도달 불가 -- 맵 확장 대비 fail-closed
    raise KISUsageError(f"해외 예약주문을 지원하지 않는 시장이다: {exchange!r}({order_market}).")


def _place_us_reserved(
    transport: Transport, store: OrderStore, *,
    symbol: str, side: Side, qty: Decimal, limit: Decimal, order_exchange: str,
    exchange: str, client_order_id: str, algo_strategy: AlgoStrategy | None = None,
    cano: str, product_code: str, environment: Environment, risk: RiskLimits | None = None,
) -> ExecutionReport:
    """미국(NAS/NYS/AMS) 예약 발주 와이어를 만들어 공용 안전 셸에 넘긴다 -- 매수/매도 분리 TR +
    ORD_DVSN body. 미국은 통화 선택이 없다(``currency`` 는 홍콩 전용이며, 미국에 주면 상위
    :func:`place_overseas_reserved_order` 가 이미 fail-closed 한다). 응답은 예약번호 ODNO 만 돌려주고
    접수일자는 없다. ``algo_strategy`` 가 있으면(TWAP/VWAP) ORD_DVSN 을 35/36 으로 바꾸고
    ALGO_ORD_TMD_DVSN_CD="02"(정규장 종료 집행)를 싣는다 -- 예약 algo 는 시간창이 없다(공지
    2025-05-23). 비-algo 는 ORD_DVSN="00"·algo 필드 없음이다. 지문에는 실제 전송 거래소도 기록한다."""
    fingerprint = ReservedOrderFingerprint(
        symbol=symbol, side=side, order_type="limit",
        quantity=format_wire_decimal(qty), limit_price=format_wire_decimal(limit),
        end_date="", exchange=_RESERVED_EXCHANGE, overseas_exchange=order_exchange,
        algo_strategy=algo_strategy or "",
    )
    body = {
        "CANO": cano, "ACNT_PRDT_CD": product_code, "PDNO": symbol,
        "OVRS_EXCG_CD": order_exchange,
        "FT_ORD_QTY": format_wire_decimal(qty),
        "FT_ORD_UNPR3": format_wire_decimal(limit),
        "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": _ORD_DVSN_LIMIT,
    }
    if algo_strategy is not None:
        body["ORD_DVSN"] = _ORD_DVSN_ALGO[algo_strategy]
        body["ALGO_ORD_TMD_DVSN_CD"] = _ALGO_TMD_CLOSE  # 예약 algo 는 정규장 종료 집행 고정(시간창 없음)
    return _submit_reserved(
        transport, store, client_order_id, fingerprint, body,
        _PLACE_TR["US"][side][environment], _extract_us_reserved, exchange=exchange, risk=risk,
    )


def _place_asia_reserved(
    transport: Transport, store: OrderStore, *,
    symbol: str, side: Side, qty: Decimal, limit: Decimal, order_exchange: str,
    exchange: str, currency: str | None, client_order_id: str,
    cano: str, product_code: str, environment: Environment, risk: RiskLimits | None = None,
) -> ExecutionReport:
    """아시아(홍콩/상해/심천/일본/베트남) 예약 발주 와이어를 만들어 공용 안전 셸에 넘긴다 -- 공용 TR
    (TTTS3013U) + SLL_BUY_DVSN_CD + RVSE_CNCL_DVSN_CD=00 + 거래소·통화에서 파생한 PRDT_TYPE_CD.
    응답은 예약번호(OVRS_RSVN_ODNO)와 접수일자(RSVN_ORD_RCIT_DT)를 돌려준다."""
    prdt_type_cd = _asia_prdt_type_cd(exchange, currency)
    fingerprint = ReservedOrderFingerprint(
        symbol=symbol, side=side, order_type="limit",
        quantity=format_wire_decimal(qty), limit_price=format_wire_decimal(limit),
        end_date="", exchange=_ASIA_RESERVED_EXCHANGE, overseas_exchange=exchange,
        # 지문/dedup 정체성은 byte-identity 기본 "HKD" 로 정규화(취소는 홍콩만 통화를
        # 되읽고, 그 외 아시아는 _asia_prdt_type_cd 가 통화를 무시한다). 홍콩 미지정도 HKD.
        currency=currency or "HKD",
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
        exchange=exchange, currency=currency, risk=risk,
    )


def _submit_reserved(
    transport: Transport, store: OrderStore, client_order_id: str,
    fingerprint: ReservedOrderFingerprint, body: dict[str, str], tr_id: str,
    extract_reservation: Callable[[Mapping[str, Any]], tuple[str, str | None]],
    *, exchange: str, currency: str | None = None, risk: RiskLimits | None = None,
) -> ExecutionReport:
    """해외예약 발주의 공용 안전 셸 -- 이중발주 방지(claim)·무재시도 타임아웃·거부 처리·기록을 미국/
    아시아 공통으로 한 곳에서 처리한다(복제하면 두 경로의 이중발주 방어가 어긋날 수 있어 반드시 단일).
    시장별 body/TR/지문은 호출부(:func:`_place_us_reserved`/:func:`_place_asia_reserved`)가 만들고,
    응답의 예약번호/접수일자 추출만 ``extract_reservation`` 으로 주입받는다(미국은 접수일자 None)."""
    # 저장된 결과를 새 리스크 설정으로 재해석하지 않고 fresh 만 검사 뒤 원자적으로 claim 한다.
    peek = store.classify(client_order_id, fingerprint)
    if isinstance(peek, Completed):
        return peek.report
    if isinstance(peek, Conflict):
        raise KISUsageError(
            f"client_order_id {client_order_id!r} 는 이미 다른 주문에 사용됐다. 새 id를 발행하라."
        )
    if isinstance(peek, InFlight):
        raise KISUsageError(
            f"해외 예약주문 {client_order_id} 은 전송됐으나 결과가 확인되지 않았다. "
            f"kis.orders.reconcile({client_order_id!r}) 로 저장 상태를 확인하라. "
            "증권사 주문번호가 없으면 불명으로 유지되므로 재전송하지 말고 "
            "HTS/MTS나 계좌 주문·체결 조회로 확인하라."
        )
    if risk is not None:
        # 지문의 예약 네임스페이스가 아닌 실제 거래소/해외주식으로 검사한다. 이 객체는 전송하지 않는다.
        order = Order(
            symbol=fingerprint.symbol, side=fingerprint.side, order_type="limit",
            quantity=Decimal(fingerprint.quantity), limit_price=Decimal(fingerprint.limit_price),
            region="overseas", asset_class="stock", exchange=exchange,
            client_order_id=client_order_id,
        )
        risk_currency = currency if exchange == "HKS" and currency is not None else resolve_risk_currency(exchange)
        risk.check(order, reference_price=None, currency=risk_currency)
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
            f"kis.orders.reconcile({client_order_id!r}) 로 저장 상태를 확인하라. "
            "증권사 주문번호가 없으면 불명으로 유지되므로 재전송하지 말고 "
            "HTS/MTS나 계좌 주문·체결 조회로 확인하라."
        )
    if not isinstance(claim, Claimed):
        raise OrderError(f"예상치 못한 claim 결과: {claim!r}")

    try:
        resp = transport.request(
            method="POST", path=_PLACE_PATH, tr_id=tr_id, body=body, idempotent=False
        )
    except TransportTimeoutError as err:
        raise OrderTimeoutError(
            f"해외 예약주문 전송 시간초과 -- 접수 여부가 불명이다. 재전송하지 말고 "
            f"kis.orders.reconcile({client_order_id!r}) 로 저장 상태를 확인하라. "
            "증권사 주문번호가 없으면 불명으로 유지되므로 "
            "HTS/MTS나 계좌 주문·체결 조회로 확인하라.",
            client_order_id=client_order_id,
        ) from err
    except KISAuthError:
        # 401 은 인증 단계 거부 -- 예약이 접수되지 않았음이 확실하다. in-flight 해제 후 그대로 올려
        # 토큰 교정 뒤 재시도하게 한다(_order_core 의 401-clear 와 동일 규율).
        store.clear_in_flight(client_order_id)
        raise
    except KISError as err:
        # 비-200 전송계층 오류(KIS 는 스로틀을 HTTP 500 으로 낸다). claim 뒤에 터졌고 접수 여부가
        # 불명이므로 타임아웃과 동일하게 in-flight 유지 + 재조회 요구 -- 그냥 올리면 호출자가 접수됐을
        # 수도 있는 예약을 새 id 로 재전송해 이중 예약이 난다(중복 전송 방지 가드, _order_core 와 동일).
        raise OrderTimeoutError(
            f"해외 예약주문 전송 중 전송계층 오류 -- 접수 여부가 불명이다. 재전송하지 말고 "
            f"kis.orders.reconcile({client_order_id!r}) 로 저장 상태를 확인하라. "
            "증권사 주문번호가 없으면 불명으로 유지되므로 "
            "HTS/MTS나 계좌 주문·체결 조회로 확인하라.",
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


def reconcile_us_reserved_order(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment, now: datetime | None = None,
    force_refresh: bool = False,
) -> ExecutionReport | None:
    """저장된 미국 예약번호로 예약주문조회를 재조회한다.

    기존 리포트는 force_refresh 가 아닐 때 반환한다. 저장된 유효 증권사 예약번호가 없는 초기
    요청은 지문 일치만으로 자동 확정하지 않고 None 으로 유지한다. 상세 조회창·접수일 제한은
    :func:`_reconcile_reserved` 를 따른다. now 는 날짜창 기준시각(생략 시 현재 KST)이다.
    조회 TR 이 실전전용이라 paper 는 KISUsageError이며 미확인 기록과 재전송 차단을 유지한다."""
    if environment == "paper":
        raise KISUsageError(
            "해외 예약주문 재조회는 예약주문조회(실전전용) 기반이라 모의투자 미지원 -- 실전에서만."
        )
    return _reconcile_reserved(
        transport, store, client_order_id, cano=cano, product_code=product_code,
        market="US", now=now, force_refresh=force_refresh,
    )


def reconcile_asia_reserved_order(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment, now: datetime | None = None,
    force_refresh: bool = False,
) -> ExecutionReport | None:
    """저장된 아시아 예약번호로 예약주문조회(TTTS3014R)를 재조회한다.

    초기 요청을 경제 조건·날짜만으로 자동 귀속하지 않는다. 기존 리포트와 force_refresh,
    저장 예약번호·접수일·조회창은 :func:`reconcile_us_reserved_order` 와 같은 제한을 따른다.
    조회 TR 은 실전전용으로 paper 는 KISUsageError이며 미확인 기록과 재전송 차단을 유지한다."""
    if environment == "paper":
        raise KISUsageError(
            "아시아 예약주문 재조회는 실전전용(TTTS3014R 모의투자 미지원) -- 실전에서만."
        )
    return _reconcile_reserved(
        transport, store, client_order_id, cano=cano, product_code=product_code,
        market="ASIA", now=now, force_refresh=force_refresh,
    )


def _reconcile_reserved(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, market: Literal["US", "ASIA"], now: datetime | None,
    force_refresh: bool = False,
) -> ExecutionReport | None:
    """미국/아시아 공용 예약 재조회 코어 -- 저장 예약번호와 접수일 문맥을 사용한다.

    기존 리포트가 있고 force_refresh 가 아니면 그대로 반환한다. 저장된 유효 증권사 예약번호가
    없으면 경제 조건·날짜만으로 초기 요청을 자동 귀속하지 않고 None 을 반환한다. 미확인 기록과
    재전송 차단을 유지하며 저장 번호의 레거시 출처는 이 조회가 증명하지 않는다.

    조회는 현재 주문번호 세대의 고정 기준 주변에 제한된 창을 쓴다. 레거시 관측 기준은 약한 계획이며
    기준 미상일 때만 조회일로 폴백한다. KIS 조회기간 제한과 접수일/집행일 필터 차이 때문에 창 밖의
    예약을 못 찾을 수 있다. 저장된 유효 접수일이 있으면 같은 번호여도 그 날짜와 다른 행은 제외한다.
    0건은 None, 다건·조회 실패는 오류이며 미접수로 단정하지 않는다. 접수일을 모르는 미국 주문에
    조회 기준 날짜를 접수일로 만들어 넣지 않는다. 날짜창과 번호만으로 보편적 인과성을 증명하지 않는다.
    force_refresh 는 캐시를 건너뛰지만 이 날짜창과 자산별 제한은 그대로다."""
    snapshot = store._recovery_snapshot_for(client_order_id)
    prior, fingerprint, query_anchor, _ = snapshot
    if prior is not None and not force_refresh:
        return prior
    if fingerprint is None:
        raise KISUsageError(
            f"모르는 client_order_id: {client_order_id!r} (이 계좌로 전송한 적이 없다)."
        )
    if not isinstance(fingerprint, ReservedOrderFingerprint):  # 해외 예약주문 reconcile 경로
        raise KISError(
            f"client_order_id {client_order_id!r} 의 지문이 예약주문이 아니다"
            f"({type(fingerprint).__name__}, 내부 상태 불일치)."
        )
    # 저장된 유효 증권사 주문번호가 없으면 경제 조건이나 날짜만으로 요청을 귀속하지 않는다.
    # 미확인 상태와 재전송 차단을 유지하며 HTS/MTS 등에서 수동 확인한다.
    if prior is None or not (prior.order_id or "").strip().lstrip("0"):
        return None
    today = (now or datetime.now(_KST)).date()
    # 같은 원자 스냅샷의 현재 주문번호 세대 기준에 최대 6일 창을 고정한다.
    # KIS의 서버 날짜 필터 기준과 창 밖 집행은 보장하지 않는다. 미상 기준만 조회일로 폴백한다.
    anchor = _claim_date(query_anchor[0])
    anchored = anchor if anchor is not None else today
    start = f"{anchored - timedelta(days=1):%Y%m%d}"
    end = f"{anchored + timedelta(days=_RESERVED_QUERY_MAX_SPAN_DAYS - 1):%Y%m%d}"
    try:
        rows = _walk(transport, cano, product_code, start, end, market=market)
    except TransportTimeoutError as err:
        raise OrderTimeoutError(
            f"해외 예약주문 재조회 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
            f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
            client_order_id=client_order_id,
        ) from err
    prior_seq = prior.order_id if prior is not None else None
    if prior_seq:
        matches = [r for r in rows if str(r.get("ovrs_rsvn_odno") or "").strip() == prior_seq]
    else:
        matches = []  # 초기 번호 없는 요청은 조회 전에 반환한다.
    known_receipt = _parse_date(prior.receipt_date)
    if known_receipt is not None:
        matches = [row for row in matches
                   if _parse_date(row.get("rsvn_ord_rcit_dt")) == known_receipt]
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
    if not store._record_recovery_if_unchanged(report, fingerprint, expected=snapshot):
        return store.report_for(client_order_id)
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
    except TransportTimeoutError as err:
        raise OrderTimeoutError(
            f"해외 예약주문 취소 요청 시간초과 -- 처리 여부가 불명이다. 재전송하지 말고 "
            f"예약주문조회로 상태를 확인하라(예약번호 {reserved_order_id}).",
            client_order_id=str(reserved_order_id),   # 이 흐름엔 client_order_id 가 없어 예약번호를 넣는다
        ) from err
    except KISAuthError:
        raise      # 401: 취소 미적용 확실 -- 토큰 교정 후 재시도
    except KISError as err:
        # 스로틀(HTTP 500) 등 -- 처리 여부 불명. raw 로 올리면 재전송 이중취소 위험이라 타임아웃과
        # 동일한 '재전송 금지 + 조회 확인' 계약으로 바꾼다.
        raise OrderTimeoutError(
            f"해외 예약주문 취소 요청 중 전송계층 오류 -- 처리 여부가 불명이다. 재전송하지 말고 "
            f"예약주문조회로 상태를 확인하라(예약번호 {reserved_order_id}).",
            client_order_id=str(reserved_order_id),
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
    # (리포트, 지문)을 한 락으로 원자 스냅샷 -- 아래 취소 와이어는 리포트(예약번호·접수일자)와
    # 지문(종목·방향·수량·단가·거래소)을 함께 읽어 조립하므로 둘의 세대가 일치해야 한다.
    binding = store.binding_for(client_order_id)
    if binding is None:
        raise KISUsageError(f"모르는 아시아 해외예약주문: {client_order_id!r}")
    report, fingerprint = binding
    if (
        not isinstance(fingerprint, ReservedOrderFingerprint)
        or fingerprint.exchange != _ASIA_RESERVED_EXCHANGE
    ):
        raise KISUsageError(f"모르는 아시아 해외예약주문: {client_order_id!r}")
    if not report.order_id or not report.receipt_date:
        raise KISError(
            f"아시아 예약 취소에 필요한 예약번호/접수일자가 리포트에 없다 -- "
            f"저장된 유효 예약번호가 있으면 kis.orders.reconcile({client_order_id!r}, refresh=True) 로 "
            "접수일을 조회하라. 필요한 번호나 접수일을 확인할 수 없으면 취소를 재전송하지 말고 "
            "HTS/MTS나 계좌 주문·체결 조회로 수동 확인하라."
        )
    try:
        order_exchange, order_market = _ORDER_EXCHANGE[fingerprint.overseas_exchange]
    except KeyError:
        raise KISError(
            f"아시아 예약주문 {client_order_id!r} 지문의 거래소코드를 해석할 수 없다"
            f"({fingerprint.overseas_exchange!r}, 내부 상태 불일치)."
        ) from None
    if order_market not in _ASIA_MARKETS:
        raise KISError(
            f"아시아 예약주문 {client_order_id!r} 지문의 거래소가 아시아가 아니다"
            f"({fingerprint.overseas_exchange!r} -> {order_market}, 내부 상태 불일치)."
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
    except TransportTimeoutError as err:
        raise OrderTimeoutError(
            f"아시아 예약주문 취소 요청 시간초과 -- 처리 여부가 불명이다. 재전송하지 말고 "
            f"예약주문조회로 상태를 확인하라(예약번호 {report.order_id}).",
            client_order_id=client_order_id,
        ) from err
    except KISAuthError:
        raise      # 401: 취소 미적용 확실 -- 토큰 교정 후 재시도
    except KISError as err:
        raise OrderTimeoutError(
            f"아시아 예약주문 취소 요청 중 전송계층 오류 -- 처리 여부가 불명이다. 재전송하지 말고 "
            f"예약주문조회로 상태를 확인하라(예약번호 {report.order_id}).",
            client_order_id=client_order_id,
        ) from err
    if not resp.ok:
        raise OrderRejectedError(
            f"아시아 예약주문 취소 요청 거부: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    # 아시아 취소는 발주 TR(TTTS3013U, RVSE_CNCL=02)로 나가므로 취소 자체가 새 예약번호(취소주문 번호)를
    # 받는다 -- 응답의 OVRS_RSVN_ODNO 는 '취소주문'의 번호이지 원 예약번호가 아니다(라이브 확인: 원 684 를
    # 취소하면 응답 685). 미국 취소(전용 ccnl 엔드포인트)와 달리 원 예약번호를 에코하지 않으므로 원번호와의
    # 일치를 요구하면 정상 취소를 오거부한다. 접수 확정은 rt_cd=0(=resp.ok)이고, 취소주문 번호가 실려 왔는지
    # (비어있지 않은지)만 fail-closed 로 본다(rt_cd=0 인데 번호 부재면 부분/오응답).
    cancel_odno = _extract_ovrs_rsvn_odno(resp.body)
    if not cancel_odno:
        raise KISError(
            "아시아 예약주문 취소 응답(rt_cd=0)에 취소주문번호(OVRS_RSVN_ODNO)가 없다 -- "
            "예약주문조회로 상태를 확인하라.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    cancelled = _make_report(client_order_id, report.order_id, fingerprint.symbol,
                             fingerprint.side, resp.body)
    return replace(cancelled, status=OrderStatus.PENDING_CANCEL, receipt_date=report.receipt_date)


def _filter_matching(
    rows: list[Mapping[str, Any]], fingerprint: ReservedOrderFingerprint,
    recent_receipt_dates: set[date],
) -> list[Mapping[str, Any]]:
    """예약주문조회 행 중 요청 지문과 맞는 것만(순수). 종목+매매구분+수량+단가에 더해 **접수일자**로
    좁힌다 -- 해외예약 지문엔 end_date 등 추가 판별자가 없어(국내와 달리) 같은 종목/수량/가격의 옛
    pending 예약이 넓은 창에서 가짜 단일매칭될 수 있다. ``recent_receipt_dates`` 는 발주일(claim 확보일)에
    앵커한 창(:func:`~kis_trader._internal._datetime._reconcile_recency_window`, 국내 reserved 와 같은
    규율)이라 며칠 뒤 체결되는 예약을 늦게 reconcile 해도 접수일자가 창에 든다. 판별자가 적은 해외는
    국내와 달리 접수일자 파싱 불가 행도 제외한다(fail-closed -- 좁힐 근거가 없으면 매칭 대상에서 뺀다).
    예약번호 없는/취소된 행 제외."""
    quantity = Decimal(fingerprint.quantity)
    limit_price = Decimal(fingerprint.limit_price) if fingerprint.limit_price else None
    want_side = _SIDE_CODE[fingerprint.side]
    matched = []
    for row in rows:
        if not str(row.get("ovrs_rsvn_odno", "")).strip():  # 예약번호 없는 행 -- 매칭 불가
            continue
        if str(row.get("cncl_yn", "")).strip().upper() == "Y":  # 취소된 예약은 제외
            continue
        receipt = _parse_date(row.get("rsvn_ord_rcit_dt"))
        if receipt is None or receipt not in recent_receipt_dates:  # 발주일창 접수분만(판별자 적어 None 제외)
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
    if isinstance(out, (list, tuple)):
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
    if isinstance(out, (list, tuple)):
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
    if isinstance(out, (list, tuple)):
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
        order_quantity=_decimal_or_zero(row, "ft_ord_qty"),
        order_price=_decimal_or_zero(row, "ft_ord_unpr3"),
        filled_quantity=_decimal_or_zero(row, "ft_ccld_qty"),
        canceled=str(row.get("cncl_yn", "")).strip().upper() == "Y",
        unprocessed_reason=str(row.get("nprc_rson_text", "")).strip(),
        _raw=row,
    )
