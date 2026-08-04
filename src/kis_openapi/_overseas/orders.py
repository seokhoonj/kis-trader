"""해외주식 주문 요청 조립 (내부) -- (거래소, 매수/매도, 실전/모의) -> TR + 바디.

주문 실행의 안전 규칙(이중체결 방지·쓰기 재시도 금지·보수적 재조회)은 국내와 공유하는 안전
코어(:class:`~kis_openapi.store.OrderStore`)가 맡는다. 이 모듈은 그 코어에 넘길 **해외 주문의
와이어 요청**만 조립한다 -- 순수 함수라 오케스트레이션 없이 단독 검증된다.

KIS URL/TR-id (원장 대조, sheet '해외주식 주문'):
- 주문: ``POST /uapi/overseas-stock/v1/trading/order``. TR 은 시장 x 매수/매도 x 실전/모의로 갈린다
  (아래 :data:`_ORDER_TR`). 시세 조회의 거래소코드(NAS/NYS/...)와 주문 거래소코드(NASD/NYSE/...)가
  다르므로 :data:`_ORDER_EXCHANGE` 로 매핑한다. 해외 주문은 지정가(ORD_DVSN=00) 중심이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from .._domestic.market_data import _KST
from .._wire import format_wire_decimal
from ..errors import KisError, KisUsageError, OrderTimeoutError
from ..report import ExecutionReport, OrderStatus
from ..store import OrderStore
from ..transport import Environment, Transport, TransportTimeout

if TYPE_CHECKING:
    from ..order import Order

_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"

#: 시세 거래소코드(EXCD) -> (주문 거래소코드 OVRS_EXCG_CD, 시장 그룹). 원장 코드표.
_ORDER_EXCHANGE: dict[str, tuple[str, str]] = {
    "NAS": ("NASD", "US"), "NYS": ("NYSE", "US"), "AMS": ("AMEX", "US"),
    "HKS": ("SEHK", "HK"),
    "SHS": ("SHAA", "SH"),
    "SZS": ("SZAA", "SZ"),
    "TSE": ("TKSE", "JP"),
    "HNX": ("HASE", "VN"), "HSX": ("VNSE", "VN"),
}

#: (시장 그룹, 매수/매도, 실전/모의) -> tr_id. 원장 코드표('해외주식 주문').
_ORDER_TR: dict[tuple[str, str, Environment], str] = {
    ("US", "buy", "real"): "TTTT1002U", ("US", "sell", "real"): "TTTT1006U",
    ("US", "buy", "demo"): "VTTT1002U", ("US", "sell", "demo"): "VTTT1001U",
    ("JP", "buy", "real"): "TTTS0308U", ("JP", "sell", "real"): "TTTS0307U",
    ("JP", "buy", "demo"): "VTTS0308U", ("JP", "sell", "demo"): "VTTS0307U",
    ("SH", "buy", "real"): "TTTS0202U", ("SH", "sell", "real"): "TTTS1005U",
    ("SH", "buy", "demo"): "VTTS0202U", ("SH", "sell", "demo"): "VTTS1005U",
    ("HK", "buy", "real"): "TTTS1002U", ("HK", "sell", "real"): "TTTS1001U",
    ("HK", "buy", "demo"): "VTTS1002U", ("HK", "sell", "demo"): "VTTS1001U",
    ("SZ", "buy", "real"): "TTTS0305U", ("SZ", "sell", "real"): "TTTS0304U",
    ("SZ", "buy", "demo"): "VTTS0305U", ("SZ", "sell", "demo"): "VTTS0304U",
    ("VN", "buy", "real"): "TTTS0311U", ("VN", "sell", "real"): "TTTS0310U",
    ("VN", "buy", "demo"): "VTTS0311U", ("VN", "sell", "demo"): "VTTS0310U",
}
_ORD_DVSN_LIMIT = "00"  # 지정가


def build_order_request(
    *,
    side: str,
    symbol: str,
    quantity: Decimal,
    limit_price: Decimal | None,
    exchange: str,
    cano: str,
    product_code: str,
    environment: Environment,
) -> tuple[str, str, str, dict[str, str]]:
    """해외 주문의 (method, path, tr_id, body) 를 조립한다.

    ``exchange`` 는 시세 거래소코드(NAS/NYS/...). 해외 주문은 지정가만 지원하므로 ``limit_price`` 가
    필요하다(시장가/MOO/MOC 등은 시장별로 제약이 달라 아직 미지원). ``quantity`` 는 정수(주 단위)."""
    if limit_price is None:
        raise KisUsageError("해외 주문은 지정가만 지원한다 -- price 를 지정하라(시장가 미지원).")
    if quantity != quantity.to_integral_value():
        raise KisUsageError(f"해외 주문 수량은 정수여야 한다(주 단위): {quantity}")
    try:
        order_exchange, market = _ORDER_EXCHANGE[exchange]
    except KeyError:
        raise KisUsageError(
            f"해외 주문을 지원하지 않는 거래소코드: {exchange!r} ({'/'.join(_ORDER_EXCHANGE)})."
        ) from None
    try:
        tr_id = _ORDER_TR[(market, side, environment)]
    except KeyError:
        raise KisUsageError(
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
    if side == "sell":
        body["SLL_TYPE"] = "00"        # 매도 표시(매수는 필드 없음)
    return "POST", _ORDER_PATH, tr_id, body


def is_overseas_exchange(exchange: str) -> bool:
    """``exchange`` 가 해외 거래소코드(주문 지원)면 True. 안전 코어의 주문 라우팅에 쓴다."""
    return exchange in _ORDER_EXCHANGE


def make_order_request(
    order: Order, cano: str, product_code: str, environment: Environment
) -> tuple[str, str, str, dict[str, str]]:
    """안전 코어(:func:`~kis_openapi._domestic.orders.place`)에 넘길 해외 주문 빌더.

    :class:`~kis_openapi.order.Order` 를 :func:`build_order_request` 인자로 풀어 넘긴다. ``order.exchange``
    는 시세 거래소코드(NAS/NYS/...)를 담는다."""
    return build_order_request(
        side=order.side, symbol=order.symbol, quantity=order.quantity,
        limit_price=order.limit_price, exchange=order.exchange,
        cano=cano, product_code=product_code, environment=environment,
    )


# --- 재조회(해외 주문체결내역) ---------------------------------------------
_CCNL_PATH = "/uapi/overseas-stock/v1/trading/inquire-ccnl"
_CCNL_TR = {"real": "TTTS3035R", "demo": "VTTS3035R"}
#: 해외 체결내역 매매구분코드. 매수=02, 매도=01(도메스틱과 다르다).
_SIDE_CODE = {"buy": "02", "sell": "01"}
_MAX_CCNL_PAGES = 100
#: 재조회 날짜창(일). 해외는 현지시각 기준이라 KST-오늘과 ±1일 어긋날 수 있어 여유를 둔다.
_LOOKBACK_DAYS = 2


def reconcile(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment,
) -> ExecutionReport | None:
    """미확인 해외 주문의 실제 상태를 체결내역에서 재조회한다 -- **보수적**(도메스틱과 동형).

    완료 리포트가 있으면 반환. in-flight 면 체결내역을 지문으로 스캔해 정확히 1건이면 확정, 0건이면
    ``None``(재전송 금지 유지), 2건 이상이면 :class:`KisError`. **자동 해제는 절대 하지 않는다.**
    ODNO 로는 검색이 안 돼(원장) 지문(종목/매매/수량/단가)으로 맞춘다."""
    prior = store.report_for(client_order_id)
    if prior is not None:
        return prior
    fingerprint = store.fingerprint_for(client_order_id)
    if fingerprint is None:
        raise KisUsageError(
            f"모르는 client_order_id: {client_order_id!r} (이 계좌로 전송한 적이 없다)."
        )
    exchange = fingerprint[-1]
    try:
        rows = _fetch_ccnl(
            transport, symbol=fingerprint[0], exchange=exchange,
            cano=cano, product_code=product_code, environment=environment,
        )
    except TransportTimeout as err:
        raise OrderTimeoutError(
            f"해외 재조회(체결내역) 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
            f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
            client_order_id=client_order_id,
        ) from err
    matches = _filter_matching_ccnl_rows(rows, fingerprint)
    if len(matches) > 1:
        raise KisError(
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
    cano: str, product_code: str, environment: Environment,
) -> list[Mapping[str, Any]]:
    """해외 체결내역을 연속조회 소진까지 읽어 행을 돌려준다(순수 I/O). 에러 응답은 fail-closed."""
    order_exchange = _ORDER_EXCHANGE.get(exchange, (exchange, ""))[0]
    now = datetime.now(_KST)
    start = f"{now - timedelta(days=_LOOKBACK_DAYS):%Y%m%d}"
    end = f"{now:%Y%m%d}"
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk = "", ""
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
            params=params, idempotent=True,  # 읽기 -- 타임아웃 재시도 안전
        )
        if not resp.ok:
            raise KisError(
                f"해외 재조회(체결내역) 실패: {resp.msg1}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        page = resp.body.get("output")
        rows.extend(page if isinstance(page, list) else [])
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        if not ctx_nk:
            break
    else:
        raise KisError(
            f"해외 재조회 스캔이 {_MAX_CCNL_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 스캔으로 확정하지 않는다. 재시도하거나 수동 확인하라."
        )
    return rows


def _filter_matching_ccnl_rows(
    rows: list[Mapping[str, Any]], fingerprint: tuple[str, ...]
) -> list[Mapping[str, Any]]:
    """체결내역 행 중 요청 지문과 맞는 것만(순수). 종목+매매구분+주문수량, 지정가는 주문단가까지 비교."""
    symbol, side = fingerprint[0], fingerprint[1]
    quantity = Decimal(fingerprint[3])
    limit_price = Decimal(fingerprint[4]) if fingerprint[4] else None
    want_side = _SIDE_CODE[side]
    matched = []
    for row in rows:
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
    client_order_id: str, fingerprint: tuple[str, ...], row: Mapping[str, Any]
) -> ExecutionReport:
    ordered = _parse_decimal(row.get("ft_ord_qty"))
    filled = _parse_decimal(row.get("ft_ccld_qty"))
    rejected = str(row.get("rjct_rson", "")).strip()
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
        symbol=fingerprint[0],
        side=fingerprint[1],
        status=status,
        filled_quantity=filled,
        average_price=avg if filled > 0 and avg > 0 else None,
        submitted_at=datetime.now(_KST),
        _raw=row,
    )


def _parse_decimal(value: object) -> Decimal:
    """KIS 문자열 수치 -> Decimal. 공백/None 은 0, 값이 있는데 파싱 실패면 fail-closed(:class:`KisError`)."""
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as err:
        raise KisError(f"해외 재조회 응답의 수치 파싱 실패: {value!r}") from err
