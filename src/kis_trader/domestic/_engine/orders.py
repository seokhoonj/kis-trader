"""국내주식 주문 실행 엔진 (내부) -- 국내 와이어 빌더 + 재조회.

사용자면(종목 핸들 buy/sell, kis.orders.reconcile)이 이 함수들을 거쳐 주문한다. 지역 중립 안전
코어(발주/정정취소: :func:`~kis_trader._order_core.place`/:func:`~kis_trader._order_core.submit_change`)는
여기서 분리돼 있고, 이 모듈은 그 코어에 ``build_request`` 로 주입할 **국내주식 현금/신용 와이어
빌더**(:func:`_make_order_cash_request`/:func:`make_credit_order_request`/:func:`_make_domestic_change_request`),
사전 리스크 점검(:func:`run_pre_trade_risk`, 참조가가 국내 시세 기반이라 여기 둔다), 그리고 국내
일별체결조회 재조회(:func:`reconcile`)를 담는다. KIS 주문/체결조회 와이어 매핑은 이 안에 갇힌다.

:func:`reconcile` 는 저장된 유효 증권사 주문번호가 있을 때 일별체결내역을 재조회한다.
번호가 없는 초기 요청은 경제 조건만으로 자동 귀속하지 않으며, 스캔이 비거나 모호해도 미접수로
단정하지 않고 in-flight 를 유지한다(부재는 미접수의 증거가 아니다).

KIS URL/TR-ID (국내주식):
- 현금주문: ``POST .../trading/order-cash`` 실전 매수 ``TTTC0012U`` / 매도 ``TTTC0011U``,
  모의 매수 ``VTTC0012U`` / 매도 ``VTTC0011U``.
- 일별체결조회(재조회): ``GET .../trading/inquire-daily-ccld`` 실전 ``TTTC0081R`` / 모의 ``VTTC0081R``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ..._internal._response import _CONTINUATION_END, _MAX_RECONCILE_PAGES
from ..._internal._wire import (
    decimal_or_zero,
    format_wire_decimal,
    normalize_order_number,
    required_decimal,
)
from ..._order_core import _extract_organization_number, _extract_output_mapping
from ...errors import (
    KISError,
    KISUsageError,
    OrderError,
    OrderTimeoutError,
)
from ...instrument import resolve_domestic_quote_exchange
from ...order import (
    ChangeAction,
    ImmediateOrderFingerprint,
    Order,
    WireRequest,
    reject_bad_change_price_shape,
)
from ...report import TERMINAL_ORDER_STATUSES, ExecutionReport, OrderStatus
from ...risk import RiskLimits
from ...store import OrderStore
from ...transport import Environment, Transport, TransportTimeoutError
from . import market_data

_KST = timezone(timedelta(hours=9))

_ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_ORDER_CREDIT_PATH = "/uapi/domestic-stock/v1/trading/order-credit"
# 신용주문 (모의 미지원): 매도 TTTC0051U / 매수 TTTC0052U.
_ORDER_CREDIT_TR = {"sell": "TTTC0051U", "buy": "TTTC0052U"}
_DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
# (environment, side) -> tr_id
_ORDER_CASH_TR = {
    ("real", "buy"):  "TTTC0012U", ("real", "sell"):  "TTTC0011U",
    ("paper", "buy"): "VTTC0012U", ("paper", "sell"): "VTTC0011U",
}
_DAILY_CCLD_TR = {"real": "TTTC0081R", "paper": "VTTC0081R"}
_CHANGE_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
_CHANGE_TR = {"real": "TTTC0013U", "paper": "VTTC0013U"}
# (base, time_in_force) -> KIS ORD_DVSN. base = order.division 이 있으면 그것, 없으면 order_type.
# 현금주문 전용(KRX 코드표 KIS 명세 대조). IOC/FOK 는 time_in_force 로 조합하며 지정가/시장가/최유리/
# 중간가에서만 유효하다(조건부/최우선/시간외엔 없어 매핑 부재 -> 거부). 스톱지정가(22)는 day 만.
_ORD_DVSN_MAP = {
    ("limit", "day"): "00", ("limit", "ioc"): "11", ("limit", "fok"): "12",
    ("market", "day"): "01", ("market", "ioc"): "13", ("market", "fok"): "14",
    ("immediate_limit", "day"): "03", ("immediate_limit", "ioc"): "15", ("immediate_limit", "fok"): "16",
    ("conditional_limit", "day"): "02",
    ("priority_limit", "day"): "04",
    ("midpoint", "day"): "21", ("midpoint", "ioc"): "23", ("midpoint", "fok"): "24",
    ("pre_market_close", "day"): "05",
    ("post_market_close", "day"): "06",
    # KRX 애프터마켓(16:00~20:00): 지정가/최유리는 IOC/FOK 조합, 최우선은 day 만.
    ("after_market_limit", "day"): "41", ("after_market_limit", "ioc"): "42", ("after_market_limit", "fok"): "43",
    ("after_market_immediate_limit", "day"): "44", ("after_market_immediate_limit", "ioc"): "45",
    ("after_market_immediate_limit", "fok"): "46",
    ("after_market_priority_limit", "day"): "47",
    # NXT 프리마켓 GTP: 전부 day 만(IOC/FOK 미매핑 -> 거부).
    ("gtp_limit", "day"): "27",
    ("gtp_immediate_limit", "day"): "28",
    ("gtp_priority_limit", "day"): "29",
    ("stop_limit", "day"): "22",
}


def _resolve_ord_dvsn(*, order_type: str, division: str | None, time_in_force: str) -> str | None:
    """주문의 실제 ORD_DVSN 을 정한다 -- base=division(있으면) 아니면 order_type. **place·reconcile·정정
    이 한 리졸버를 공유**해야 division 주문이 세 경로에서 같은 코드로 해석된다(다른 유도를 쓰면 최유리(03)
    를 시장가(01)로 오인해 재확인 불가·오확정·정정 오코드 전송). 미매핑 조합은 ``None``."""
    return _ORD_DVSN_MAP.get((division or order_type, time_in_force))
# our side -> KIS SLL_BUY_DVSN_CD (01 매도, 02 매수)
_SIDE_CODE = {"buy": "02", "sell": "01"}
# 국내 주문 거래소 -> KIS EXCG_ID_DVSN_CD.
# KIS 명세: 미입력=KRX, 모의투자는 KRX 만 가능.
_DOMESTIC_EXCHANGE_CODE = {"KRX": "KRX", "NXT": "NXT", "SOR": "SOR"}


def reconcile(
    transport: Transport, store: OrderStore, client_order_id: str, *,
    cano: str, product_code: str, environment: Environment, force_refresh: bool = False,
) -> ExecutionReport | None:
    """저장된 국내 즉시주문의 증권사 주문번호(ODNO)로 일별체결내역을 재조회한다.

    기존 리포트가 있고 force_refresh 가 아니면 그대로 반환한다. 저장된 유효 증권사 주문번호가
    없으면 경제 조건·날짜·시각의 일치만으로 초기 요청에 귀속하지 않고 None 을 반환한다.
    미확인 기록과 같은 ID의 재전송 차단은 유지되므로 HTS/MTS 등에서 수동 확인해야 한다.

    force_refresh 는 캐시를 건너뛰어 현재 상태를 조회한다. 반환된 행을 저장 ODNO 로 로컬 필터링해
    동일 수량의 다른 주문으로 바인딩하지 않는다. 0건은 미접수로 단정하지 않으며 비종료 prior 는
    None, 종료 prior 는 기존 결과를 유지한다. 다건·조회 실패는 오류이며 미확인 기록을 해제하지
    않는다. 모르는 ID는 KISUsageError다. 저장 ODNO 의 출처나 날짜별 재사용을 보편적으로 증명하는
    계약은 아니며, 레거시 기록의 인과적 출처는 별도로 확인해야 한다."""
    prior, fingerprint = store.snapshot_for(client_order_id)  # 리포트+지문 원자 스냅샷(세대 일관)
    if prior is not None and not force_refresh:
        return prior
    if fingerprint is None:  # 완료도 in-flight 도 아님 -> 모르는 id
        raise KISUsageError(
            f"모르는 client_order_id: {client_order_id!r} (이 계좌로 전송한 적이 없다)."
        )
    if not isinstance(fingerprint, ImmediateOrderFingerprint):  # 즉시주문 reconcile 경로
        raise KISError(
            f"client_order_id {client_order_id!r} 의 지문이 즉시주문이 아니다"
            f"({type(fingerprint).__name__}, 내부 상태 불일치)."
        )
    # 저장된 유효 증권사 주문번호가 없으면 경제 조건이나 날짜만으로 요청을 귀속하지 않는다.
    # 미확인 상태와 재전송 차단을 유지하며 HTS/MTS 등에서 수동 확인한다.
    if prior is None or not (prior.order_id or "").strip().lstrip("0"):
        return None
    try:
        rows = _fetch_daily_orders(transport, fingerprint.symbol, cano=cano,
                                   product_code=product_code, environment=environment)
    except TransportTimeoutError as err:
        raise OrderTimeoutError(
            f"재조회(일별체결조회) 시간초과 -- 주문 {client_order_id} 상태 여전히 불명. "
            f"in-flight 유지, 재전송 금지. 잠시 후 다시 reconcile 하라.",
            client_order_id=client_order_id,
        ) from err
    want_odno = normalize_order_number(prior.order_id) if prior is not None and prior.order_id else ""
    if want_odno:
        # 저장된 주문번호로 자기 행만 찾는다. 초기 번호 없는 요청은 조회 전에 반환한다.
        matches = [row for row in rows if normalize_order_number(str(row.get("odno", ""))) == want_odno]
    else:
        matches = []
    if len(matches) > 1:
        raise KISError(
            f"주문 {client_order_id} 의 지문과 일치하는 당일 주문이 {len(matches)}건이라 "
            f"자동 확정 불가하다(KIS가 client_order_id를 돌려주지 않음). 수동 확인이 필요하다."
        )
    if not matches:  # 0건: 미접수인지 반영 지연인지 당일창 밖인지 단정 불가.
        # prior 가 종료상태(체결/취소/거부/만료)면 재스캔으로 바뀔 수 없으니 그 확정 리포트를 돌려준다
        # -- 당일창 밖(어제 완료) 주문의 refresh 재조회가 '미해소'로 퇴행하지 않게. 비종료 prior 는
        # 미확정 유지(None) -- 접수직후 NEW 스냅샷을 체결로 둔갑시키지 않는다.
        if prior is not None and prior.status in TERMINAL_ORDER_STATUSES:
            return prior
        return None
    report = _execution_report_from_daily_row(client_order_id, fingerprint, matches[0])
    if not report.is_terminal and normalize_order_number(str(matches[0].get("odno", ""))) == "":
        # 비종료 리포트인데 매칭 행에 쓸 수 있는 ODNO 가 없으면 order_id=None 으로 기록되고, 이후 refresh
        # 재조회가 그 ODNO 로 자기 행을 못 집어 영구 교착(None)에 빠진다 -> 기록하지 않고 in-flight 유지.
        # 종료(체결/취소/거부/만료)는 ODNO 없어도 확정한다 -- 종료 prior 는 refresh 의 0건 분기가 그대로
        # 돌려주므로 교착이 없고, 진짜 체결을 미확정으로 버리면(재전송 유도) 이중체결 위험이 더 크다.
        return None
    if not store.record_if_unchanged(report, fingerprint, expected=(prior, fingerprint)):
        # 읽기~기록 사이에 동시 정정/취소가 이 id 를 새 ODNO 로 재바인딩했다 -- 옛 세대 스캔으로 덮어쓰지
        # 않고(그랬다간 취소가 죽은 ODNO 를 겨냥) 현재(새) 상태를 돌려준다.
        return store.report_for(client_order_id)
    return report


def _make_domestic_change_request(
    *, original_report: ExecutionReport, original_fingerprint: ImmediateOrderFingerprint,
    action: ChangeAction, quantity: Decimal, limit_price: Decimal | None,
    cano: str, product_code: str, environment: Environment,
) -> WireRequest:
    # 지정가 전용 자산이라 가격형상 규칙을 공유 헬퍼로 강제한다(정정=>가격>0, 취소=>가격 없음).
    reject_bad_change_price_shape(action, limit_price)
    # 영속되는 리포트 필드를 우선 쓰고(재기동 후에도 유효), 없으면 미영속 _raw 에서 뽑는다
    # (구버전 레코드 하위호환). 둘 다 없으면 대상 식별 불가라 fail-closed.
    organization_number = original_report.organization_number or _extract_organization_number(
        _extract_output_mapping(original_report._raw)
    )
    if not organization_number:
        raise KISUsageError(
            "원주문 리포트에 한국거래소전송주문조직번호가 없어 정정·취소할 수 없다."
        )
    order_division = _resolve_ord_dvsn(
        order_type=original_fingerprint.order_type, division=original_fingerprint.division,
        time_in_force=original_fingerprint.time_in_force,
    )
    if order_division is None:
        raise KISUsageError("원주문의 주문구분을 정정·취소 와이어로 변환할 수 없다.")
    # 부분 취소가 조용히 전량 취소가 되지 않게 한다. QTY_ALL_ORD_YN="Y" 는 ORD_QTY 를 무시하고
    # 잔량 전부를 취소하므로, 사용자가 부분 수량을 준 취소(change_order 가 quantity 를 그대로 전달)에는
    # "N"+ORD_QTY 로 그 수량만 취소한다. 전량 취소(quantity 미지정 -> change_order 가 잔량 전체로 해소)는
    # "Y" 로 KIS 가 실제 잔량을 취소하게 둔다(스냅샷과 실잔량이 어긋나도 강건). 정정(modify)은 잔량
    # 재지정이라 항상 "N"+ORD_QTY.
    remaining_quantity = Decimal(original_fingerprint.quantity) - original_report.filled_quantity
    is_full_cancel = action == "cancel" and quantity >= remaining_quantity
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
            if limit_price is None and original_fingerprint.limit_price
            else "0" if limit_price is None else format_wire_decimal(limit_price)
        ),
        "CNDT_PRIC": original_fingerprint.stop_price or "0",
        "QTY_ALL_ORD_YN": "Y" if is_full_cancel else "N",
        "EXCG_ID_DVSN_CD": _DOMESTIC_EXCHANGE_CODE[original_fingerprint.exchange],
    }
    return WireRequest("POST", _CHANGE_PATH, _CHANGE_TR[environment], body)


# --- 사전 리스크 한도 ------------------------------------------------------
def run_pre_trade_risk(transport: Transport, order: Order, risk: RiskLimits) -> None:
    """리스크 한도를 점검한다. 참조가(현재가)가 필요하면 시세를 조회해 넘긴다 -- 조회 실패는
    잡지 않고 그대로 올린다(fail-closed: 한도를 확인 못 하면 주문을 보내지 않는다).

    place(발주)와 정정(modify) 양쪽이 쓴다 -- 정정은 새 수량/가격으로 재구성한 주문을 넘긴다."""
    reference_price = None
    if risk.needs_reference_price(order):
        quote = market_data.fetch_quote(
            transport, symbol=order.symbol, market=resolve_domestic_quote_exchange(order.symbol)
        )
        reference_price = quote.current_price
    risk.check(order, reference_price=reference_price)


# --- 와이어 매핑(국내 현금주문) --------------------------------------------
def _make_order_cash_request(
    order: Order, *, cano: str, product_code: str, environment: Environment
) -> WireRequest:
    if (order.region, order.asset_class) != ("domestic", "stock"):
        raise NotImplementedError(
            "국내 주식 빌더에 다른 주문 lane이 들어왔다"
            f"(region={order.region!r}, asset_class={order.asset_class!r})."
        )
    # ORD_DVSN 은 리졸버로 정한다(place·reconcile·정정 공용). 미매핑 조합(예: 조건부+ioc, 스톱, gtc)은
    # 조용히 day/지정가로 바꾸지 않고 fail-closed -- 사용자의 잘못된 인자 조합이라 KISUsageError.
    order_division = _resolve_ord_dvsn(
        order_type=order.order_type, division=order.division, time_in_force=order.time_in_force
    )
    if order_division is None:
        raise KISUsageError(
            f"지원하지 않는 주문구분/TIF 조합이다(division/order_type={order.division or order.order_type!r}, "
            f"time_in_force={order.time_in_force!r})."
        )
    # 모의투자는 KRX 거래소만 제공(KIS 명세) -- NXT/SOR 주문은 demo 에서 와이어 전 fail-closed.
    if environment == "paper" and order.exchange != "KRX":
        raise KISUsageError(f"모의투자는 KRX 거래소만 지원한다(exchange={order.exchange!r}).")
    tr_id = _ORDER_CASH_TR[(environment, order.side)]
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "PDNO": order.symbol,
        "ORD_DVSN": order_division,
        "ORD_QTY": _format_optional_wire_decimal(order.quantity),
        "ORD_UNPR": "0" if order.limit_price is None else _format_optional_wire_decimal(order.limit_price),
        "CNDT_PRIC": "0" if order.stop_price is None else _format_optional_wire_decimal(order.stop_price),
        "EXCG_ID_DVSN_CD": _DOMESTIC_EXCHANGE_CODE[order.exchange],
    }
    return WireRequest("POST", _ORDER_CASH_PATH, tr_id, body)


def make_credit_order_request(
    order: Order, *, cano: str, product_code: str, environment: Environment
) -> WireRequest:
    """국내 신용(융자/대주) 주문 와이어. 안전 코어(place)가 ``build_request`` 로 주입해 쓴다 --
    현금주문과 같은 즉시체결·ODNO 응답이라 dedup/재시도금지/reconcile 은 그대로 공유된다.

    **모의투자 미지원**, **국내 주식만**. ORD_DVSN 은 현금주문과 같은 ``_resolve_ord_dvsn`` 을 공유해
    애프터마켓(``after_market_*``)/GTP(``gtp_*``) 주문구분을 지원한다(공지가 신용 ORD_DVSN 에 41~47·27~29
    추가). 일반 신용(division 없음)은 지정가/시장가·day 만. credit_type/loan_date 는 :class:`Order`
    생성 시점에 검증·확정되므로(신규=오늘, 상환=대상 대출일자), 이 빌더는 주문의 순수 함수다."""
    if environment == "paper":
        raise KISUsageError("신용주문(order-credit)은 모의투자 미지원 -- 실전에서만.")
    if (order.region, order.asset_class) != ("domestic", "stock"):
        raise KISUsageError(
            "신용주문은 국내 주식만 지원한다"
            f"(region={order.region!r}, asset_class={order.asset_class!r})."
        )
    if order.credit_type is None or order.loan_date is None:  # Order 가 보장 -- 라우팅 방어
        raise OrderError("신용주문 빌더에 credit_type/loan_date 없는 주문이 들어왔다(라우팅 오류).")
    # ORD_DVSN 은 현금·정정과 같은 리졸버로(애프터마켓/GTP division + tif 조합 지원). 미매핑 조합은 거부.
    order_division = _resolve_ord_dvsn(
        order_type=order.order_type, division=order.division, time_in_force=order.time_in_force
    )
    if order_division is None:
        raise KISUsageError(
            f"지원하지 않는 신용 주문구분/TIF 조합이다(division/order_type="
            f"{order.division or order.order_type!r}, tif={order.time_in_force!r})."
        )
    # 일반 신용(division 없음)은 day·지정가/시장가 만 -- IOC/FOK 는 애프터마켓 지정가/최유리 division 에서만.
    if order.division is None and order.time_in_force != "day":
        raise KISUsageError(
            f"일반 신용주문은 day 만 지원한다(IOC/FOK 는 애프터마켓 지정가/최유리에서만). "
            f"tif={order.time_in_force!r}"
        )
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "PDNO": order.symbol,
        "SLL_TYPE": "",                       # 공란(KIS 명세 지시)
        "CRDT_TYPE": order.credit_type,
        "LOAN_DT": order.loan_date,
        "ORD_DVSN": order_division,
        "ORD_QTY": _format_optional_wire_decimal(order.quantity),
        "ORD_UNPR": "0" if order.order_type == "market" else _format_optional_wire_decimal(order.limit_price),
        "RSVN_ORD_YN": "N",
        "EXCG_ID_DVSN_CD": _DOMESTIC_EXCHANGE_CODE[order.exchange],
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
        if not isinstance(page, (list, tuple)):
            # 성공 응답인데 output1 이 리스트가 아니면 빈 결과로 오인하지 않는다 -- '미접수'로 단정하면
            # 이중체결 위험. fail-closed 로 올려 재시도/수동확인을 유도한다.
            raise KISError(
                "재조회(일별체결조회) 응답의 output1 이 리스트가 아니다 -- 부분/오응답으로 확정하지 않는다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        prev_nk = ctx_nk
        ctx_nk = str(resp.body.get("ctx_area_nk100") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk100") or "").strip()
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
    rows: list[Mapping[str, Any]], fingerprint: ImmediateOrderFingerprint
) -> list[Mapping[str, Any]]:
    """일별체결조회 행 중 요청 지문과 맞는 것만(순수). 종목+매매구분+주문구분+수량, 지정가면
    단가까지, 스톱지정가면 조건가격까지 비교해 무관한 동일수량 주문의 오귀속을 줄인다. 신용/현금은 행의 대출일자(loan_dt)로
    가른다 -- 안 그러면 같은 종목·수량·가격의 현금 체결이 미접수 신용주문을 phantom 확정할 수 있다."""
    symbol, side, order_type = fingerprint.symbol, fingerprint.side, fingerprint.order_type
    quantity = Decimal(fingerprint.quantity)
    limit_price = Decimal(fingerprint.limit_price) if fingerprint.limit_price else None
    want_side = _SIDE_CODE[side]
    # reconcile 도 place 와 같은 리졸버로 ORD_DVSN 을 유도해야 division 주문이 자기 행에 매칭된다
    # (order_type 만으로 유도하면 최유리(03)를 01 로 찾아 무관한 시장가 행을 오확정한다).
    want_dvsn = _resolve_ord_dvsn(
        order_type=order_type, division=fingerprint.division, time_in_force=fingerprint.time_in_force
    )
    want_excg = _DOMESTIC_EXCHANGE_CODE[fingerprint.exchange]
    want_loan_date = fingerprint.loan_date        # 신용이면 대출일자, 현금이면 ""
    matched = []
    for row in rows:
        if str(row.get("pdno", "")) != symbol:
            continue
        if str(row.get("sll_buy_dvsn_cd", "")) != want_side:
            continue
        # division 이 명시된 주문(최유리/최우선/조건부)은 행의 주문구분이 정확히 일치해야 한다 -- 빈
        # ord_dvsn_cd 행은 어떤 구분인지 알 수 없어(같은 종목·수량의 다른 구분 체결일 수 있음) 오확정을
        # 부르므로 매칭하지 않는다. division 미지정(일반 지정가/시장가)일 때만 빈 값을 관용한다(종전과
        # 동일 -- 행이 구분을 안 채우는 경우 대비).
        if want_dvsn is not None:
            allowed = (want_dvsn,) if fingerprint.division else ("", want_dvsn)
            if str(row.get("ord_dvsn_cd", "")) not in allowed:
                continue
        # 보드 구분: KRX vs NXT 는 다른 거래소 주문 -- 행의 거래소(excg_id_dvsn_cd)가 지문 보드와 정확히
        # 같아야 한다(같은 종목·수량·주문구분의 KRX 체결이 미접수 NXT 주문을 phantom 확정하는 것 방지).
        # 거래소 필드는 실응답에 실재하므로(라이브 확인) blank 는 관용하지 않고 비매칭으로 본다(오확정 방지).
        if str(row.get("excg_id_dvsn_cd", "")) != want_excg:
            continue
        # 신용/현금 구분: 신용주문 지문(loan_date != "")은 행의 loan_dt 가 그 대출일자와 같아야,
        # 현금주문 지문("")은 행에 대출일자가 없어야 매칭한다(cash<->credit 오확정 방지).
        if _normalize_loan_date(row.get("loan_dt")) != want_loan_date:
            continue
        if decimal_or_zero(row.get("ord_qty")) != quantity:
            continue
        # 지정가 주문은 단가가 있고 같아야 한다(fail-closed) -- 단가 없는 행 통과 시 무관한
        # 동일수량 주문을 오귀속할 수 있다. 단가 없으면 제외(안전 방향).
        if limit_price is not None:
            row_price = row.get("ord_unpr")
            if row_price in (None, "") or decimal_or_zero(row_price) != limit_price:
                continue
        if fingerprint.order_type == "stop_limit":
            try:
                trigger = required_decimal(row.get("stpm_cndt_pric"), "stpm_cndt_pric")
            except KISError:
                continue
            if trigger != Decimal(fingerprint.stop_price):
                continue
        matched.append(row)
    return matched


def _execution_report_from_daily_row(
    client_order_id: str, fingerprint: ImmediateOrderFingerprint, row: Mapping[str, Any]
) -> ExecutionReport:
    ordered = decimal_or_zero(row.get("ord_qty"))
    filled = decimal_or_zero(row.get("tot_ccld_qty"))
    rejected = decimal_or_zero(row.get("rjct_qty"))
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
    avg = decimal_or_zero(row.get("avg_prvs"))
    return ExecutionReport(
        client_order_id=client_order_id,
        order_id=str(row.get("odno")) if row.get("odno") else None,
        symbol=fingerprint.symbol,
        side=fingerprint.side,
        status=status,
        filled_quantity=filled,
        average_price=avg if filled > 0 and avg > 0 else None,
        recorded_at=datetime.now(_KST),
        organization_number=_extract_organization_number(row),
        _raw=row,
    )


def _normalize_loan_date(value: object) -> str:
    """일별체결조회 행의 대출일자를 지문의 loan_date 정본과 맞춘다 -- 공백/0채움("00000000")은
    '대출 없음(현금)'을 뜻하므로 ``""`` 로 본다."""
    text = str(value or "").strip()
    return "" if text.strip("0") == "" else text


def _format_optional_wire_decimal(value: Decimal | None) -> str:
    """주문 단가/수량을 KIS 와이어 정본 문자열로(``None`` -> "0")."""
    if value is None:
        return "0"
    return format_wire_decimal(value)
