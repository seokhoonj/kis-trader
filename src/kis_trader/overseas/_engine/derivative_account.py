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

from ..._internal._response import _fetch_paginated_rows, _raise_if_error
from ..._internal._wire import field_decimal_or_zero, format_wire_decimal, required_decimal
from ...errors import KISError, KISUsageError
from ...transport import Environment, Transport
from ..entities.derivative_account import (
    OverseasDerivativeDeposit,
    OverseasDerivativeMargin,
    OverseasDerivativeOrderable,
    OverseasDerivativePosition,
)
from ._parse import _MAX_PAGES, _side_from_code

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
#: 매도매수구분코드(SLL_BUY_DVSN_CD): 매수 02 / 매도 01.
_SIDE_TO_SLL_BUY = {"buy": "02", "sell": "01"}


def fetch_deposit(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    currency: str, date: str,
) -> OverseasDerivativeDeposit:
    """해외선물옵션 예수금현황(1콜, output 단일 객체). ``currency`` 조회 통화(CRCY_CD),
    ``date`` 조회일자(YYYYMMDD). 금액은 그 통화의 Decimal(원화 아님).
    **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 예수금현황(inquire-deposit)은 모의투자 미지원 -- 실전에서만."
        )
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "CRCY_CD": currency, "INQR_DT": date,
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
    currency: str, date: str,
) -> OverseasDerivativeMargin:
    """해외선물옵션 증거금상세(1콜, output 단일 객체). ``currency`` 조회 통화(CRCY_CD),
    ``date`` 조회일자(YYYYMMDD). 금액은 그 통화의 Decimal(원화 아님). SPAN/EUREX 등 상세
    증거금 내역은 ``_raw`` 로만 노출한다. **모의투자 미지원**(paper면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "해외선물옵션 증거금상세(margin-detail)는 모의투자 미지원 -- 실전에서만."
        )
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "CRCY_CD": currency, "INQR_DT": date,
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
