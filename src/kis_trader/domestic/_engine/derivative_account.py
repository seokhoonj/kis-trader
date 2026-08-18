"""국내선물옵션 계좌 조회 (내부) -- 잔고(보유내역 + 계좌 요약).

사용자면(``kis.account`` -> :class:`DomesticDerivativesAccount`)이 이 함수를 호출한다. 계좌
식별정보(``cano``/``product_code``)와 환경(``environment``)은 세션에서 온다. KIS 원본 필드 매핑과
fail-closed 파싱은 여기 갇힌다.

KIS URL/TR-ID:
- 잔고: ``GET .../trading/inquire-balance`` (실전 ``CTFO6118R`` / 모의 ``VTFO6118R``).
- 총자산현황: ``GET .../trading/inquire-deposit`` (``CTRP6550R``, **모의 미지원**).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from ..._internal._response import _raise_if_error
from ..._internal._wire import optional_decimal, required_decimal
from ...errors import KISError, KISUsageError
from ...transport import Environment, RawResponse, Transport
from ..entities.derivative_account import (
    DerivativeBalance,
    DerivativeCommission,
    DerivativeCommissionHistory,
    DerivativeDeposit,
    DerivativeFill,
    DerivativeFillHistory,
    DerivativePosition,
    DerivativeSettlementBalance,
    DerivativeSettlementPosition,
    DerivativeValuationBalance,
    DerivativeValuationPosition,
)

_BALANCE_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-balance"
_BALANCE_TR = {"real": "CTFO6118R", "paper": "VTFO6118R"}
#: 잔고 보유내역 연속조회 페이지 상한. 여기 닿으면 부분 결과로 자르지 않고 fail-closed.
_MAX_BALANCE_PAGES = 100

_DEPOSIT_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-deposit"
_DEPOSIT_TR = "CTRP6550R"  # 선물옵션 총자산현황, 모의투자 미지원

_VALUATION_PL_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-balance-valuation-pl"
_VALUATION_PL_TR = "CTFO6159R"  # 선물옵션 잔고평가손익내역, 모의투자 미지원

_SETTLEMENT_PL_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-balance-settlement-pl"
_SETTLEMENT_PL_TR = "CTFO6117R"  # 선물옵션 잔고정산손익내역, 모의투자 미지원

_BASE_DATE_FILLS_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-ccnl-bstime"
_BASE_DATE_FILLS_TR = "CTFO5139R"  # 선물옵션 기준일체결내역, 모의투자 미지원

_COMMISSIONS_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-daily-amount-fee"
_COMMISSIONS_TR = "CTFO6119R"  # 선물옵션 기간약정수수료일별, 모의투자 미지원


def fetch_balance(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> DerivativeBalance:
    """선물옵션 잔고(보유내역 output1 + 계좌 요약 output2). 연속조회로 보유내역을 소진까지 모으고
    계좌 요약은 첫 페이지에서 완결한다(계좌 단위라 페이지 불변). 모의투자 지원."""
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_BALANCE_PAGES):
        resp = _fetch_balance_page(
            transport, cano=cano, product_code=product_code, environment=environment,
            ctx_fk=ctx_fk, ctx_nk=ctx_nk, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        if summary is None:  # 계좌 요약은 첫 페이지에서(계좌 단위라 페이지 불변)
            summary = _extract_summary(resp.body)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 계좌도 output1 을 빈 배열로 준다 -> 부재/비배열은 손상
            raise KISError(
                "선물옵션 잔고 응답의 output1 이 보유내역 배열이 아니다.",
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
            f"선물옵션 잔고 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    if summary is None:
        raise KISError("선물옵션 잔고 응답에 계좌 요약(output2)이 없다.")
    return DerivativeBalance(
        positions=tuple(_parse_positions(rows)),
        total_deposit=required_decimal(summary.get("tot_dncl_amt"), "tot_dncl_amt"),
        deposit_cash=required_decimal(summary.get("dnca_cash"), "dnca_cash"),
        total_margin=required_decimal(summary.get("mgna_tota"), "mgna_tota"),
        orderable_cash=required_decimal(summary.get("ord_psbl_cash"), "ord_psbl_cash"),
        orderable_total=required_decimal(summary.get("ord_psbl_tota"), "ord_psbl_tota"),
        total_unrealized_pnl=required_decimal(summary.get("evlu_pfls_amt_smtl"), "evlu_pfls_amt_smtl"),
        total_realized_pnl=required_decimal(summary.get("trad_pfls_amt_smtl"), "trad_pfls_amt_smtl"),
        futures_unrealized_pnl=required_decimal(summary.get("futr_evlu_pfls_amt"), "futr_evlu_pfls_amt"),
        options_unrealized_pnl=required_decimal(summary.get("opt_evlu_pfls_amt"), "opt_evlu_pfls_amt"),
        futures_realized_pnl=required_decimal(summary.get("futr_trad_pfls_amt"), "futr_trad_pfls_amt"),
        options_realized_pnl=required_decimal(summary.get("opt_trad_pfls_amt"), "opt_trad_pfls_amt"),
        account_value=required_decimal(summary.get("prsm_dpast_amt"), "prsm_dpast_amt"),
        raw=summary,
    )


def fetch_deposit(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> DerivativeDeposit:
    """선물옵션 총자산현황(1콜, output 단일 객체). 예수금·주문가능·위탁증거금·손익 요약.
    **모의투자 미지원**(demo면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "선물옵션 총자산현황(inquire-deposit)은 모의투자 미지원 -- 실전에서만."
        )
    params = {"CANO": cano, "ACNT_PRDT_CD": product_code}
    resp = transport.request(
        method="GET", path=_DEPOSIT_PATH, tr_id=_DEPOSIT_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise KISError(
            "선물옵션 총자산현황 응답에 output 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return DerivativeDeposit(
        total_deposit=required_decimal(output.get("dnca_tota"), "dnca_tota"),
        orderable_cash=required_decimal(output.get("ord_psbl_cash"), "ord_psbl_cash"),
        orderable_total=required_decimal(output.get("ord_psbl_tota"), "ord_psbl_tota"),
        brokerage_margin_cash=required_decimal(output.get("brkg_mgna_cash"), "brkg_mgna_cash"),
        brokerage_margin_substitute=required_decimal(output.get("brkg_mgna_sbst"), "brkg_mgna_sbst"),
        maintenance_ratio=required_decimal(output.get("mtnc_rt"), "mtnc_rt"),
        total_unrealized_pnl=required_decimal(output.get("evlu_pfls_smtl"), "evlu_pfls_smtl"),
        total_realized_pnl=required_decimal(output.get("trad_pfls_smtl"), "trad_pfls_smtl"),
        futures_unrealized_pnl=required_decimal(output.get("futr_evlu_pfls_amt"), "futr_evlu_pfls_amt"),
        options_unrealized_pnl=required_decimal(output.get("opt_evlu_pfls_amt"), "opt_evlu_pfls_amt"),
        futures_realized_pnl=required_decimal(output.get("futr_trad_pfls"), "futr_trad_pfls"),
        options_realized_pnl=required_decimal(output.get("opt_trad_pfls_amt"), "opt_trad_pfls_amt"),
        account_value=required_decimal(output.get("prsm_dpast_amt"), "prsm_dpast_amt"),
        receivable=required_decimal(output.get("rcva"), "rcva"),
        raw=output,
    )


def fetch_valuation_pl(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> DerivativeValuationBalance:
    """선물옵션 잔고평가손익내역(보유내역 output1 + 계좌 요약 output2). 연속조회로 보유내역을
    소진까지 모으고 계좌 요약은 첫 페이지에서 완결한다(계좌 단위라 페이지 불변).
    **모의투자 미지원**(demo면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "선물옵션 잔고평가손익내역(inquire-balance-valuation-pl)은 모의투자 미지원 -- 실전에서만."
        )
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_BALANCE_PAGES):
        resp = _fetch_valuation_pl_page(
            transport, cano=cano, product_code=product_code,
            ctx_fk=ctx_fk, ctx_nk=ctx_nk, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        if summary is None:  # 계좌 요약은 첫 페이지에서(계좌 단위라 페이지 불변)
            summary = _extract_summary(resp.body)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 계좌도 output1 을 빈 배열로 준다 -> 부재/비배열은 손상
            raise KISError(
                "선물옵션 잔고평가손익내역 응답의 output1 이 보유내역 배열이 아니다.",
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
            f"선물옵션 잔고평가손익내역 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 "
            f"연속조회가 남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    if summary is None:
        raise KISError("선물옵션 잔고평가손익내역 응답에 계좌 요약(output2)이 없다.")
    return DerivativeValuationBalance(
        positions=tuple(_parse_valuation_positions(rows)),
        total_deposit=required_decimal(summary.get("tot_dncl_amt"), "tot_dncl_amt"),
        deposit_cash=required_decimal(summary.get("dnca_cash"), "dnca_cash"),
        total_margin=required_decimal(summary.get("mgna_tota"), "mgna_tota"),
        orderable_cash=required_decimal(summary.get("ord_psbl_cash"), "ord_psbl_cash"),
        orderable_total=required_decimal(summary.get("ord_psbl_tota"), "ord_psbl_tota"),
        total_unrealized_pnl=required_decimal(summary.get("evlu_pfls_amt_smtl"), "evlu_pfls_amt_smtl"),
        total_realized_pnl=required_decimal(summary.get("trad_pfls_amt_smtl"), "trad_pfls_amt_smtl"),
        futures_unrealized_pnl=required_decimal(summary.get("futr_evlu_pfls_amt"), "futr_evlu_pfls_amt"),
        options_unrealized_pnl=required_decimal(summary.get("opt_evlu_pfls_amt"), "opt_evlu_pfls_amt"),
        futures_realized_pnl=required_decimal(summary.get("futr_trad_pfls_amt"), "futr_trad_pfls_amt"),
        options_realized_pnl=required_decimal(summary.get("opt_trad_pfls_amt"), "opt_trad_pfls_amt"),
        account_value=required_decimal(summary.get("prsm_dpast_amt"), "prsm_dpast_amt"),
        raw=summary,
    )


def fetch_settlement_pl(
    transport: Transport, *, cano: str, product_code: str, environment: Environment, date: str
) -> DerivativeSettlementBalance:
    """선물옵션 잔고정산손익내역(정산 보유내역 output1 + 계좌 요약 output2). ``date`` (YYYYMMDD)
    기준일로 조회하고 연속조회로 보유내역을 소진까지 모은다(계좌 요약은 첫 페이지에서 완결).
    **모의투자 미지원**(demo면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "선물옵션 잔고정산손익내역(inquire-balance-settlement-pl)은 모의투자 미지원 -- 실전에서만."
        )
    _require_wire_date(date, "date")
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_BALANCE_PAGES):
        resp = _fetch_settlement_pl_page(
            transport, cano=cano, product_code=product_code, date=date,
            ctx_fk=ctx_fk, ctx_nk=ctx_nk, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        if summary is None:  # 계좌 요약은 첫 페이지에서(계좌 단위라 페이지 불변)
            summary = _extract_summary(resp.body)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 계좌도 output1 을 빈 배열로 준다 -> 부재/비배열은 손상
            raise KISError(
                "선물옵션 잔고정산손익내역 응답의 output1 이 정산 보유내역 배열이 아니다.",
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
            f"선물옵션 잔고정산손익내역 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 "
            f"연속조회가 남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    if summary is None:
        raise KISError("선물옵션 잔고정산손익내역 응답에 계좌 요약(output2)이 없다.")
    return DerivativeSettlementBalance(
        positions=tuple(_parse_settlement_positions(rows)),
        next_day_deposit=required_decimal(summary.get("nxdy_dnca"), "nxdy_dnca"),
        maintenance_margin_cash=required_decimal(summary.get("mmga_cash"), "mmga_cash"),
        maintenance_margin_total=required_decimal(summary.get("mmga_tota"), "mmga_tota"),
        brokerage_margin_cash=required_decimal(summary.get("brkg_mgna_cash"), "brkg_mgna_cash"),
        brokerage_margin_total=required_decimal(summary.get("brkg_mgna_tota"), "brkg_mgna_tota"),
        deposit_cash=required_decimal(summary.get("dnca_cash"), "dnca_cash"),
        deposit_substitute=required_decimal(summary.get("dnca_sbst"), "dnca_sbst"),
        option_buy_amount=required_decimal(summary.get("opt_buy_chgs"), "opt_buy_chgs"),
        option_sell_amount=required_decimal(summary.get("opt_sll_chgs"), "opt_sll_chgs"),
        option_liquidation_value=required_decimal(summary.get("opt_lqd_evlu_amt"), "opt_lqd_evlu_amt"),
        fee=required_decimal(summary.get("fee"), "fee"),
        today_settlement_diff=required_decimal(summary.get("thdt_dfpa"), "thdt_dfpa"),
        renewal_settlement_diff=required_decimal(summary.get("rnwl_dfpa"), "rnwl_dfpa"),
        raw=summary,
    )


def fetch_base_date_fills(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    date: str, start_time: str, end_time: str,
) -> DerivativeFillHistory:
    """선물옵션 기준일체결내역(체결내역 output1 + 기간 합계 요약 output2). ``date`` (YYYYMMDD)
    기준일과 ``start_time``/``end_time`` (HHMMSS) 시각 구간으로 조회하고 연속조회로 체결을
    소진까지 모은다(합계 요약은 첫 페이지에서 완결).
    **모의투자 미지원**(demo면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "선물옵션 기준일체결내역(inquire-ccnl-bstime)은 모의투자 미지원 -- 실전에서만."
        )
    _require_wire_date(date, "date")
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_BALANCE_PAGES):
        resp = _fetch_base_date_fills_page(
            transport, cano=cano, product_code=product_code,
            date=date, start_time=start_time, end_time=end_time,
            ctx_fk=ctx_fk, ctx_nk=ctx_nk, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        if summary is None:  # 합계 요약은 첫 페이지에서(조회 단위라 페이지 불변)
            summary = _extract_summary(resp.body)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 결과도 output1 을 빈 배열로 준다 -> 부재/비배열은 손상
            raise KISError(
                "선물옵션 기준일체결내역 응답의 output1 이 체결내역 배열이 아니다.",
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
            f"선물옵션 기준일체결내역 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 "
            f"연속조회가 남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    if summary is None:
        raise KISError("선물옵션 기준일체결내역 응답에 합계 요약(output2)이 없다.")
    return DerivativeFillHistory(
        fills=tuple(_parse_fills(rows)),
        total_fill_quantity=required_decimal(summary.get("tot_ccld_qty_smtl"), "tot_ccld_qty_smtl"),
        total_fill_amount=required_decimal(summary.get("tot_ccld_amt_smtl"), "tot_ccld_amt_smtl"),
        fee_adjustment=required_decimal(summary.get("fee_adjt"), "fee_adjt"),
        total_fee=required_decimal(summary.get("fee_smtl"), "fee_smtl"),
        raw=summary,
    )


def fetch_commissions(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str,
) -> DerivativeCommissionHistory:
    """선물옵션 기간약정수수료일별(일별 내역 output1 + 기간 합계 요약 output2). ``start``~``end``
    (YYYYMMDD) 기간으로 조회하고 연속조회로 일별 내역을 소진까지 모은다(합계 요약은 첫 페이지에서
    완결). **모의투자 미지원**(demo면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "선물옵션 기간약정수수료일별(inquire-daily-amount-fee)은 모의투자 미지원 -- 실전에서만."
        )
    _require_wire_date(start, "start")
    _require_wire_date(end, "end")
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_BALANCE_PAGES):
        resp = _fetch_commissions_page(
            transport, cano=cano, product_code=product_code, start=start, end=end,
            ctx_fk=ctx_fk, ctx_nk=ctx_nk, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        if summary is None:  # 합계 요약은 첫 페이지에서(기간 단위라 페이지 불변)
            summary = _extract_summary(resp.body)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 결과도 output1 을 빈 배열로 준다 -> 부재/비배열은 손상
            raise KISError(
                "선물옵션 기간약정수수료일별 응답의 output1 이 일별 내역 배열이 아니다.",
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
            f"선물옵션 기간약정수수료일별 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 "
            f"연속조회가 남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    if summary is None:
        raise KISError("선물옵션 기간약정수수료일별 응답에 합계 요약(output2)이 없다.")
    return DerivativeCommissionHistory(
        days=tuple(_parse_commissions(rows)),
        total_fee=required_decimal(summary.get("fee_smtl"), "fee_smtl"),
        total_agreement_amount=required_decimal(summary.get("agrm_amt_smtl"), "agrm_amt_smtl"),
        total_sell_fee=required_decimal(summary.get("sll_fee"), "sll_fee"),
        total_buy_fee=required_decimal(summary.get("buy_fee"), "buy_fee"),
        futures_fee=required_decimal(summary.get("futr_fee_smtl"), "futr_fee_smtl"),
        options_fee=required_decimal(summary.get("opt_fee_smtl"), "opt_fee_smtl"),
        total_realized_pnl=required_decimal(summary.get("trad_pfls_smtl"), "trad_pfls_smtl"),
        raw=summary,
    )


def _fetch_balance_page(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    ctx_fk: str, ctx_nk: str, tr_cont: str = "",
) -> RawResponse:
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "MGNA_DVSN": "01",   # 증거금구분 -- 01 개시
        "EXCC_STAT_CD": "1",  # 정산상태 -- 1 정산
        "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
    }
    return transport.request(
        method="GET", path=_BALANCE_PATH, tr_id=_BALANCE_TR[environment],
        params=params, idempotent=True, tr_cont=tr_cont,
    )


def _fetch_valuation_pl_page(
    transport: Transport, *, cano: str, product_code: str,
    ctx_fk: str, ctx_nk: str, tr_cont: str = "",
) -> RawResponse:
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "MGNA_DVSN": "01",   # 증거금구분 -- 01 개시
        "EXCC_STAT_CD": "1",  # 정산상태 -- 1 정산
        "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
    }
    return transport.request(
        method="GET", path=_VALUATION_PL_PATH, tr_id=_VALUATION_PL_TR,
        params=params, idempotent=True, tr_cont=tr_cont,
    )


def _fetch_settlement_pl_page(
    transport: Transport, *, cano: str, product_code: str, date: str,
    ctx_fk: str, ctx_nk: str, tr_cont: str = "",
) -> RawResponse:
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "INQR_DT": date,  # 조회일자(YYYYMMDD)
        "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
    }
    return transport.request(
        method="GET", path=_SETTLEMENT_PL_PATH, tr_id=_SETTLEMENT_PL_TR,
        params=params, idempotent=True, tr_cont=tr_cont,
    )


def _fetch_base_date_fills_page(
    transport: Transport, *, cano: str, product_code: str,
    date: str, start_time: str, end_time: str,
    ctx_fk: str, ctx_nk: str, tr_cont: str = "",
) -> RawResponse:
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "ORD_DT": date,  # 주문일자(YYYYMMDD)
        "FUOP_TR_STRT_TMD": start_time,  # 선물옵션 거래 시작시각(HHMMSS)
        "FUOP_TR_END_TMD": end_time,     # 선물옵션 거래 종료시각(HHMMSS)
        "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
    }
    return transport.request(
        method="GET", path=_BASE_DATE_FILLS_PATH, tr_id=_BASE_DATE_FILLS_TR,
        params=params, idempotent=True, tr_cont=tr_cont,
    )


def _parse_fills(rows: list[Mapping[str, Any]]) -> list[DerivativeFill]:
    fills: list[DerivativeFill] = []
    for row in rows:
        order_id = str(row.get("odno", "")).strip()
        if not order_id:  # 주문번호 없는 패딩 행 -- 건너뜀
            continue
        fills.append(
            # 수치는 빈 값을 0으로 읽는다 -- 이유는 _parse_positions 와 동일.
            DerivativeFill(
                symbol=str(row.get("pdno", "")).strip(),
                name=str(row.get("prdt_name", "")).strip(),
                order_id=order_id,
                transaction_type=str(row.get("tr_type_name", "")).strip(),
                final_settlement_date=_parse_date(row.get("last_sttldt")),
                fill_index=_decimal_or_zero(row.get("ccld_idx"), "ccld_idx"),
                fill_quantity=_decimal_or_zero(row.get("ccld_qty"), "ccld_qty"),
                trade_amount=_decimal_or_zero(row.get("trad_amt"), "trad_amt"),
                fee=_decimal_or_zero(row.get("fee"), "fee"),
                fill_time=str(row.get("ccld_btwn", "")).strip(),
                _raw=row,
            )
        )
    return fills


def _fetch_commissions_page(
    transport: Transport, *, cano: str, product_code: str, start: str, end: str,
    ctx_fk: str, ctx_nk: str, tr_cont: str = "",
) -> RawResponse:
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "INQR_STRT_DAY": start,  # 조회시작일(YYYYMMDD)
        "INQR_END_DAY": end,     # 조회종료일(YYYYMMDD)
        "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
    }
    return transport.request(
        method="GET", path=_COMMISSIONS_PATH, tr_id=_COMMISSIONS_TR,
        params=params, idempotent=True, tr_cont=tr_cont,
    )


def _parse_commissions(rows: list[Mapping[str, Any]]) -> list[DerivativeCommission]:
    days: list[DerivativeCommission] = []
    for row in rows:
        order_date = str(row.get("ord_dt", "")).strip()
        symbol = str(row.get("pdno", "")).strip()
        if not order_date and not symbol:  # 주문일자·상품번호 모두 없는 패딩 행 -- 건너뜀
            continue
        days.append(
            # 수치는 빈 값을 0으로 읽는다 -- 이유는 _parse_positions 와 동일.
            DerivativeCommission(
                order_date=_parse_date(order_date),
                symbol=symbol,
                name=str(row.get("item_name", "")).strip(),
                sell_agreement_amount=_decimal_or_zero(row.get("sll_agrm_amt"), "sll_agrm_amt"),
                sell_fee=_decimal_or_zero(row.get("sll_fee"), "sll_fee"),
                buy_agreement_amount=_decimal_or_zero(row.get("buy_agrm_amt"), "buy_agrm_amt"),
                buy_fee=_decimal_or_zero(row.get("buy_fee"), "buy_fee"),
                total_fee=_decimal_or_zero(row.get("tot_fee_smtl"), "tot_fee_smtl"),
                realized_pnl=_decimal_or_zero(row.get("trad_pfls"), "trad_pfls"),
                _raw=row,
            )
        )
    return days


def _parse_settlement_positions(
    rows: list[Mapping[str, Any]]
) -> list[DerivativeSettlementPosition]:
    positions: list[DerivativeSettlementPosition] = []
    for row in rows:
        symbol = str(row.get("pdno", "")).strip()
        if not symbol:  # 상품번호 없는 패딩 행 -- 건너뜀
            continue
        positions.append(
            # 종목별 수치는 빈 값을 0으로 읽는다 -- 이유는 _parse_positions 와 동일.
            DerivativeSettlementPosition(
                symbol=symbol,
                name=str(row.get("prdt_name", "")).strip(),
                trade_type=str(row.get("trad_dvsn_name", "")).strip(),
                prior_quantity=_decimal_or_zero(row.get("bfdy_cblc_qty"), "bfdy_cblc_qty"),
                new_quantity=_decimal_or_zero(row.get("new_qty"), "new_qty"),
                offset_quantity=_decimal_or_zero(row.get("mnpl_rpch_qty"), "mnpl_rpch_qty"),
                quantity=_decimal_or_zero(row.get("cblc_qty"), "cblc_qty"),
                balance_amount=_decimal_or_zero(row.get("cblc_amt"), "cblc_amt"),
                realized_pnl=_decimal_or_zero(row.get("trad_pfls_amt"), "trad_pfls_amt"),
                market_value=_decimal_or_zero(row.get("evlu_amt"), "evlu_amt"),
                unrealized_pnl=_decimal_or_zero(row.get("evlu_pfls_amt"), "evlu_pfls_amt"),
                _raw=row,
            )
        )
    return positions


def _parse_valuation_positions(
    rows: list[Mapping[str, Any]]
) -> list[DerivativeValuationPosition]:
    positions: list[DerivativeValuationPosition] = []
    for row in rows:
        symbol = str(row.get("shtn_pdno", "")).strip()
        if not symbol:  # 단축상품번호 없는 패딩 행 -- 건너뜀
            continue
        positions.append(
            # 종목별 수치는 빈 값을 0으로 읽는다 -- 이유는 _parse_positions 와 동일.
            DerivativeValuationPosition(
                symbol=symbol,
                isin=str(row.get("pdno", "")).strip(),
                name=str(row.get("prdt_name", "")).strip(),
                side=str(row.get("sll_buy_dvsn_name", "")).strip(),
                quantity=_decimal_or_zero(row.get("cblc_qty1"), "cblc_qty1"),
                settle_price=_decimal_or_zero(row.get("excc_unpr"), "excc_unpr"),
                avg_price=_decimal_or_zero(row.get("ccld_avg_unpr1"), "ccld_avg_unpr1"),
                index_close=_decimal_or_zero(row.get("idx_clpr"), "idx_clpr"),
                purchase_amount=_decimal_or_zero(row.get("pchs_amt"), "pchs_amt"),
                market_value=_decimal_or_zero(row.get("evlu_amt"), "evlu_amt"),
                unrealized_pnl=_decimal_or_zero(row.get("evlu_pfls_amt"), "evlu_pfls_amt"),
                realized_pnl=_decimal_or_zero(row.get("trad_pfls_amt"), "trad_pfls_amt"),
                liquidatable_quantity=_decimal_or_zero(row.get("lqd_psbl_qty"), "lqd_psbl_qty"),
                _raw=row,
            )
        )
    return positions


def _parse_positions(rows: list[Mapping[str, Any]]) -> list[DerivativePosition]:
    positions: list[DerivativePosition] = []
    for row in rows:
        symbol = str(row.get("shtn_pdno", "")).strip()
        if not symbol:  # 단축상품번호 없는 패딩 행 -- 건너뜀
            continue
        positions.append(
            # 종목별 수치는 빈 값을 0으로 읽는다 -- 정산된 0수량 잔여 lot 에서 일부 필드가 빌 수
            # 있는데 그 한 종목 때문에 보유내역 전체 조회가 깨지면 안 된다(값이 있는데 파싱 실패면
            # 여전히 예외). 종목 식별은 위의 빈 shtn_pdno 가드가 이미 처리.
            DerivativePosition(
                symbol=symbol,
                isin=str(row.get("pdno", "")).strip(),
                name=str(row.get("prdt_name", "")).strip(),
                side=str(row.get("sll_buy_dvsn_name", "")).strip(),
                quantity=_decimal_or_zero(row.get("cblc_qty"), "cblc_qty"),
                settle_price=_decimal_or_zero(row.get("excc_unpr"), "excc_unpr"),
                avg_price=_decimal_or_zero(row.get("ccld_avg_unpr1"), "ccld_avg_unpr1"),
                purchase_amount=_decimal_or_zero(row.get("pchs_amt"), "pchs_amt"),
                market_value=_decimal_or_zero(row.get("evlu_amt"), "evlu_amt"),
                unrealized_pnl=_decimal_or_zero(row.get("evlu_pfls_amt"), "evlu_pfls_amt"),
                realized_pnl=_decimal_or_zero(row.get("trad_pfls_amt"), "trad_pfls_amt"),
                liquidatable_quantity=_decimal_or_zero(row.get("lqd_psbl_qty"), "lqd_psbl_qty"),
                _raw=row,
            )
        )
    return positions


def _extract_summary(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """계좌 요약(output2) -- KIS가 '길이 1 배열'로도, 단일 객체로도 준다. 비매핑이면 None."""
    summary = body.get("output2")
    if isinstance(summary, list):
        first = summary[0] if summary else None
        return first if isinstance(first, Mapping) else None
    if isinstance(summary, Mapping):
        return summary
    return None


def _parse_date(value: object) -> date | None:
    """``"20240216"`` -> ``date(2024, 2, 16)``. 공백/형식오류면 None(fail-soft)."""
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def _require_wire_date(value: str, field_name: str) -> None:
    """조회 요청의 일자 파라미터를 와이어 이전에 검증한다 -- 8자리 숫자(YYYYMMDD)가 아니면
    :class:`KISUsageError`. 잘못된 일자로 조회를 날리는 대신 호출 즉시 실패시킨다."""
    if len(value) != 8 or not value.isdigit():
        raise KISUsageError(
            f"일자 파라미터 {field_name!r} 는 8자리 숫자(YYYYMMDD)여야 한다: {value!r}"
        )


def _decimal_or_zero(value: object, field_name: str) -> Decimal:
    """없으면 0, 있으면 Decimal(파싱 실패면 예외). '없음=0'인 수량·금액 필드용.

    부재(None)만 0으로 본다 -- 값 "0"도 Decimal(0)이라 결과는 같지만, 판정을 truthiness 가
    아니라 명시적 None 검사로 해 의도를 분명히 한다.
    """
    amount = optional_decimal(value, field_name)
    return Decimal(0) if amount is None else amount
