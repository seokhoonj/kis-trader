"""퇴직연금(IRP; DC가입자 55는 API 세션 불가, 연금저축 22는 별개 사적연금) 계좌 조회 (내부) -- 예수금·매수가능·잔고·미체결.

일반 위탁계좌 조회(:mod:`kis_trader._domestic.account`)와 별개인 퇴직연금 전용 엔드포인트
(`/uapi/domestic-stock/v1/trading/pension/...`)를 감싼다. 전부 **모의투자 미지원**이며, 계좌
상품코드가 퇴직연금 계좌여야 KIS가 정상 응답한다(아니면 KIS 오류를 그대로 전달).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .._internal._response import _require_mapping_rows
from .._internal._wire import field_decimal_or_zero, format_wire_decimal
from ..domestic.entities.balance import Position
from ..errors import KISError, KISUsageError
from ..order import Side
from ..transport import Environment, Transport
from .entities import (
    PensionBalance,
    PensionBuyableAmount,
    PensionDeposit,
    PensionOrder,
    PensionPresentBalance,
)

if TYPE_CHECKING:
    from .._literals import Numeric

_DEPOSIT_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-deposit"
_DEPOSIT_TR = "TTTC0506R"
_BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-psbl-order"
_BUYABLE_TR = "TTTC0503R"
_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-balance"
_BALANCE_TR = "TTTC2208R"
_PRESENT_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-present-balance"
_PRESENT_BALANCE_TR = "TTTC2202R"
_ORDERS_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-daily-ccld"
_ORDERS_TR = "TTTC2210R"  # KRX+NXT/SOR (구 KRX전용 TTTC2201R)
#: 연속조회 페이지 상한. 닿으면 fail-closed.
_MAX_PAGES = 100
#: KIS 연속조회 종료 센티널. 일부 조회(주문내역 등)는 tr_cont 를 F/M 로 유지한 채 연속키를 이 값으로
#: 돌려 "더 없음"을 알린다 -- 이 키로는 재요청하지 않는다(같은 페이지 재조회/이중집계 방지).
_CONTINUATION_END = "^^"
_SIDE: dict[str, Side] = {"01": "sell", "02": "buy"}


def _side_from_code(code: object) -> Side:
    """벤더 매매구분코드(01 매도 / 02 매수)를 방향으로 -- 알 수 없는 코드는 fail-closed(:class:`KISError`).
    ``side=""`` 로 뭉개면 buy/sell 어느 쪽도 아닌 주문 레코드가 새어 이후 오귀속/오매칭을 부른다."""
    text = str(code or "").strip()
    try:
        return _SIDE[text]
    except KeyError:
        raise KISError(f"알 수 없는 매매구분코드: {text!r} (01 매도 / 02 매수만 유효).") from None


def fetch_deposit(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> PensionDeposit:
    """퇴직연금 예수금 요약. **모의투자 미지원**."""
    _reject_demo(environment, what="퇴직연금 예수금조회(pension/inquire-deposit)")
    params = {"CANO": cano, "ACNT_PRDT_CD": product_code, "ACCA_DVSN_CD": "00"}
    output = _request_output(transport, path=_DEPOSIT_PATH, tr_id=_DEPOSIT_TR, params=params, label="예수금")
    return PensionDeposit(
        total_deposit=_decimal_or_zero(output, "dnca_tota"),
        next_day_estimated_settlement_amount=_decimal_or_zero(output, "nxdy_excc_amt"),
        next_day_settlement_amount=_decimal_or_zero(output, "nxdy_sttl_amt"),
        second_day_settlement_amount=_decimal_or_zero(output, "nx2_day_sttl_amt"),
        _raw=output,
    )


def fetch_buyable_amount(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    symbol: str, limit_price: Numeric | None = None,
) -> PensionBuyableAmount:
    """퇴직연금 매수가능 여력. ``limit_price`` 없으면 시장가 기준. **모의투자 미지원**."""
    _reject_demo(environment, what="퇴직연금 매수가능조회(pension/inquire-psbl-order)")
    unit_price = _format_order_unit_price(limit_price)
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code, "PDNO": symbol,
        "ACCA_DVSN_CD": "00", "CMA_EVLU_AMT_ICLD_YN": "N",
        "ORD_DVSN": "00" if limit_price is not None else "01",
        "ORD_UNPR": unit_price or "0",
    }
    output = _request_output(transport, path=_BUYABLE_PATH, tr_id=_BUYABLE_TR, params=params, label="매수가능")
    return PensionBuyableAmount(
        symbol=symbol,
        orderable_cash=_decimal_or_zero(output, "ord_psbl_cash"),
        reusable_cash=_decimal_or_zero(output, "ruse_psbl_amt"),
        calc_unit_price=_decimal_or_zero(output, "psbl_qty_calc_unpr"),
        max_buyable_amount=_decimal_or_zero(output, "max_buy_amt"),
        max_buyable_quantity=_decimal_or_zero(output, "max_buy_qty"),
        _raw=output,
    )


def fetch_balance(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> PensionBalance:
    """퇴직연금 잔고(보유종목 + 예수금 기준 요약). **모의투자 미지원**."""
    _reject_demo(environment, what="퇴직연금 잔고조회(pension/inquire-balance)")
    rows, summary = _walk_holdings(
        transport, cano=cano, product_code=product_code, path=_BALANCE_PATH, tr_id=_BALANCE_TR,
        extra={"ACCA_DVSN_CD": "00", "INQR_DVSN": "00"}, label="잔고",   # ACCA_DVSN_CD(적립금구분) 필수
    )
    if summary is None:
        raise KISError("퇴직연금 잔고 응답에 계좌 요약(output2)이 없다.")
    return PensionBalance(
        positions=tuple(_parse_position(row, sellable_key="ord_psbl_qty", pnl_rate_key="evlu_erng_rt")
                        for row in rows if str(row.get("pdno", "")).strip()),
        total_deposit=_decimal_or_zero(summary, "dnca_tot_amt"),
        next_day_estimated_settlement_amount=_decimal_or_zero(summary, "nxdy_excc_amt"),
        prior_settlement=_decimal_or_zero(summary, "prvs_rcdl_excc_amt"),
        securities_evaluation=_decimal_or_zero(summary, "scts_evlu_amt"),
        total_evaluation=_decimal_or_zero(summary, "tot_evlu_amt"),
        today_buy_amount=_decimal_or_zero(summary, "thdt_buy_amt"),
        today_sell_amount=_decimal_or_zero(summary, "thdt_sll_amt"),
        _raw=summary,
    )


def fetch_present_balance(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> PensionPresentBalance:
    """퇴직연금 체결기준잔고(보유종목 + 손익 요약). **모의투자 미지원**."""
    _reject_demo(environment, what="퇴직연금 체결기준잔고(pension/inquire-present-balance)")
    rows, summary = _walk_holdings(
        transport, cano=cano, product_code=product_code, path=_PRESENT_BALANCE_PATH, tr_id=_PRESENT_BALANCE_TR,
        extra={"INQR_DVSN": "00"}, label="체결기준잔고", summary_is_list=True,
    )
    if summary is None:
        raise KISError("퇴직연금 체결기준잔고 응답에 요약(output2)이 없다.")
    return PensionPresentBalance(
        positions=tuple(_parse_position(row, sellable_key="slpsb_qty", pnl_rate_key="evlu_pfls_rt")
                        for row in rows if str(row.get("pdno", "")).strip()),
        total_purchase_amount=_decimal_or_zero(summary, "pchs_amt_smtl_amt"),
        total_evaluation_amount=_decimal_or_zero(summary, "evlu_amt_smtl_amt"),
        total_unrealized_pnl=_decimal_or_zero(summary, "evlu_pfls_smtl_amt"),
        total_realized_pnl=_decimal_or_zero(summary, "trad_pfls_smtl"),
        today_total_pnl=_decimal_or_zero(summary, "thdt_tot_pfls_amt"),
        return_percent=_decimal_or_zero(summary, "pftrt"),
        _raw=summary,
    )


def fetch_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    only_unfilled: bool = False,
) -> list[PensionOrder]:
    """퇴직연금 당일 주문(체결/미체결). ``only_unfilled`` 이면 미체결만. **모의투자 미지원**."""
    _reject_demo(environment, what="퇴직연금 미체결내역(pension/inquire-daily-ccld)")
    rows, _summary = _walk_holdings(
        transport, cano=cano, product_code=product_code, path=_ORDERS_PATH, tr_id=_ORDERS_TR,
        extra={
            "USER_DVSN_CD": "%%", "SLL_BUY_DVSN_CD": "00",
            "CCLD_NCCS_DVSN": "02" if only_unfilled else "%%", "INQR_DVSN_3": "00",
        },
        label="주문내역", output_key="output",
    )
    return [_parse_order(row) for row in rows if str(row.get("odno", "")).strip()]


def _walk_holdings(
    transport: Transport, *, cano: str, product_code: str, path: str, tr_id: str,
    extra: Mapping[str, str], label: str,
    output_key: str = "output1", summary_is_list: bool = False,
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any] | None]:
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        params = {"CANO": cano, "ACNT_PRDT_CD": product_code, **extra,
                  "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk}
        resp = transport.request(
            method="GET", path=path, tr_id=tr_id, params=params, idempotent=True, tr_cont=tr_cont
        )
        if not resp.ok:
            raise KISError(
                f"퇴직연금 {label} 조회 실패: {resp.msg1}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        if summary is None:
            summary = _extract_summary(resp.body, is_list=summary_is_list)
        rows.extend(_require_mapping_rows(output_key, resp))
        if resp.tr_cont not in ("F", "M"):
            break
        next_nk = str(resp.body.get("ctx_area_nk100") or "").strip()
        # KIS 가 tr_cont 를 계속 F/M 로 주면서 연속키를 진전시키지 않는 경우가 있다: 명시적 종료
        # 센티널("^^")을 주거나, 같은 키를 반복(0행)하거나, 빈 키를 준다. 어느 쪽이든 그 키로 재요청하면
        # 같은 페이지를 다시 받아(이중집계) 또는 무한 루프가 되므로 여기서 종료한다.
        if not next_nk or next_nk == ctx_nk or next_nk == _CONTINUATION_END:
            break
        ctx_nk = next_nk
        ctx_fk = str(resp.body.get("ctx_area_fk100") or "").strip()
        tr_cont = "N"
    else:
        raise KISError(
            f"퇴직연금 {label} 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다."
        )
    return rows, summary


def _extract_summary(body: Mapping[str, Any], *, is_list: bool) -> Mapping[str, Any] | None:
    summary = body.get("output2")
    if is_list:
        first = summary[0] if isinstance(summary, list) and summary else None
        return first if isinstance(first, Mapping) else None
    return summary if isinstance(summary, Mapping) else None


def _parse_position(row: Mapping[str, Any], *, sellable_key: str, pnl_rate_key: str) -> Position:
    return Position(
        symbol=str(row.get("pdno", "")).strip(),
        security_name=str(row.get("prdt_name", "")).strip(),
        currency="KRW",
        quantity=_decimal_or_zero(row, "hldg_qty"),
        sellable_quantity=_decimal_or_zero(row, sellable_key),
        average_purchase_price=_decimal_or_zero(row, "pchs_avg_pric"),
        purchase_amount=_decimal_or_zero(row, "pchs_amt"),
        current_price=_decimal_or_zero(row, "prpr"),
        market_value=_decimal_or_zero(row, "evlu_amt"),
        unrealized_pnl=_decimal_or_zero(row, "evlu_pfls_amt"),
        unrealized_pnl_percent=_decimal_or_zero(row, pnl_rate_key),
        _raw=row,
    )


def _parse_order(row: Mapping[str, Any]) -> PensionOrder:
    return PensionOrder(
        order_id=str(row.get("odno", "")).strip(),
        original_order_id=str(row.get("orgn_odno", "")).strip(),
        branch_number=str(row.get("ord_gno_brno", "")).strip(),
        side=_side_from_code(row.get("sll_buy_dvsn_cd")),
        order_type=str(row.get("ord_dvsn_name", "")).strip(),
        symbol=str(row.get("pdno", "")).strip(),
        name=str(row.get("prdt_name", "")).strip(),
        quantity=_decimal_or_zero(row, "ord_qty"),
        filled_quantity=_decimal_or_zero(row, "tot_ccld_qty"),
        unfilled_quantity=_decimal_or_zero(row, "nccs_qty"),
        order_price=_decimal_or_zero(row, "ord_unpr"),
        average_purchase_price=_decimal_or_zero(row, "pchs_avg_pric"),
        order_time=_parse_hhmmss(row.get("ord_tmd")),
        _raw=row,
    )


def _parse_hhmmss(value: object) -> time | None:
    text = str(value or "").strip()
    if len(text) != 6 or not text.isdigit():
        return None
    hour, minute, second = int(text[0:2]), int(text[2:4]), int(text[4:6])
    if hour > 23 or minute > 59 or second > 59:
        return None
    return time(hour, minute, second)


# --- 공용 ------------------------------------------------------------------
def _request_output(
    transport: Transport, *, path: str, tr_id: str, params: Mapping[str, Any], label: str
) -> Mapping[str, Any]:
    resp = transport.request(method="GET", path=path, tr_id=tr_id, params=dict(params), idempotent=True)
    if not resp.ok:
        raise KISError(
            f"퇴직연금 {label} 조회 실패: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise KISError(
            f"퇴직연금 {label} 응답에 output 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return output


def _reject_demo(environment: Environment, *, what: str) -> None:
    if environment == "paper":
        raise KISUsageError(f"{what}는 모의투자 미지원 -- 실전에서만.")


def _decimal_or_zero(output: Mapping[str, Any], key: str) -> Decimal:
    return field_decimal_or_zero(output.get(key), key)


def _format_order_unit_price(limit_price: object | None) -> str:
    """주문단가를 KIS 와이어 정본으로 -- ``None`` 이면 빈 문자열. 유한 양수 아니면 거부."""
    if limit_price is None:
        return ""
    try:
        price = Decimal(str(limit_price))
    except (ArithmeticError, ValueError) as err:
        raise KISUsageError(f"limit_price 는 숫자여야 한다: {limit_price!r}") from err
    if not price.is_finite() or price <= 0:
        raise KISUsageError(f"limit_price 는 0보다 큰 유한값이어야 한다: {limit_price!r}")
    return format_wire_decimal(price)
