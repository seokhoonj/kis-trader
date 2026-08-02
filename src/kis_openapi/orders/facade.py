"""주문 파사드 -- :class:`Orders`. 안전 표준을 구현하는 핵심.

``place`` 흐름의 안전 규칙:
1. 계좌 주문가능 여부와 와이어 변환을 **먼저** 확인(퇴직연금/미구현이면 여기서 중단 --
   in-flight 를 남기지 않음).
2. :meth:`OrderStore.try_claim` 로 상태를 **원자적으로** 판정/확보: 완료면 그 리포트
   replay, 같은 id 다른 지문이면 충돌 거부, in-flight 면 재조회 요구, 새로 확보되면 전송.
   (한 저장소를 공유하는 여러 스레드/인스턴스의 이중전송도 이 원자 연산이 막는다.)
3. 전송. 타임아웃이면 **재시도 없이** in-flight 유지 후 :class:`OrderTimeoutError`.
4. 접수 거부(``rt_cd`` != 0)면 in-flight 해제 후 :class:`OrderRejectedError`(체결 아님).
5. 접수 성공이되 거래소 주문번호(ODNO) 없으면 재조회 불가 -> in-flight 유지하고 raise.
6. 성공이면 리포트(상태=NEW, ``order_id``=ODNO)를 기록하고 반환.

타임아웃/미확인 주문은 :meth:`reconcile` 로 재조회한다 -- KIS 일별체결조회를 스캔해
지문과 맞는 주문의 실제 상태를 확정한다(KIS가 ``client_order_id`` 를 돌려주지 않으므로
종목+매매구분+수량 지문으로 매칭). **보수적**: 스캔이 비거나 모호하면 미접수로 단정하지
않고 in-flight 를 유지한다(부재는 미접수의 증거가 아니다).

KIS URL/TR-id (국내주식):
- 현금주문: ``POST /uapi/domestic-stock/v1/trading/order-cash``
  실전 매수 ``TTTC0012U`` / 매도 ``TTTC0011U``, 모의 매수 ``VTTC0012U`` / 매도 ``VTTC0011U``.
- 일별체결조회(재조회): ``GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld``
  실전 ``TTTC0081R`` / 모의 ``VTTC0081R``.
해외/신용/정정취소는 다음 슬라이스.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from ..errors import (
    AccountNotOrderable,
    KisError,
    KisUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
)
from ..transport import Transport, TransportTimeout
from .order import Order, Side, TimeInForce, format_wire_decimal
from .report import ExecutionReport, OrderStatus
from .store import ClaimOutcome, OrderStore

_KST = timezone(timedelta(hours=9))

#: 재조회 시 일별체결조회 연속조회(페이지) 상한 -- 무한 루프 방지의 명시적 안전 상한.
_MAX_RECONCILE_PAGES = 100

#: 국내(KRX/KOSDAQ/Nextrade) 시장 식별코드 -- 이 셋은 국내 현금주문으로 라우팅.
_DOMESTIC_MICS = frozenset(("XKRX", "XKOS", "NXTE"))

_ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
# (environment, side) -> tr_id
_ORDER_CASH_TR = {
    ("real", "buy"): "TTTC0012U", ("real", "sell"): "TTTC0011U",
    ("demo", "buy"): "VTTC0012U", ("demo", "sell"): "VTTC0011U",
}
_DAILY_CCLD_TR = {"real": "TTTC0081R", "demo": "VTTC0081R"}
# order_type -> KIS ORD_DVSN(주문구분): 00 지정가, 01 시장가
_ORD_DVSN = {"limit": "00", "market": "01"}
# our side -> KIS SLL_BUY_DVSN_CD (01 매도, 02 매수)
_SIDE_CODE = {"buy": "02", "sell": "01"}


class Orders:
    """한 계좌의 주문 실행 표면. 하나의 :class:`Transport` 와 :class:`OrderStore` 를 공유한다."""

    def __init__(
        self,
        transport: Transport,
        store: OrderStore,
        *,
        cano: str,
        product_code: str,
        orderable: bool = True,
        environment: Literal["real", "demo"] = "real",
    ) -> None:
        self._transport = transport
        self._store = store
        self._cano = cano
        self._product_code = product_code
        self._orderable = orderable
        self._environment = environment

    # --- 주문 실행 ----------------------------------------------------
    def place(self, order: Order) -> ExecutionReport:
        """주문을 안전 규칙에 따라 전송한다(모듈 docstring의 6단계)."""
        client_order_id = order.client_order_id
        fingerprint = order.fingerprint

        if not self._orderable:
            raise AccountNotOrderable(
                "이 계좌는 API 주문이 불가하다(퇴직연금 IRP/DC 등 조회전용). 일반/연금저축 계좌를 쓰라."
            )
        # 와이어 변환을 먼저 -- 미구현/부적합이면 claim 전에 중단(stuck in-flight 방지).
        method, path, tr_id, body = self._order_cash_wire(order)

        outcome, prior = self._store.try_claim(client_order_id, fingerprint)
        if outcome is ClaimOutcome.COMPLETED:
            if prior is None:  # assert 가 아니라 -- python -O 에서도 안전
                raise OrderError("claim 이 COMPLETED 인데 리포트가 없다(store 불변식 위반).")
            return prior
        if outcome is ClaimOutcome.CONFLICT:
            raise KisUsageError(
                f"client_order_id {client_order_id!r} 는 이미 다른 주문에 사용됐다. 새 id를 발행하라."
            )
        if outcome is ClaimOutcome.IN_FLIGHT:
            raise KisUsageError(
                f"주문 {client_order_id} 은 전송됐으나 결과가 확인되지 않았다. "
                f"kis.orders.reconcile({client_order_id!r}) 로 재조회한 뒤 판단하라."
            )
        # 전송은 '좁게 가드된' 경우여야 한다 -- CLAIMED 만 와이어에 닿는다(assert 로 두면 -O 에서
        # 예상외 outcome 이 전송으로 fail-open 된다).
        if outcome is not ClaimOutcome.CLAIMED:
            raise OrderError(f"예상치 못한 claim outcome: {outcome!r}")

        try:
            resp = self._transport.request(
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
            self._store.clear_in_flight(client_order_id)
            raise OrderRejectedError(
                f"주문 접수 거부: {resp.msg1}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )

        output = _extract_output_mapping(resp.body)
        order_id = output.get("ODNO")
        if not order_id:
            # 접수(rt_cd=0)인데 거래소 주문번호가 없음 -> 재조회 불가. 상태 불명이므로
            # in-flight 를 유지한 채 raise(추후 reconcile 로 재조회).
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
            raw=resp.body,
        )
        self._store.record(report, fingerprint)
        return report

    # --- 편의 진입점 --------------------------------------------------
    def buy(self, symbol: str, *, quantity: object, limit_price: object | None = None,
            time_in_force: TimeInForce = "day", exchange: str = "XKRX",
            client_order_id: str | None = None) -> ExecutionReport:
        """매수 -- ``limit_price`` 를 주면 지정가, 없으면 시장가."""
        return self.place(_market_or_limit_order(symbol, "buy", quantity, limit_price,
                                                 time_in_force, exchange, client_order_id))

    def sell(self, symbol: str, *, quantity: object, limit_price: object | None = None,
             time_in_force: TimeInForce = "day", exchange: str = "XKRX",
             client_order_id: str | None = None) -> ExecutionReport:
        """매도 -- ``limit_price`` 를 주면 지정가, 없으면 시장가."""
        return self.place(_market_or_limit_order(symbol, "sell", quantity, limit_price,
                                                 time_in_force, exchange, client_order_id))

    # --- 재조회(reconcile) --------------------------------------------
    def reconcile(self, client_order_id: str) -> ExecutionReport | None:
        """미확인 주문의 실제 상태를 브로커에 재조회한다 -- **보수적**.

        완료 리포트가 있으면 그대로 반환. in-flight(타임아웃 등)이면 KIS 일별체결조회를
        스캔해 요청 지문과 맞는 주문을 찾는다: **정확히 1건**이면 그 상태로 리포트를 확정한다.

        스캔이 비었거나(반영 지연일 수 있음) 여러 건이 모호하면 **절대 미접수로 단정하지
        않는다** -- in-flight 를 유지하고 재전송을 허용하지 않는다(일별체결조회는
        eventually-consistent 뷰라, 한 번 비었다고 미접수의 증거가 아니다). ``None`` 은
        오직 "in-flight 인데 아직 미확인(재전송 금지)"만 뜻한다. 스캔이 실패(에러/타임아웃)
        하면 in-flight 를 유지한 채 예외를 올린다. 이 ``Orders`` 로 보낸 적 없는 id면
        :class:`KisUsageError`. **자동 해제는 절대 하지 않는다** -- 해제는 원 전송의 명시적
        거부(rt_cd!=0) 또는 운영자의 의도적 조치로만 일어난다.

        한계: KIS는 client_order_id 를 돌려주지 않아 매칭은 지문 기반이다. 동일 지문 주문이
        둘 이상이면 :class:`KisError` 로 수동 확인을 요구한다.
        """
        prior = self._store.report_for(client_order_id)
        if prior is not None:
            return prior
        fingerprint = self._store.fingerprint_for(client_order_id)
        if fingerprint is None:  # 완료도 in-flight 도 아님 -> 이 Orders 가 모르는 id
            raise KisUsageError(
                f"모르는 client_order_id: {client_order_id!r} (이 Orders 로 전송한 적이 없다)."
            )
        # 여기 도달하면 in-flight(완료는 위에서 반환됨) -- 브로커에 실제 상태를 조회한다.
        try:
            rows = self._fetch_daily_orders(fingerprint[0])
        except TransportTimeout as err:
            # 재조회 스캔이 타임아웃 -- 상태 여전히 불명. in-flight 유지, 도메인 예외로.
            raise OrderTimeoutError(
                f"재조회(일별체결조회) 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
                f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
                client_order_id=client_order_id,
            ) from err
        matches = _matching_rows(rows, fingerprint)

        if len(matches) > 1:
            raise KisError(
                f"주문 {client_order_id} 의 지문과 일치하는 당일 주문이 {len(matches)}건이라 "
                f"자동 확정 불가하다(KIS가 client_order_id를 돌려주지 않음). 수동 확인이 필요하다."
            )
        if not matches:
            # 0건: 미접수인지 반영 지연인지 단정 불가 -> in-flight 유지, 재전송 금지.
            return None
        report = _execution_report_from_daily_row(client_order_id, fingerprint, matches[0])
        self._store.record(report, fingerprint)
        return report

    # --- 와이어 매핑(국내 현금주문) ----------------------------------
    def _order_cash_wire(self, order: Order) -> tuple[str, str, str, dict[str, str]]:
        if order.exchange not in _DOMESTIC_MICS:
            raise NotImplementedError(
                f"해외주문은 아직 미구현이다(exchange={order.exchange!r}). 다음 슬라이스."
            )
        if order.order_type not in _ORD_DVSN:
            raise NotImplementedError(
                f"{order.order_type} 주문은 아직 와이어 매핑이 없다(현재 시장가/지정가만)."
            )
        tr_id = _ORDER_CASH_TR[(self._environment, order.side)]
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._product_code,
            "PDNO": order.symbol,
            "ORD_DVSN": _ORD_DVSN[order.order_type],
            "ORD_QTY": _plain(order.quantity),
            "ORD_UNPR": "0" if order.order_type == "market" else _plain(order.limit_price),
            "EXCG_ID_DVSN_CD": "KRX",
        }
        return "POST", _ORDER_CASH_PATH, tr_id, body

    def _fetch_daily_orders(self, symbol: str) -> list[Mapping[str, Any]]:
        """당일 일별체결조회를 **연속조회 소진까지** 읽어 한 종목의 모든 주문 행을 돌려준다
        (순수 I/O). 조회(I/O)와 필터/매칭(순수 판정)을 분리해, 매칭은 :func:`_matching_rows`
        가 한다.

        에러 응답(``rt_cd`` != 0)은 :class:`KisError` 로 올린다 -- 빈 결과로 오인해
        '미접수'로 단정하면 이중체결로 이어지기 때문. 1페이지만 읽고 멈추면 뒤 페이지의
        매칭을 놓쳐 같은 오판이 나므로, ``CTX_AREA`` 키가 빌 때까지 반복한다.
        """
        today = f"{datetime.now(_KST):%Y%m%d}"
        rows: list[Mapping[str, Any]] = []
        ctx_fk, ctx_nk = "", ""
        for _page in range(_MAX_RECONCILE_PAGES):
            params = {
                "CANO": self._cano, "ACNT_PRDT_CD": self._product_code,
                "INQR_STRT_DT": today, "INQR_END_DT": today,
                "SLL_BUY_DVSN_CD": "00", "PDNO": symbol, "ORD_GNO_BRNO": "", "ODNO": "",
                "CCLD_DVSN": "00", "INQR_DVSN": "00", "INQR_DVSN_1": "", "INQR_DVSN_3": "00",
                "EXCG_ID_DVSN_CD": "", "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk,
            }
            resp = self._transport.request(
                method="GET", path=_DAILY_CCLD_PATH, tr_id=_DAILY_CCLD_TR[self._environment],
                params=params, idempotent=True,  # 읽기 -- 타임아웃에 재시도해도 안전
            )
            if not resp.ok:
                raise KisError(
                    f"재조회(일별체결조회) 실패: {resp.msg1}",
                    rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
                )
            page = resp.body.get("output1")
            rows.extend(page if isinstance(page, list) else [])  # 리스트 아니면(매핑 등) 무시
            ctx_nk = str(resp.body.get("ctx_area_nk100", "")).strip()
            ctx_fk = str(resp.body.get("ctx_area_fk100", "")).strip()
            if not ctx_nk:  # 연속조회 키 없음 -> 마지막 페이지
                break
        else:
            # 상한까지 갔는데 연속조회가 남음 -> 부분 스캔을 '전부'로 오인하면 다중일치 가드가
            # 무력화된다(확정 불가). fail-closed.
            raise KisError(
                f"재조회 스캔이 {_MAX_RECONCILE_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
                f"-- 부분 스캔으로 확정하지 않는다. 재시도하거나 수동 확인하라."
            )
        return rows


def _matching_rows(rows: list[Mapping[str, Any]], fingerprint: tuple[str, ...]) -> list[Mapping[str, Any]]:
    """일별체결조회 행들 중 요청 지문과 맞는 것만(순수). 종목+매매구분+주문구분+수량,
    그리고 지정가면 단가까지 비교해 무관한 동일수량 주문의 오귀속을 줄인다."""
    symbol, side, order_type = fingerprint[0], fingerprint[1], fingerprint[2]
    quantity = Decimal(fingerprint[3])
    limit_price = Decimal(fingerprint[4]) if fingerprint[4] else None  # 루프 밖에서 1회 파싱
    want_side = _SIDE_CODE[side]
    want_dvsn = _ORD_DVSN.get(order_type)
    matched = []
    for row in rows:
        if str(row.get("pdno", "")) != symbol:
            continue
        if str(row.get("sll_buy_dvsn_cd", "")) != want_side:
            continue
        if want_dvsn is not None and str(row.get("ord_dvsn_cd", "")) not in ("", want_dvsn):
            continue
        if _parse_decimal(row.get("ord_qty")) != quantity:
            continue
        # 지정가 주문은 단가가 **있고 같아야** 한다(fail-closed) -- 단가 없는 행을 통과시키면
        # 무관한 동일수량 주문을 우리 것으로 오귀속할 수 있다. 단가 없으면 제외(안전 방향).
        if limit_price is not None:
            row_price = row.get("ord_unpr")
            if row_price in (None, "") or _parse_decimal(row_price) != limit_price:
                continue
        matched.append(row)
    return matched


def _market_or_limit_order(
    symbol: str, side: Side, quantity: object, limit_price: object | None,
    time_in_force: TimeInForce, exchange: str, client_order_id: str | None,
) -> Order:
    """``limit_price`` 유무로 시장가/지정가 Order 를 만든다(buy/sell 편의용)."""
    if limit_price is None:
        return Order.market(symbol, side=side, quantity=quantity, time_in_force=time_in_force,
                            exchange=exchange, client_order_id=client_order_id)
    return Order.limit(symbol, side=side, quantity=quantity, limit_price=limit_price,
                      time_in_force=time_in_force, exchange=exchange, client_order_id=client_order_id)


def _execution_report_from_daily_row(
    client_order_id: str, fingerprint: tuple[str, ...], row: Mapping[str, Any]
) -> ExecutionReport:
    ordered = _parse_decimal(row.get("ord_qty"))
    filled = _parse_decimal(row.get("tot_ccld_qty"))
    rejected = _parse_decimal(row.get("rjct_qty"))
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
    avg = _parse_decimal(row.get("avg_prvs"))
    return ExecutionReport(
        client_order_id=client_order_id,
        order_id=str(row.get("odno")) if row.get("odno") else None,
        symbol=fingerprint[0],
        side=fingerprint[1],
        status=status,
        filled_quantity=filled,
        average_price=avg if filled > 0 and avg > 0 else None,
        submitted_at=datetime.now(_KST),
        raw=row,
    )


def _extract_output_mapping(body: Mapping[str, Any]) -> Mapping[str, Any]:
    out = body.get("output", body)
    return out if isinstance(out, Mapping) else {}


def _parse_decimal(value: object) -> Decimal:
    """KIS 문자열 수치를 Decimal 로. 공백/None 은 0(KIS 빈 필드).

    값이 **있는데** 파싱 실패면 :class:`KisError` 로 fail-closed 한다 -- 신뢰 못 할 숫자를
    0으로 조작하면 재조회가 체결을 '미체결'로, 주문을 '미접수'로 오판해 이중체결을 부른다.
    """
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as err:
        raise KisError(f"재조회 응답의 수치 파싱 실패: {value!r}") from err


def _plain(value: Decimal | None) -> str:
    """주문 단가/수량을 KIS 와이어 정본 문자열로(``None`` -> "0")."""
    if value is None:
        return "0"
    return format_wire_decimal(value)
