"""퇴직연금(IRP/DC) 계좌 조회 (내부) -- 예수금·매수가능·잔고·미체결.

일반 위탁계좌 조회(:mod:`kis_openapi._domestic.account`)와 별개인 퇴직연금 전용 엔드포인트
(`/uapi/domestic-stock/v1/trading/pension/...`)를 감싼다. 전부 **모의투자 미지원**이며, 계좌
상품코드가 퇴직연금 계좌여야 KIS가 정상 응답한다(아니면 KIS 오류를 그대로 전달).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .._wire import format_wire_decimal, optional_decimal
from ..errors import KISError, KISUsageError
from ..pension_items import PensionBuyableAmount, PensionDeposit
from ..transport import Environment, Transport

_DEPOSIT_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-deposit"
_DEPOSIT_TR = "TTTC0506R"
_BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-psbl-order"
_BUYABLE_TR = "TTTC0503R"


def fetch_deposit(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> PensionDeposit:
    """퇴직연금 예수금 요약. **모의투자 미지원**."""
    _reject_demo(environment, "퇴직연금 예수금조회(pension/inquire-deposit)")
    params = {"CANO": cano, "ACNT_PRDT_CD": product_code, "ACCA_DVSN_CD": "00"}
    output = _request_output(transport, _DEPOSIT_PATH, _DEPOSIT_TR, params, "예수금")
    return PensionDeposit(
        deposit_total=_decimal_or_zero(output, "dnca_tota"),
        next_day_settlement=_decimal_or_zero(output, "nxdy_excc_amt"),
        next_day_settlement_amount=_decimal_or_zero(output, "nxdy_sttl_amt"),
        second_day_settlement_amount=_decimal_or_zero(output, "nx2_day_sttl_amt"),
        _raw=output,
    )


def fetch_buyable(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    symbol: str, limit_price: object | None = None,
) -> PensionBuyableAmount:
    """퇴직연금 매수가능 여력. ``limit_price`` 없으면 시장가 기준. **모의투자 미지원**."""
    _reject_demo(environment, "퇴직연금 매수가능조회(pension/inquire-psbl-order)")
    unit_price = _format_order_unit_price(limit_price)
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code, "PDNO": symbol,
        "ACCA_DVSN_CD": "00", "CMA_EVLU_AMT_ICLD_YN": "N",
        "ORD_DVSN": "00" if limit_price is not None else "01",
        "ORD_UNPR": unit_price or "0",
    }
    output = _request_output(transport, _BUYABLE_PATH, _BUYABLE_TR, params, "매수가능")
    return PensionBuyableAmount(
        symbol=symbol,
        orderable_cash=_decimal_or_zero(output, "ord_psbl_cash"),
        reusable_cash=_decimal_or_zero(output, "ruse_psbl_amt"),
        calc_unit_price=_decimal_or_zero(output, "psbl_qty_calc_unpr"),
        max_buyable_amount=_decimal_or_zero(output, "max_buy_amt"),
        max_buyable_quantity=_decimal_or_zero(output, "max_buy_qty"),
        _raw=output,
    )


# --- 공용 ------------------------------------------------------------------
def _request_output(
    transport: Transport, path: str, tr_id: str, params: Mapping[str, Any], label: str
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


def _reject_demo(environment: Environment, what: str) -> None:
    if environment == "demo":
        raise KISUsageError(f"{what}는 모의투자 미지원 -- 실전에서만.")


def _decimal_or_zero(output: Mapping[str, Any], key: str) -> Decimal:
    amount = optional_decimal(output.get(key), key)
    return Decimal(0) if amount is None else amount


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
