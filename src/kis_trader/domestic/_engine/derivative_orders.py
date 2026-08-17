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
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from ..._internal._datetime import _KST
from ..._internal._wire import format_wire_decimal
from ...errors import KISError, KISUsageError, OrderError, OrderTimeoutError
from ...order import _DERIVATIVE_EXCHANGE, ImmediateOrderFingerprint, Order, WireRequest
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
        raise KISUsageError(f"파생 주문 수량은 정수여야 한다(계약 단위): {order.quantity}")
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


def _extract_fo_output(body: Mapping[str, Any]) -> Mapping[str, Any]:
    """파생 주문 응답에서 ``output``(object)만 엄격히 뽑는다 -- 커널의 top-level 폴백을 쓰지 않는다.

    ``output`` 키가 없거나 Mapping 이 아니면 :class:`~kis_trader.errors.OrderError` 로 fail-closed
    한다(top-level 로 폴백해 엉뚱한 ODNO 를 읽으면 재조회 대상이 어긋나 오확정으로 이어진다)."""
    out = body.get("output")
    if not isinstance(out, Mapping):
        raise OrderError("파생 주문 응답에 output(object)이 없다 -- top-level 폴백 금지, 재조회 불가.")
    return out


# --- 재조회(국내 파생 주간 일별체결내역) -----------------------------------
_DAY_INQUIRY_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-ccnl"
#: session -> environment -> tr_id. 주간(정규)만 -- 야간(STTN5201R)은 Task 7 의 union 스캔.
_INQUIRY_TR: dict[str, dict[str, str]] = {
    "regular": {"real": "TTTO5201R", "paper": "VTTO5201R"},
}
#: 재조회 연속조회(페이지) 상한 -- 무한 루프 방지의 명시적 안전 상한.
_MAX_INQUIRY_PAGES = 100
#: 원주문 행의 원주문번호 센티넬(0-채움). 이와 다르면 그 행은 정정/취소 행이라 원주문 매칭에서 뺀다.
_ORIGINAL_ODNO = "0000000000"


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
        # 야간(STTN) 체결은 주간 일별체결내역(TTTO5201R)에 없으므로, 여기서 단일 매칭되는 행은 반드시
        # 주간 주문이다 -> 야간 주문을 그 행으로 확정하면 오확정이다. 야간 전용 조회(STTN5201R union
        # 스캔)가 아직 없어, 자동 확정하지 않고 fail-closed 로 올린다(수동 확인 필요).
        raise KISError(
            f"파생 야간(STTN) 주문 {client_order_id!r} 은 주간 체결내역으로 확인할 수 없다 "
            f"(야간 전용 재조회 미구현). 수동 확인이 필요하다."
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
    matches = _filter_matching_rows(rows, fingerprint)
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
) -> list[Mapping[str, Any]]:
    """파생 주간 일별체결내역을 연속조회 소진까지 읽어 행을 돌려준다(순수 I/O). 에러 응답/오형상은
    fail-closed -- 빈 결과로 오인해 '미접수'로 단정하면 이중체결로 이어진다. ``now`` 주입 시 날짜창이
    결정적(테스트용); 생략 시 현재 KST. 국내(KST) 당일 하루를 본다."""
    stamp = datetime.now(_KST) if now is None else now
    day = f"{stamp:%Y%m%d}"
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_INQUIRY_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "STRT_ORD_DT": day, "END_ORD_DT": day,
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


def _filter_matching_rows(
    rows: list[Mapping[str, Any]], fingerprint: ImmediateOrderFingerprint
) -> list[Mapping[str, Any]]:
    """일별체결내역 행 중 요청 지문과 맞는 것만(순수). 종목+매매구분+호가유형(nmpr_type_cd)+주문수량,
    지정가는 주문단가(ord_idx)까지 비교한다. 호가유형은 발주와 **같은** 리졸버의 NMPR_TYPE_CD 로 --
    이 행에는 ord_dvsn_cd 가 없어 그걸 요구하면 매칭이 전부 사라진다. 정정/취소 행(원주문번호를
    참조 -- orgn_odno != 0)은 원주문 지문 매칭에서 제외해 가짜 다중매칭을 줄인다."""
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
