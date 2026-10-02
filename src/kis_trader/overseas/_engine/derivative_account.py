"""해외선물옵션(08) 계좌 조회 (내부) -- 예수금현황/미결제(보유)/주문가능.

사용자면(``kis.account`` -> :class:`~kis_trader.overseas.derivative_account.
OverseasDerivativesAccount`)이 이 함수를 호출한다. 계좌 식별정보(``cano``/``product_code``)와
환경(``environment``)은 세션에서 온다. **모든 조회는 실전 전용**이라 모의(paper)면 와이어 이전에
:class:`KISUsageError` 로 막는다. 금액·수량은 조회 통화의 Decimal(원화 아님).

KIS URL/TR-ID:
- 예수금현황: ``GET .../overseas-futureoption/v1/trading/inquire-deposit`` (``OTFM1411R``, 모의 미지원).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..._internal._response import _fetch_paginated_rows, _raise_if_error, advance_cursor
from ..._internal._wire import field_decimal_or_zero, format_wire_decimal, required_decimal
from ...errors import KISError, KISUsageError
from ...transport import Environment, RawResponse, Transport
from ..entities.derivative_account import (
    OverseasDerivativeDailyOrder,
    OverseasDerivativeDeposit,
    OverseasDerivativeFill,
    OverseasDerivativeFillHistory,
    OverseasDerivativeMargin,
    OverseasDerivativeOrder,
    OverseasDerivativeOrderable,
    OverseasDerivativePNL,
    OverseasDerivativePNLHistory,
    OverseasDerivativePosition,
    OverseasDerivativeTransaction,
)
from ._parse import _MAX_PAGES, _parse_date, _side_from_code

if TYPE_CHECKING:
    from ..._literals import Numeric
    from ...order import Side

_DEPOSIT_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-deposit"
_DEPOSIT_TR = "OTFM1411R"  # 해외선물옵션 예수금현황, 모의투자 미지원

_POSITIONS_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-unpd"
_POSITIONS_TR = "OTFM1412R"  # 해외선물옵션 미결제내역, 모의투자 미지원

_ORDERABLE_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-psamount"
_ORDERABLE_TR = "OTFM3304R"  # 해외선물옵션 주문가능수량, 모의투자 미지원

_MARGIN_PATH = "/uapi/overseas-futureoption/v1/trading/margin-detail"
_MARGIN_TR = "OTFM3115R"  # 해외선물옵션 증거금상세, 모의투자 미지원

_TODAY_ORDERS_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-ccld"
_TODAY_ORDERS_TR = "OTFM3116R"  # 해외선물옵션 당일주문내역, 모의투자 미지원

_DAILY_FILLS_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-daily-ccld"
_DAILY_FILLS_TR = "OTFM3122R"  # 해외선물옵션 일별체결내역, 모의투자 미지원

_DAILY_ORDERS_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-daily-order"
_DAILY_ORDERS_TR = "OTFM3120R"  # 해외선물옵션 일별주문내역, 모의투자 미지원

_PERIOD_PNL_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-period-ccld"
_PERIOD_PNL_TR = "OTFM3118R"  # 해외선물옵션 기간손익, 모의투자 미지원

_PERIOD_TRANS_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-period-trans"
_PERIOD_TRANS_TR = "OTFM3114R"  # 해외선물옵션 기간입출금내역, 모의투자 미지원
#: 매도매수구분코드(SLL_BUY_DVSN_CD): 매수 02 / 매도 01.
_SIDE_TO_SLL_BUY = {"buy": "02", "sell": "01"}


def fetch_deposit(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    currency: str, query_date: str,
) -> OverseasDerivativeDeposit:
    """해외선물옵션 예수금현황(1콜, output 단일 객체). ``currency`` 조회 통화(CRCY_CD),
    ``query_date`` 조회일자(YYYYMMDD). 금액은 그 통화의 Decimal(원화 아님).
    **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 예수금현황(inquire-deposit)은 모의투자 미지원 -- 실전에서만."
        )
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "CRCY_CD": currency, "INQR_DT": query_date,
    }
    resp = transport.request(
        method="GET", path=_DEPOSIT_PATH, tr_id=_DEPOSIT_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise KISError(
            "해외선물옵션 예수금현황 응답에 output 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return OverseasDerivativeDeposit(
        # 외화 계좌 필드는 비어 올 수 있어 '없음=0'(field_decimal_or_zero)으로 읽되, 값이 있는데
        # 파싱 실패면 여전히 예외로 fail-closed 한다.
        currency=str(output.get("crcy_cd", "")).strip() or currency,
        cash_balance=field_decimal_or_zero(output.get("fm_dnca_rmnd"), "fm_dnca_rmnd"),
        total_asset=field_decimal_or_zero(output.get("fm_tot_asst_evlu_amt"), "fm_tot_asst_evlu_amt"),
        unrealized_pnl=field_decimal_or_zero(output.get("fm_fuop_evlu_pfls_amt"), "fm_fuop_evlu_pfls_amt"),
        realized_pnl=field_decimal_or_zero(output.get("fm_lqd_pfls_amt"), "fm_lqd_pfls_amt"),
        brokerage_margin=field_decimal_or_zero(output.get("fm_brkg_mgn_amt"), "fm_brkg_mgn_amt"),
        maintenance_margin=field_decimal_or_zero(output.get("fm_mntn_mgn_amt"), "fm_mntn_mgn_amt"),
        additional_margin=field_decimal_or_zero(output.get("fm_add_mgn_amt"), "fm_add_mgn_amt"),
        risk_rate=field_decimal_or_zero(output.get("fm_risk_rt"), "fm_risk_rt"),
        orderable_amount=field_decimal_or_zero(output.get("fm_ord_psbl_amt"), "fm_ord_psbl_amt"),
        withdrawable_amount=field_decimal_or_zero(output.get("fm_drwg_psbl_amt"), "fm_drwg_psbl_amt"),
        receivable=field_decimal_or_zero(output.get("fm_rcvb_amt"), "fm_rcvb_amt"),
        next_day_deposit=field_decimal_or_zero(output.get("fm_nxdy_dncl_amt"), "fm_nxdy_dncl_amt"),
        option_value=field_decimal_or_zero(output.get("fm_opt_evlu_amt"), "fm_opt_evlu_amt"),
        fee=field_decimal_or_zero(output.get("fm_fee"), "fm_fee"),
        _raw=output,
    )


def fetch_margin_detail(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    currency: str, query_date: str,
) -> OverseasDerivativeMargin:
    """해외선물옵션 증거금상세(1콜, output 단일 객체). ``currency`` 조회 통화(CRCY_CD),
    ``query_date`` 조회일자(YYYYMMDD). 금액은 그 통화의 Decimal(원화 아님). SPAN/EUREX 등 상세
    증거금 내역은 ``_raw`` 로만 노출한다. **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 증거금상세(margin-detail)는 모의투자 미지원 -- 실전에서만."
        )
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "CRCY_CD": currency, "INQR_DT": query_date,
    }
    resp = transport.request(
        method="GET", path=_MARGIN_PATH, tr_id=_MARGIN_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise KISError(
            "해외선물옵션 증거금상세 응답에 output 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return OverseasDerivativeMargin(
        # 외화 증거금 필드는 비어 올 수 있어 '없음=0'(field_decimal_or_zero)으로 읽되, 값이 있는데
        # 파싱 실패면 여전히 예외로 fail-closed 한다.
        currency=str(output.get("crcy_cd", "")).strip() or currency,
        orderable_amount=field_decimal_or_zero(output.get("fm_ord_psbl_amt"), "fm_ord_psbl_amt"),
        brokerage_margin=field_decimal_or_zero(output.get("fm_brkg_mgn_amt"), "fm_brkg_mgn_amt"),
        settlement_brokerage_margin=field_decimal_or_zero(
            output.get("fm_excc_brkg_mgn_amt"), "fm_excc_brkg_mgn_amt"
        ),
        open_margin=field_decimal_or_zero(output.get("fm_ustl_mgn_amt"), "fm_ustl_mgn_amt"),
        maintenance_margin=field_decimal_or_zero(output.get("fm_mntn_mgn_amt"), "fm_mntn_mgn_amt"),
        order_margin=field_decimal_or_zero(output.get("fm_ord_mgn_amt"), "fm_ord_mgn_amt"),
        additional_margin=field_decimal_or_zero(output.get("fm_add_mgn_amt"), "fm_add_mgn_amt"),
        net_risk_applied=str(output.get("acnt_net_risk_mgna_aply_yn", "")).strip(),
        _raw=output,
    )


def fetch_positions(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    fuop: str = "00",
) -> list[OverseasDerivativePosition]:
    """해외선물옵션 미결제내역(보유 종목 전체, 연속조회 소진까지). ``fuop`` 선물옵션구분
    (FUOP_DVSN, 기본 "00" 전체). 금액·수량은 각 행 통화의 Decimal(원화 아님).
    **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 미결제내역(inquire-unpd)은 모의투자 미지원 -- 실전에서만."
        )
    rows = _fetch_paginated_rows(
        transport,
        path=_POSITIONS_PATH, tr_id=_POSITIONS_TR,
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "FUOP_DVSN": fuop,
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        },
        output_key="output", max_pages=_MAX_PAGES, ctx_width=100,
        cap_message=(
            f"해외선물옵션 미결제내역 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        ),
    )
    return _parse_positions(rows)


def _parse_positions(rows: list[Mapping[str, Any]]) -> list[OverseasDerivativePosition]:
    positions: list[OverseasDerivativePosition] = []
    for row in rows:
        symbol = str(row.get("ovrs_futr_fx_pdno", "")).strip()
        if not symbol:  # 상품번호 없는 패딩 행 -- 건너뜀
            continue
        positions.append(
            # 종목별 수치는 빈 값을 0으로 읽되(외화 계좌 필드는 비어 올 수 있음), 값이 있는데 파싱
            # 실패면 여전히 예외. 방향(side)은 종목이 있는 행에서만 코드->buy/sell 로 fail-closed 변환.
            OverseasDerivativePosition(
                symbol=symbol,
                product_type=str(row.get("prdt_type_cd", "")).strip(),
                currency=str(row.get("crcy_cd", "")).strip(),
                side=_side_from_code(row.get("sll_buy_dvsn_cd")),
                quantity=field_decimal_or_zero(row.get("fm_ustl_qty"), "fm_ustl_qty"),
                average_price=field_decimal_or_zero(row.get("fm_ccld_avg_pric"), "fm_ccld_avg_pric"),
                current_price=field_decimal_or_zero(row.get("fm_now_pric"), "fm_now_pric"),
                unrealized_pnl=field_decimal_or_zero(row.get("fm_evlu_pfls_amt"), "fm_evlu_pfls_amt"),
                option_value=field_decimal_or_zero(row.get("fm_opt_evlu_amt"), "fm_opt_evlu_amt"),
                option_unrealized_pnl=field_decimal_or_zero(
                    row.get("fm_otp_evlu_pfls_amt"), "fm_otp_evlu_pfls_amt"
                ),
                liquidatable_quantity=field_decimal_or_zero(row.get("fm_lqd_psbl_qty"), "fm_lqd_psbl_qty"),
                exercise_reserved=str(row.get("ecis_rsvn_ord_yn", "")).strip(),
                _raw=row,
            )
        )
    return positions


def fetch_today_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
) -> list[OverseasDerivativeOrder]:
    """해외선물옵션 당일 주문내역(체결+미체결 전체, 연속조회 소진까지). v1 은 전체를 돌려주므로
    체결여부/매매/선물옵션 구분은 모두 "00"(전체)으로 고정한다. 연속조회 커서는 200폭
    (CTX_AREA_FK200/NK200)이다. 금액·수량은 각 계약 통화의 Decimal(원화 아님).
    **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 당일주문내역(inquire-ccld)은 모의투자 미지원 -- 실전에서만."
        )
    rows = _fetch_paginated_rows(
        transport,
        path=_TODAY_ORDERS_PATH, tr_id=_TODAY_ORDERS_TR,
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "CCLD_NCCS_DVSN": "00",   # 체결미체결구분 -- 00 전체
            "SLL_BUY_DVSN_CD": "00",  # 매도매수구분 -- 00 전체
            "FUOP_DVSN": "00",        # 선물옵션구분 -- 00 전체
            "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        },
        output_key="output", max_pages=_MAX_PAGES, ctx_width=200,
        cap_message=(
            f"해외선물옵션 당일주문내역 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        ),
    )
    return _parse_today_orders(rows)


def _parse_today_orders(rows: list[Mapping[str, Any]]) -> list[OverseasDerivativeOrder]:
    orders: list[OverseasDerivativeOrder] = []
    for row in rows:
        order_id = str(row.get("odno", "")).strip()
        if not order_id:  # 주문번호 없는 패딩 행 -- 건너뜀
            continue
        orders.append(
            # 수량·가격은 빈 값을 0으로 읽되(외화 필드는 비어 올 수 있음), 값이 있는데 파싱 실패면
            # 여전히 예외. 방향(side)은 주문번호가 있는 행에서만 코드->buy/sell 로 fail-closed 변환.
            OverseasDerivativeOrder(
                order_date=_parse_date(row.get("ord_dt")),
                order_id=order_id,
                original_order_id=str(row.get("orgn_odno", "")).strip(),
                symbol=str(row.get("ovrs_futr_fx_pdno", "")).strip(),
                side=_side_from_code(row.get("sll_buy_dvsn_cd")),
                status=str(row.get("ord_stat_cd", "")).strip(),
                order_quantity=field_decimal_or_zero(row.get("fm_ord_qty"), "fm_ord_qty"),
                order_price=field_decimal_or_zero(row.get("fm_ord_pric"), "fm_ord_pric"),
                filled_quantity=field_decimal_or_zero(row.get("fm_ccld_qty"), "fm_ccld_qty"),
                filled_price=field_decimal_or_zero(row.get("fm_ccld_pric"), "fm_ccld_pric"),
                remaining_quantity=field_decimal_or_zero(row.get("fm_ord_rmn_qty"), "fm_ord_rmn_qty"),
                new_liquidation=str(row.get("new_lqd_dvsn_cd", "")).strip(),
                fuop=str(row.get("fuop_dvsn", "")).strip(),
                _raw=row,
            )
        )
    return orders


def fetch_daily_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str,
) -> list[OverseasDerivativeDailyOrder]:
    """해외선물옵션 일별 주문내역(기간 주문 목록, 연속조회 소진까지). ``start``~``end``
    (YYYYMMDD) 기간을 전체 매매("%%")·전체 체결미체결("00")로 조회한다. 연속조회 커서는 200폭
    (CTX_AREA_FK200/NK200)이다. 금액·수량은 각 계약 통화의 Decimal(원화 아님).
    **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 일별주문내역(inquire-daily-order)은 모의투자 미지원 -- 실전에서만."
        )
    _require_wire_date(start, "start")
    _require_wire_date(end, "end")
    rows = _fetch_paginated_rows(
        transport,
        path=_DAILY_ORDERS_PATH, tr_id=_DAILY_ORDERS_TR,
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "STRT_DT": start, "END_DT": end,
            "FM_PDGR_CD": "",          # 상품군코드 -- 전체
            "CCLD_NCCS_DVSN": "00",    # 체결미체결구분 -- 00 전체
            "SLL_BUY_DVSN_CD": "%%",   # 매도매수구분 -- %% 전체
            "FUOP_DVSN": "00",         # 선물옵션구분 -- 00 전체
            "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        },
        output_key="output", max_pages=_MAX_PAGES, ctx_width=200,
        cap_message=(
            f"해외선물옵션 일별주문내역 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        ),
    )
    return _parse_daily_orders(rows)


def _parse_daily_orders(rows: list[Mapping[str, Any]]) -> list[OverseasDerivativeDailyOrder]:
    orders: list[OverseasDerivativeDailyOrder] = []
    for row in rows:
        order_id = str(row.get("odno", "")).strip()
        if not order_id:  # 주문번호 없는 패딩 행 -- 건너뜀
            continue
        orders.append(
            # 수량·가격은 빈 값을 0으로 읽되(외화 필드는 비어 올 수 있음), 값이 있는데 파싱 실패면
            # 여전히 예외. 방향(side)은 주문번호가 있는 행에서만 코드->buy/sell 로 fail-closed 변환.
            OverseasDerivativeDailyOrder(
                date=_parse_date(row.get("dt")),
                order_date=_parse_date(row.get("ord_dt")),
                order_id=order_id,
                original_order_id=str(row.get("orgn_odno", "")).strip(),
                symbol=str(row.get("ovrs_futr_fx_pdno", "")).strip(),
                revise_cancel_type=str(row.get("rvse_cncl_dvsn_cd", "")).strip(),
                side=_side_from_code(row.get("sll_buy_dvsn_cd")),
                order_quantity=field_decimal_or_zero(row.get("fm_ord_qty"), "fm_ord_qty"),
                order_price=field_decimal_or_zero(row.get("fm_ord_pric"), "fm_ord_pric"),
                filled_quantity=field_decimal_or_zero(row.get("fm_ccld_qty"), "fm_ccld_qty"),
                filled_price=field_decimal_or_zero(row.get("fm_ccld_pric"), "fm_ccld_pric"),
                remaining_quantity=field_decimal_or_zero(row.get("fm_ord_rmn_qty"), "fm_ord_rmn_qty"),
                reject_reason=str(row.get("rjct_rson_name", "")).strip(),
                trade_end_date=_parse_date(row.get("trad_end_dt")),
                _raw=row,
            )
        )
    return orders


def fetch_daily_fills(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str,
) -> OverseasDerivativeFillHistory:
    """해외선물옵션 일별 체결내역(체결내역 output1 + 기간 합계 요약 output2). ``start``~``end``
    (YYYYMMDD) 기간을 전체 통화("%%%")·전체 매매("%%")로 조회하고 연속조회로 체결을 소진까지
    모은다(합계 요약은 첫 페이지에서 완결 -- 기간 단위라 페이지 불변). 연속조회 커서는 200폭
    (CTX_AREA_FK200/NK200)이다. 금액·수량은 각 체결 통화의 Decimal(원화 아님).
    **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 일별체결내역(inquire-daily-ccld)은 모의투자 미지원 -- 실전에서만."
        )
    _require_wire_date(start, "start")
    _require_wire_date(end, "end")
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "STRT_DT": start, "END_DT": end,
            "FUOP_DVSN_CD": "00",     # 선물옵션구분 -- 00 전체
            "FM_PDGR_CD": "",         # 상품군코드 -- 전체
            "CRCY_CD": "%%%",         # 통화코드 -- %%% 전체
            "FM_ITEM_FTNG_YN": "N",   # 종목합산여부 -- N
            "SLL_BUY_DVSN_CD": "%%",  # 매도매수구분 -- %% 전체
            "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_DAILY_FILLS_PATH, tr_id=_DAILY_FILLS_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        if summary is None:  # 합계 요약은 첫 페이지에서(기간 단위라 페이지 불변)
            summary = _extract_summary(resp.body)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 결과도 output1 을 빈 배열로 준다 -> 부재/비배열은 손상
            raise KISError(
                "해외선물옵션 일별체결내역 응답의 output1 이 체결내역 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        if resp.tr_cont not in ("F", "M"):
            break
        nxt = advance_cursor(resp.body, ctx_width=200, prev_nk=ctx_nk)
        if nxt is None:  # 비진전 커서(빈 키/반복/종료 센티널) -> 재요청 중단(이중집계 방지)
            break
        ctx_fk, ctx_nk = nxt
        tr_cont = "N"
    else:
        raise KISError(
            f"해외선물옵션 일별체결내역 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    if summary is None:
        raise KISError("해외선물옵션 일별체결내역 응답에 합계 요약(output2)이 없다.")
    return OverseasDerivativeFillHistory(
        # 외화 합계 필드는 비어 올 수 있어 '없음=0'(field_decimal_or_zero)으로 읽되, 값이 있는데
        # 파싱 실패면 여전히 예외로 fail-closed 한다.
        total_filled_quantity=field_decimal_or_zero(summary.get("fm_tot_ccld_qty"), "fm_tot_ccld_qty"),
        total_futures_agreement_amount=field_decimal_or_zero(
            summary.get("fm_tot_futr_agrm_amt"), "fm_tot_futr_agrm_amt"
        ),
        total_options_agreement_amount=field_decimal_or_zero(
            summary.get("fm_tot_opt_agrm_amt"), "fm_tot_opt_agrm_amt"
        ),
        total_fee=field_decimal_or_zero(summary.get("fm_fee_smtl"), "fm_fee_smtl"),
        fills=tuple(_parse_fills(rows)),
        _raw=summary,
    )


def _parse_fills(rows: list[Mapping[str, Any]]) -> list[OverseasDerivativeFill]:
    fills: list[OverseasDerivativeFill] = []
    for row in rows:
        if not isinstance(row, Mapping):  # output1=[None] 등 손상 -> fail-closed
            raise KISError("해외선물옵션 일별체결내역 응답 행이 매핑이 아니다.")
        fill_number = str(row.get("ccno", "")).strip()
        order_id = str(row.get("odno", "")).strip()
        if not fill_number or not order_id:  # 체결번호·주문번호 없는 패딩 행 -- 건너뜀
            continue
        fills.append(
            # 수량·금액은 빈 값을 0으로 읽되(외화 필드는 비어 올 수 있음), 값이 있는데 파싱 실패면
            # 여전히 예외. 방향(side)은 식별자가 있는 행에서만 코드->buy/sell 로 fail-closed 변환.
            OverseasDerivativeFill(
                date=_parse_date(row.get("dt")),
                fill_number=fill_number,
                symbol=str(row.get("ovrs_futr_fx_pdno", "")).strip(),
                side=_side_from_code(row.get("sll_buy_dvsn_cd")),
                filled_quantity=field_decimal_or_zero(row.get("fm_ccld_qty"), "fm_ccld_qty"),
                filled_amount=field_decimal_or_zero(row.get("fm_ccld_amt"), "fm_ccld_amt"),
                currency=str(row.get("crcy_cd", "")).strip(),
                fee=field_decimal_or_zero(row.get("fm_fee"), "fm_fee"),
                order_date=_parse_date(row.get("ord_dt")),
                order_id=order_id,
                order_medium=str(row.get("ord_mdia_dvsn_name", "")).strip(),
                _raw=row,
            )
        )
    return fills


def _extract_summary(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """합계 요약(output2) -- KIS가 단일 객체로도, '길이 1 배열'로도 준다. 비매핑이면 None."""
    summary = body.get("output2")
    if isinstance(summary, list):
        first = summary[0] if summary else None
        return first if isinstance(first, Mapping) else None
    if isinstance(summary, Mapping):
        return summary
    return None


def fetch_period_pnl(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str,
) -> OverseasDerivativePNLHistory:
    """해외선물옵션 기간 손익(통화별 output1 + 종목별 output2). ``start``~``end`` (YYYYMMDD)
    기간을 전체 통화("%%%")·원화환산 안 함("N")으로 조회하고 연속조회로 두 집계 블록을
    소진까지 함께 모은다(각 페이지의 output1/output2 를 모두 이어붙인다). 연속조회 커서는 200폭
    (CTX_AREA_FK200/NK200)이다. 금액·수량은 각 행 통화의 Decimal(원화 아님).
    **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 기간손익(inquire-period-ccld)은 모의투자 미지원 -- 실전에서만."
        )
    _require_wire_date(start, "start")
    _require_wire_date(end, "end")
    by_currency_rows: list[Mapping[str, Any]] = []
    by_symbol_rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "INQR_TERM_FROM_DT": start, "INQR_TERM_TO_DT": end,
            "CRCY_CD": "%%%",         # 통화코드 -- %%% 전체
            "WHOL_TRSL_YN": "N",      # 원화환산여부 -- N
            "FUOP_DVSN": "00",        # 선물옵션구분 -- 00 전체
            "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_PERIOD_PNL_PATH, tr_id=_PERIOD_PNL_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        by_currency_rows.extend(_require_pnl_block(resp.body.get("output1"), "output1", resp))
        by_symbol_rows.extend(_require_pnl_block(resp.body.get("output2"), "output2", resp))
        if resp.tr_cont not in ("F", "M"):
            break
        nxt = advance_cursor(resp.body, ctx_width=200, prev_nk=ctx_nk)
        if nxt is None:  # 비진전 커서(빈 키/반복/종료 센티널) -> 재요청 중단(이중집계 방지)
            break
        ctx_fk, ctx_nk = nxt
        tr_cont = "N"
    else:
        raise KISError(
            f"해외선물옵션 기간손익 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    return OverseasDerivativePNLHistory(
        by_currency=tuple(_parse_pnl_rows(by_currency_rows)),
        by_symbol=tuple(_parse_pnl_rows(by_symbol_rows)),
    )


def _require_pnl_block(block: object, name: str, resp: RawResponse) -> list[Mapping[str, Any]]:
    """기간손익 집계 블록(output1/output2)을 배열로 검증한다. 빈 결과도 배열로 오므로
    부재/비배열은 손상으로 보고 fail-closed(:class:`KISError`)."""
    if not isinstance(block, list):
        raise KISError(
            f"해외선물옵션 기간손익 응답의 {name} 이 손익 배열이 아니다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return block


def _parse_pnl_rows(rows: list[Mapping[str, Any]]) -> list[OverseasDerivativePNL]:
    pnls: list[OverseasDerivativePNL] = []
    for row in rows:
        if not isinstance(row, Mapping):  # output=[None] 등 손상 -> fail-closed
            raise KISError("해외선물옵션 기간손익 응답 행이 매핑이 아니다.")
        currency = str(row.get("crcy_cd", "")).strip()
        symbol = str(row.get("ovrs_futr_fx_pdno", "")).strip()
        if not currency and not symbol:  # 통화·종목 모두 빈 패딩 행 -- 건너뜀
            continue
        pnls.append(
            # 수량·금액은 빈 값을 0으로 읽되(외화 필드는 비어 올 수 있음), 값이 있는데 파싱 실패면
            # 여전히 예외로 fail-closed 한다.
            OverseasDerivativePNL(
                currency=currency,
                symbol=symbol,
                buy_quantity=field_decimal_or_zero(row.get("fm_buy_qty"), "fm_buy_qty"),
                sell_quantity=field_decimal_or_zero(row.get("fm_sll_qty"), "fm_sll_qty"),
                realized_pnl=field_decimal_or_zero(row.get("fm_lqd_pfls_amt"), "fm_lqd_pfls_amt"),
                fee=field_decimal_or_zero(row.get("fm_fee"), "fm_fee"),
                net_pnl=field_decimal_or_zero(row.get("fm_net_pfls_amt"), "fm_net_pfls_amt"),
                open_buy_quantity=field_decimal_or_zero(row.get("fm_ustl_buy_qty"), "fm_ustl_buy_qty"),
                open_sell_quantity=field_decimal_or_zero(row.get("fm_ustl_sll_qty"), "fm_ustl_sll_qty"),
                unrealized_pnl=field_decimal_or_zero(
                    row.get("fm_ustl_evlu_pfls_amt"), "fm_ustl_evlu_pfls_amt"
                ),
                open_agreement_amount=field_decimal_or_zero(
                    row.get("fm_ustl_agrm_amt"), "fm_ustl_agrm_amt"
                ),
                _raw=row,
            )
        )
    return pnls


def fetch_transactions(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str,
) -> list[OverseasDerivativeTransaction]:
    """해외선물옵션 기간 입출금내역(원장 목록, 연속조회 소진까지). ``start``~``end`` (YYYYMMDD)
    기간을 전체 거래유형("%%")·전체 통화("%%%")로 조회한다. 계좌 비밀번호는 확인하지 않는다
    (PWD_CHK_YN="N"). 연속조회 커서는 100폭(CTX_AREA_FK100/NK100)이다. 금액은 각 행 통화의
    Decimal(원화 아님). **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 기간입출금내역(inquire-period-trans)은 모의투자 미지원 -- 실전에서만."
        )
    _require_wire_date(start, "start")
    _require_wire_date(end, "end")
    rows = _fetch_paginated_rows(
        transport,
        path=_PERIOD_TRANS_PATH, tr_id=_PERIOD_TRANS_TR,
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "INQR_TERM_FROM_DT": start, "INQR_TERM_TO_DT": end,
            "ACNT_TR_TYPE_CD": "%%",   # 계좌거래유형코드 -- %% 전체
            "CRCY_CD": "%%%",          # 통화코드 -- %%% 전체
            "PWD_CHK_YN": "N",         # 비밀번호확인여부 -- N(확인 안 함)
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        },
        output_key="output", max_pages=_MAX_PAGES, ctx_width=100,
        cap_message=(
            f"해외선물옵션 기간입출금내역 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        ),
    )
    return _parse_transactions(rows)


def _parse_transactions(rows: list[Mapping[str, Any]]) -> list[OverseasDerivativeTransaction]:
    transactions: list[OverseasDerivativeTransaction] = []
    for row in rows:
        ledger_sequence = str(row.get("fm_ldgr_inog_seq", "")).strip()
        base_date_text = str(row.get("bass_dt", "")).strip()
        if not ledger_sequence and not base_date_text:  # 순번·기준일자 모두 빈 패딩 행 -- 건너뜀
            continue
        transactions.append(
            # 금액은 빈 값을 0으로 읽되(외화 필드는 비어 올 수 있음), 값이 있는데 파싱 실패면
            # 여전히 예외로 fail-closed 한다.
            OverseasDerivativeTransaction(
                date=_parse_date(base_date_text),
                ledger_sequence=ledger_sequence,
                transaction_type=str(row.get("acnt_tr_type_name", "")).strip(),
                currency=str(row.get("crcy_cd", "")).strip(),
                item_name=str(row.get("tr_itm_name", "")).strip(),
                amount=field_decimal_or_zero(row.get("fm_iofw_amt"), "fm_iofw_amt"),
                fee=field_decimal_or_zero(row.get("fm_fee"), "fm_fee"),
                tax=field_decimal_or_zero(row.get("fm_tax_amt"), "fm_tax_amt"),
                settlement_amount=field_decimal_or_zero(row.get("fm_sttl_amt"), "fm_sttl_amt"),
                prior_deposit=field_decimal_or_zero(row.get("fm_bf_dncl_amt"), "fm_bf_dncl_amt"),
                deposit=field_decimal_or_zero(row.get("fm_dncl_amt"), "fm_dncl_amt"),
                receivable_incurred=field_decimal_or_zero(
                    row.get("fm_rcvb_occr_amt"), "fm_rcvb_occr_amt"
                ),
                receivable_repaid=field_decimal_or_zero(
                    row.get("fm_rcvb_pybk_amt"), "fm_rcvb_pybk_amt"
                ),
                remarks=str(row.get("rmks_text", "")).strip(),
                _raw=row,
            )
        )
    return transactions


def _require_wire_date(value: str, field_name: str) -> None:
    """조회 요청의 일자 파라미터를 와이어 이전에 검증한다 -- 8자리 숫자(YYYYMMDD)가 아니면
    :class:`KISUsageError`. 잘못된 일자로 조회를 날리는 대신 호출 즉시 실패시킨다."""
    if len(value) != 8 or not value.isdigit():
        raise KISUsageError(
            f"일자 파라미터 {field_name!r} 는 8자리 숫자(YYYYMMDD)여야 한다: {value!r}"
        )


def fetch_orderable(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    symbol: str, side: Side, price: Numeric | None = None, exercise_reserved: bool = False,
) -> OverseasDerivativeOrderable:
    """해외선물옵션 계약의 주문가능수량(1콜, output 단일 객체). ``symbol`` 해외선물FX상품번호,
    ``side`` 매수/매도, ``price`` 있으면 그 단가 기준·없으면 시장가("0"), ``exercise_reserved``
    행사예약주문 여부. 수량은 그 통화 기준. **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 주문가능수량(inquire-psamount)은 모의투자 미지원 -- 실전에서만."
        )
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "OVRS_FUTR_FX_PDNO": symbol,
        "SLL_BUY_DVSN_CD": _SIDE_TO_SLL_BUY[side],
        "FM_ORD_PRIC": _format_order_price(price),
        "ECIS_RSVN_ORD_YN": "Y" if exercise_reserved else "N",
    }
    resp = transport.request(
        method="GET", path=_ORDERABLE_PATH, tr_id=_ORDERABLE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise KISError(
            "해외선물옵션 주문가능수량 응답에 output 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return OverseasDerivativeOrderable(
        # 신규/총 주문가능수량은 조회의 핵심 답이라 required_decimal(부재면 손상->예외), 나머지
        # 상태 필드는 '없음=0'(field_decimal_or_zero)으로 읽되 값이 있는데 파싱 실패면 예외.
        symbol=str(output.get("ovrs_futr_fx_pdno", "")).strip() or symbol,
        currency=str(output.get("crcy_cd", "")).strip(),
        open_quantity=field_decimal_or_zero(output.get("fm_ustl_qty"), "fm_ustl_qty"),
        liquidatable_quantity=field_decimal_or_zero(output.get("fm_lqd_psbl_qty"), "fm_lqd_psbl_qty"),
        new_orderable_quantity=required_decimal(output.get("fm_new_ord_psbl_qty"), "fm_new_ord_psbl_qty"),
        total_orderable_quantity=required_decimal(output.get("fm_tot_ord_psbl_qty"), "fm_tot_ord_psbl_qty"),
        market_orderable_quantity=field_decimal_or_zero(
            output.get("fm_mkpr_tot_ord_psbl_qty"), "fm_mkpr_tot_ord_psbl_qty"
        ),
        _raw=output,
    )


def _format_order_price(price: Numeric | None) -> str:
    """주문가능조회의 단가를 KIS 와이어 정본으로 -- ``None`` 이면 시장가라 "0". 유한 양수 아니면 거부."""
    if price is None:
        return "0"
    try:
        amount = Decimal(str(price))
    except (ArithmeticError, ValueError) as err:
        raise KISUsageError(f"price 는 숫자여야 한다: {price!r}") from err
    if not amount.is_finite() or amount <= 0:
        raise KISUsageError(f"price 는 0보다 큰 유한값이어야 한다: {price!r}")
    return format_wire_decimal(amount)
