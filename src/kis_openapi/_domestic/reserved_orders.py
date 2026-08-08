"""국내주식 예약주문 엔진 (내부) -- 조회/발주/정정취소.

예약주문은 다음 영업일(또는 지정 기간) 아침 동시호가에 집행되는 예약이라 즉시체결 주문
(:mod:`kis_openapi._domestic.orders`)과 라이프사이클이 다르다(예약순번 rsvn_ord_seq 로 식별,
집행 전 체결 없음). 그래서 즉시주문 안전코어(place/reconcile)를 재사용하지 않고 이 모듈이
예약 전용 흐름을 담는다. 발주/정정취소(뮤테이션)는 별도 슬라이스에서 dedup·무재시도로 추가한다.

KIS URL/TR-id (전부 모의투자 미지원):
- 조회: ``GET .../trading/order-resv-ccnl`` (``CTSC0004R``).
- 발주: ``POST .../trading/order-resv`` (``CTSC0008U``).
- 정정취소: ``POST .../trading/order-resv-rvsecncl`` (취소 ``CTSC0009U`` / 정정 ``CTSC0013U``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .._wire import optional_decimal
from ..errors import KISError, KISUsageError
from ..reserved_order import ReservedOrder
from ..transport import Environment, Transport

_INQUIRE_PATH = "/uapi/domestic-stock/v1/trading/order-resv-ccnl"
_INQUIRE_TR = "CTSC0004R"          # 모의투자 미지원
#: 예약주문 조회 연속조회 페이지 상한. 닿으면 fail-closed.
_MAX_PAGES = 100
_SIDE = {"01": "sell", "02": "buy"}
#: 처리구분 -> PRCS_DVSN_CD. all:전체/processed:처리내역/unprocessed:미처리내역.
_PROCESS_FILTER = {"all": "0", "processed": "1", "unprocessed": "2"}


def fetch_reserved_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str, process: str = "all",
) -> list[ReservedOrder]:
    """예약주문 조회(연속조회 소진까지). ``start``/``end`` 는 예약주문일자 기간(YYYYMMDD),
    ``process`` = all/processed/unprocessed. 유효(취소 안 된) 예약만 준다. **모의투자 미지원**."""
    if environment == "demo":
        raise KISUsageError("예약주문조회(order-resv-ccnl)는 모의투자 미지원 -- 실전에서만.")
    try:
        process_code = _PROCESS_FILTER[process]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 process: {process!r} ({'/'.join(_PROCESS_FILTER)})."
        ) from None
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
        if resp.tr_cont not in ("F", "M"):
            break
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        tr_cont = "N"
    else:
        raise KISError(
            f"예약주문조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다."
        )
    return [_parse_reserved(row) for row in rows if str(row.get("rsvn_ord_seq", "")).strip()]


def _parse_reserved(row: Mapping[str, Any]) -> ReservedOrder:
    return ReservedOrder(
        sequence=str(row.get("rsvn_ord_seq", "")).strip(),
        order_date=_parse_date(row.get("rsvn_ord_ord_dt")),
        received_date=_parse_date(row.get("rsvn_ord_rcit_dt")),
        symbol=str(row.get("pdno", "")).strip(),
        name=str(row.get("kor_item_shtn_name", "")).strip(),
        side=_SIDE.get(str(row.get("sll_buy_dvsn_cd", "")).strip(), ""),
        order_type_name=str(row.get("ord_dvsn_name", "")).strip(),
        reserved_quantity=_decimal_or_zero(row, "ord_rsvn_qty"),
        filled_quantity=_decimal_or_zero(row, "tot_ccld_qty"),
        reserved_price=_decimal_or_zero(row, "ord_rsvn_unpr"),
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
