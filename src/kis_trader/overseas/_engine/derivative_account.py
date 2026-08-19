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

from ..._internal._response import _raise_if_error
from ..._internal._wire import field_decimal_or_zero
from ...errors import KISError, KISUsageError
from ...transport import Environment, Transport
from ..entities.derivative_account import OverseasDerivativeDeposit

_DEPOSIT_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-deposit"
_DEPOSIT_TR = "OTFM1411R"  # 해외선물옵션 예수금현황, 모의투자 미지원


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
