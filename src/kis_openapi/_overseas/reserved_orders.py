"""미국 해외주식 예약주문 엔진 (내부) -- 조회(+발주/취소는 별 슬라이스).

미국 예약주문은 정규장 시작 전에 걸어두는 예약으로, 해외예약주문번호(ovrs_rsvn_odno)로 식별한다.
아시아(일/중/홍/베) 예약은 request/response 규격이 다른 별 프로토콜(TTTS3013U/TTTS3014R)이라 여기서
다루지 않는다. 전부 **모의투자 미지원**.

KIS URL/TR-id:
- 조회: ``GET .../trading/order-resv-list`` (미국 ``TTTT3039R``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .._wire import optional_decimal
from ..errors import KISError, KISUsageError
from ..overseas_items import OverseasReservedOrder
from ..transport import Environment, Transport

_LIST_PATH = "/uapi/overseas-stock/v1/trading/order-resv-list"
_LIST_TR = "TTTT3039R"             # 미국, 모의투자 미지원
#: 연속조회 페이지 상한. 닿으면 fail-closed.
_MAX_PAGES = 100
_SIDE = {"01": "sell", "02": "buy"}


def fetch_reserved_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str,
) -> list[OverseasReservedOrder]:
    """미국 해외주식 예약주문 조회(연속조회 소진까지). ``start``/``end`` 는 기간(YYYYMMDD).
    거래소/상품유형 공백=미국 전체. **모의투자 미지원**."""
    if environment == "demo":
        raise KISUsageError("해외 예약주문조회(order-resv-list)는 모의투자 미지원 -- 실전에서만.")
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "INQR_STRT_DT": start, "INQR_END_DT": end, "INQR_DVSN_CD": "00",
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
        if resp.tr_cont not in ("F", "M"):
            break
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        tr_cont = "N"
    else:
        raise KISError(
            f"해외 예약주문조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다."
        )
    return [_parse(row) for row in rows if str(row.get("ovrs_rsvn_odno", "")).strip()]


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
