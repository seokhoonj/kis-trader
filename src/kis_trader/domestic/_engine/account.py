"""국내주식 계좌 조회 (내부) -- 잔고/보유종목/매수가능/매도가능.

사용자면(KISClient/종목 핸들)이 이 함수들을 호출해 통합 반환 타입을 받는다. 계좌 식별정보
(``cano``/``product_code``)와 환경(``environment``)은 세션에서 온다. KIS 원본 필드 매핑과
fail-closed 파싱은 여기 갇힌다.

KIS URL/TR-ID:
- 잔고: ``GET .../trading/inquire-balance`` (실전 ``TTTC8434R`` / 모의 ``VTTC8434R``).
- 매수가능: ``GET .../trading/inquire-psbl-order`` (실전 ``TTTC8908R`` / 모의 ``VTTC8908R``).
- 매도가능수량: ``GET .../trading/inquire-psbl-sell`` (``TTTC8408R``, **모의 미지원**).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from ..._internal._datetime import _KST, _parse_hhmmss
from ..._internal._datetime import _parse_optional_date as _parse_date
from ..._internal._response import (
    _advance_cursor,
    _fetch_paginated_rows,
    _fetch_paginated_rows_with_summary,
    _raise_if_error,
)
from ..._internal._wire import field_decimal_or_zero, format_wire_decimal, required_decimal
from ...errors import KISError, KISUsageError
from ...instrument import _DOMESTIC_ORDER_EXCHANGES, DomesticOrderExchange
from ...open_order import DomesticOpenOrder
from ...order import CreditType
from ...orderable import DomesticBuyableAmount, SellableQuantity
from ...transport import Environment, RawResponse, Transport
from ..entities.account_reports import (
    IntegratedMargin,
    RealizedProfitBalance,
    RealizedProfitPosition,
)
from ..entities.account_right import AccountRight
from ..entities.balance import AccountAssets, DomesticBalance, DomesticPosition, Portfolio
from ..entities.stock_fills import StockOrder, StockOrderHistory, StopTrigger
from ..entities.trade_profit import (
    DailyProfit,
    DailyProfitHistory,
    TradeProfit,
    TradeProfitHistory,
)
from ._parse import _side_from_code

if TYPE_CHECKING:
    from ..._literals import Numeric, ProfitSort, SideFilter

_logger = logging.getLogger("kis_trader.domestic")

_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
_BALANCE_TR = {"real": "TTTC8434R", "paper": "VTTC8434R"}
#: 잔고 종목배열 연속조회 페이지 상한. 여기 닿으면 부분 결과로 자르지 않고 fail-closed.
_MAX_BALANCE_PAGES = 100

_BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
_BUYABLE_TR = {"real": "TTTC8908R", "paper": "VTTC8908R"}
_SELLABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-sell"
_SELLABLE_TR = "TTTC8408R"  # 모의투자 미지원 -- demo TR 없음

_CREDIT_BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-credit-psamount"
_CREDIT_BUYABLE_TR = "TTTC8909R"  # 모의투자 미지원

_ASSETS_PATH = "/uapi/domestic-stock/v1/trading/inquire-account-balance"
_ASSETS_TR = "CTRP6548R"  # 모의투자 미지원

_TRADE_PROFIT_PATH = "/uapi/domestic-stock/v1/trading/inquire-period-trade-profit"
_TRADE_PROFIT_TR = "TTTC8715R"  # 모의투자 미지원
#: 매매손익 연속조회 페이지 상한. 닿으면 fail-closed.
_MAX_TRADE_PROFIT_PAGES = 100
#: 정렬 -> SORT_DVSN. recent:최근순/oldest:과거순.
_TRADE_PROFIT_SORT = {"recent": "00", "oldest": "01"}

_DAILY_PROFIT_PATH = "/uapi/domestic-stock/v1/trading/inquire-period-profit"
_DAILY_PROFIT_TR = "TTTC8708R"  # 모의투자 미지원

_RIGHTS_PATH = "/uapi/domestic-stock/v1/trading/period-rights"
_RIGHTS_TR = "CTRGA011R"  # 모의투자 미지원
#: 신용유형(KIS 코드표). 21 자기융자신규/22 유통대주신규/23 유통융자신규/24 자기대주신규/
#: 25 자기융자상환/26 유통대주상환/27 유통융자상환/28 자기대주상환.
_CREDIT_TYPES = frozenset({"21", "22", "23", "24", "25", "26", "27", "28"})

_OPEN_ORDERS_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
_OPEN_ORDERS_TR = "TTTC0084R"  # 정정취소가능주문조회, 모의투자 미지원

_REALIZED_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance-rlz-pl"
_REALIZED_BALANCE_TR = "TTTC8494R"  # 주식잔고조회_실현손익, 모의투자 미지원
#: 실현손익 잔고 종목배열 연속조회 페이지 상한. 닿으면 fail-closed.
_MAX_REALIZED_PAGES = 100

_INTEGRATED_MARGIN_PATH = "/uapi/domestic-stock/v1/trading/intgr-margin"
_INTEGRATED_MARGIN_TR = "TTTC0869R"  # 주식통합증거금 현황, 모의투자 미지원

_STOCK_FILLS_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
_STOCK_FILLS_TR = {"real": "TTTC0081R", "paper": "VTTC0081R"}  # 주식일별주문체결조회(3개월 이내)
# 같은 엔드포인트의 3개월 이전 내역 -- tr_id 만 다르고 경로·파라미터·응답 스키마는 동일하다.
_STOCK_FILLS_OLDER_TR = {"real": "CTSC9215R", "paper": "VTSC9215R"}  # 주식일별주문체결조회(3개월 이전)
_MAX_STOCK_FILLS_PAGES = 200
_STOCK_FILLS_SIDE = {"all": "00", "sell": "01", "buy": "02"}
#: 미체결 주문 연속조회 페이지 상한(한 콜 최대 50건). 닿으면 fail-closed.
_MAX_OPEN_ORDER_PAGES = 100


# --- 잔고 / 보유종목 / 포트폴리오 -----------------------------------------
def fetch_balance(transport: Transport, *, cano: str, product_code: str, environment: Environment) -> DomesticBalance:
    """계좌 현금·자산 요약(1콜, output2). 요약은 계좌 단위라 첫 페이지로 완결."""
    resp = _fetch_balance_page(transport, cano=cano, product_code=product_code, environment=environment, ctx_fk="", ctx_nk="")
    _raise_if_error(resp)
    summary = _extract_summary(resp.body)
    if summary is None:
        raise KISError(
            "잔고 응답에 계좌 요약(output2)이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return _parse_balance(summary)


def fetch_positions(transport: Transport, *, cano: str, product_code: str, environment: Environment) -> list[DomesticPosition]:
    """보유 종목 전체(연속조회 소진까지). 0수량 잔여 lot 포함."""
    rows, _summary = _walk_holdings(transport, cano=cano, product_code=product_code, environment=environment)
    return _parse_positions(rows)


def fetch_portfolio(transport: Transport, *, cano: str, product_code: str, environment: Environment) -> Portfolio:
    """현금·자산 요약과 보유 종목을 한 번의 조회 순회로 함께."""
    rows, summary = _walk_holdings(transport, cano=cano, product_code=product_code, environment=environment)
    if summary is None:
        raise KISError("잔고 응답에 계좌 요약(output2)이 없다.")
    return Portfolio(balance=_parse_balance(summary), positions=tuple(_parse_positions(rows)))


def _walk_holdings(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any] | None]:
    """잔고 보유내역(output1) + 계좌 요약(output2)을 공유 연속조회 상태기계에 위임한다.
    비진전 커서 방어와 페이지 상한 fail-closed 는 공유 헬퍼가 담당한다."""
    base_params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
        "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    }
    return _fetch_paginated_rows_with_summary(
        transport, path=_BALANCE_PATH, tr_id=_BALANCE_TR[environment],
        base_params=base_params, output_key="output1", max_pages=_MAX_BALANCE_PAGES,
        cap_message=(
            f"잔고 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        ),
        summary_from=_extract_summary, ctx_width=100,
    )


def _fetch_balance_page(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    ctx_fk: str, ctx_nk: str, tr_cont: str = "",
) -> RawResponse:
    """잔고 1페이지 조회 -- 계좌 요약(output2)만 필요한 단일-페이지 호출자(:func:`fetch_balance`)가 쓴다.
    보유내역 전체 순회는 :func:`_walk_holdings`(공유 페이지네이터)가 담당한다."""
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
        "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00", "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk,
    }
    return transport.request(
        method="GET", path=_BALANCE_PATH, tr_id=_BALANCE_TR[environment],
        params=params, idempotent=True, tr_cont=tr_cont,
    )


def _parse_positions(rows: list[Mapping[str, Any]]) -> list[DomesticPosition]:
    positions: list[DomesticPosition] = []
    for row in rows:
        symbol = str(row.get("pdno", "")).strip()
        if not symbol:  # 종목코드 없는 패딩 행 -- 건너뜀
            continue
        positions.append(
            # 종목별 수치는 빈 값을 0으로 읽는다 -- 정산 완료된 0수량 잔여 lot 등에서 일부 필드가
            # 빌 수 있는데, 그 한 종목 때문에 보유목록 전체 조회가 깨지면 안 된다(값이 있는데
            # 파싱 실패면 여전히 예외). 종목 식별은 위의 빈 pdno 가드가 이미 처리.
            DomesticPosition(
                symbol=symbol,
                name=str(row.get("prdt_name", "")).strip(),
                currency="KRW",
                quantity=field_decimal_or_zero(row.get("hldg_qty"), "hldg_qty"),
                sellable_quantity=field_decimal_or_zero(row.get("ord_psbl_qty"), "ord_psbl_qty"),
                average_purchase_price=field_decimal_or_zero(row.get("pchs_avg_pric"), "pchs_avg_pric"),
                purchase_amount=field_decimal_or_zero(row.get("pchs_amt"), "pchs_amt"),
                current_price=field_decimal_or_zero(row.get("prpr"), "prpr"),
                market_value=field_decimal_or_zero(row.get("evlu_amt"), "evlu_amt"),
                unrealized_pnl=field_decimal_or_zero(row.get("evlu_pfls_amt"), "evlu_pfls_amt"),
                unrealized_pnl_percent=field_decimal_or_zero(row.get("evlu_pfls_rt"), "evlu_pfls_rt"),
                change=field_decimal_or_zero(row.get("bfdy_cprs_icdc"), "bfdy_cprs_icdc"),
                change_percent=field_decimal_or_zero(row.get("fltt_rt"), "fltt_rt"),
                evaluation_return_percent=field_decimal_or_zero(row.get("evlu_erng_rt"), "evlu_erng_rt"),
                trade_type_name=str(row.get("trad_dvsn_name", "")).strip(),
                substitute_price=field_decimal_or_zero(row.get("sbst_pric"), "sbst_pric"),
                margin_rate_name=str(row.get("item_mgna_rt_name", "")).strip(),
                guarantee_rate_name=str(row.get("grta_rt_name", "")).strip(),
                stock_loan_unit_price=field_decimal_or_zero(row.get("stck_loan_unpr"), "stck_loan_unpr"),
                previous_day_buy_quantity=field_decimal_or_zero(row.get("bfdy_buy_qty"), "bfdy_buy_qty"),
                previous_day_sell_quantity=field_decimal_or_zero(row.get("bfdy_sll_qty"), "bfdy_sll_qty"),
                today_buy_quantity=field_decimal_or_zero(row.get("thdt_buyqty"), "thdt_buyqty"),
                today_sell_quantity=field_decimal_or_zero(row.get("thdt_sll_qty"), "thdt_sll_qty"),
                loan_amount=field_decimal_or_zero(row.get("loan_amt"), "loan_amt"),
                short_sell_amount=field_decimal_or_zero(row.get("stln_slng_chgs"), "stln_slng_chgs"),
                loan_date=_parse_date(row.get("loan_dt")),
                expiry_date=_parse_date(row.get("expd_dt")),
                _raw=row,
            )
        )
    return positions


def _parse_balance(summary: Mapping[str, Any]) -> DomesticBalance:
    return DomesticBalance(
        currency="KRW",
        deposit=required_decimal(summary.get("dnca_tot_amt"), "dnca_tot_amt"),
        settlement_cash_d1=required_decimal(summary.get("nxdy_excc_amt"), "nxdy_excc_amt"),
        settlement_cash_d2=required_decimal(summary.get("prvs_rcdl_excc_amt"), "prvs_rcdl_excc_amt"),
        total_evaluation=required_decimal(summary.get("tot_evlu_amt"), "tot_evlu_amt"),
        net_asset=required_decimal(summary.get("nass_amt"), "nass_amt"),
        purchase_amount=required_decimal(summary.get("pchs_amt_smtl_amt"), "pchs_amt_smtl_amt"),
        market_value=required_decimal(summary.get("evlu_amt_smtl_amt"), "evlu_amt_smtl_amt"),
        unrealized_pnl=required_decimal(summary.get("evlu_pfls_smtl_amt"), "evlu_pfls_smtl_amt"),
        securities_evaluation=field_decimal_or_zero(summary.get("scts_evlu_amt"), "scts_evlu_amt"),
        total_loan_amount=field_decimal_or_zero(summary.get("tot_loan_amt"), "tot_loan_amt"),
        previous_day_total_asset=field_decimal_or_zero(summary.get("bfdy_tot_asst_evlu_amt"), "bfdy_tot_asst_evlu_amt"),
        asset_change_amount=field_decimal_or_zero(summary.get("asst_icdc_amt"), "asst_icdc_amt"),
        asset_change_percent=field_decimal_or_zero(summary.get("asst_icdc_erng_rt"), "asst_icdc_erng_rt"),
        cma_evaluation=field_decimal_or_zero(summary.get("cma_evlu_amt"), "cma_evlu_amt"),
        previous_day_buy_amount=field_decimal_or_zero(summary.get("bfdy_buy_amt"), "bfdy_buy_amt"),
        today_buy_amount=field_decimal_or_zero(summary.get("thdt_buy_amt"), "thdt_buy_amt"),
        previous_day_sell_amount=field_decimal_or_zero(summary.get("bfdy_sll_amt"), "bfdy_sll_amt"),
        today_sell_amount=field_decimal_or_zero(summary.get("thdt_sll_amt"), "thdt_sll_amt"),
        previous_day_expenses=field_decimal_or_zero(summary.get("bfdy_tlex_amt"), "bfdy_tlex_amt"),
        today_expenses=field_decimal_or_zero(summary.get("thdt_tlex_amt"), "thdt_tlex_amt"),
        d2_auto_redemption_amount=field_decimal_or_zero(summary.get("d2_auto_rdpt_amt"), "d2_auto_rdpt_amt"),
        next_day_auto_redemption_amount=field_decimal_or_zero(summary.get("nxdy_auto_rdpt_amt"), "nxdy_auto_rdpt_amt"),
        total_short_sell_amount=field_decimal_or_zero(summary.get("tot_stln_slng_chgs"), "tot_stln_slng_chgs"),
        financing_auto_redemption_enabled=str(summary.get("fncg_gld_auto_rdpt_yn", "")).strip().upper() == "Y",
        _raw=summary,
    )


def _mapping_or_none(body: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    """응답 바디의 요약 블록을 매핑이면 그대로, 아니면(부재/비매핑) None 으로 -- 공유 페이지네이터의
    ``summary_from`` 에 넘기는 기간 총계(output2) 추출기."""
    candidate = body.get(key)
    return candidate if isinstance(candidate, Mapping) else None


def _extract_summary(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """잔고 요약(output2) -- KIS가 '길이 1 배열'로도, 단일 객체로도 준다. 비매핑이면 None."""
    summary = body.get("output2")
    if isinstance(summary, (list, tuple)):
        first = summary[0] if summary else None
        return first if isinstance(first, Mapping) else None
    if isinstance(summary, Mapping):
        return summary
    return None


# --- 매수가능 / 매도가능 ---------------------------------------------------
def fetch_buyable_amount(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    symbol: str | None = None, limit_price: Numeric | None = None,
) -> DomesticBuyableAmount:
    """매수가능 여력. ``symbol`` 없으면 금액만(수량 0). ``limit_price`` 있으면 지정가 기준."""
    if symbol is None and limit_price is not None:
        raise KISUsageError("limit_price 는 symbol 과 함께 줘야 한다(금액만 조회엔 단가 무의미).")
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "PDNO": symbol or "",
        "ORD_UNPR": _format_order_unit_price(limit_price),
        "ORD_DVSN": "00" if limit_price is not None else "01",  # 지정가/시장가
        "CMA_EVLU_AMT_ICLD_YN": "N", "OVRS_ICLD_YN": "N",
    }
    resp = transport.request(
        method="GET", path=_BUYABLE_PATH, tr_id=_BUYABLE_TR[environment], params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise KISError(
            "매수가능조회 응답에 output 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return _parse_buyable(output, symbol=symbol or "")


def fetch_sellable(
    transport: Transport, *, cano: str, product_code: str, environment: Environment, symbol: str
) -> SellableQuantity:
    """매도가능 수량. **모의투자 미지원**(demo면 사전 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError(
            "매도가능수량조회(inquire-psbl-sell)는 모의투자 미지원 -- 실전에서만. "
            "모의에선 잔고의 sellable_quantity 를 참고하라."
        )
    params = {"CANO": cano, "ACNT_PRDT_CD": product_code, "PDNO": symbol}
    resp = transport.request(
        method="GET", path=_SELLABLE_PATH, tr_id=_SELLABLE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output1 = resp.body.get("output1")
    if not isinstance(output1, Mapping):     # KIS 는 이 단일 결과를 output 으로 준다
        output1 = resp.body.get("output")
    if not isinstance(output1, Mapping):
        raise KISError(
            "매도가능수량조회 응답에 output/output1 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return _parse_sellable(output1, symbol=symbol)


def fetch_credit_buyable(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    symbol: str, credit_type: CreditType, limit_price: Numeric | None = None,
) -> DomesticBuyableAmount:
    """신용(융자/대주) 매수가능 여력. ``credit_type`` 은 신용유형(21 자기융자신규 등),
    ``limit_price`` 없으면 시장가 기준. **모의투자 미지원**.

    현금 매수가능(:func:`fetch_buyable_amount`)과 output 형상이 같아 :class:`DomesticBuyableAmount` 를 공유한다
    -- 신용 전용 필드(주문가능대용·펀드환매대금·CMA평가금액 등)는 ``_raw`` 로 접근한다.
    """
    if environment == "paper":
        raise KISUsageError(
            "신용매수가능조회(inquire-credit-psamount)는 모의투자 미지원 -- 실전에서만."
        )
    if credit_type not in _CREDIT_TYPES:
        raise KISUsageError(
            f"지원하지 않는 신용유형: {credit_type!r} ({'/'.join(sorted(_CREDIT_TYPES))})."
        )
    unit_price = _format_order_unit_price(limit_price)
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "PDNO": symbol,
        "ORD_UNPR": unit_price or "0",  # 시장가면 공란 대신 "0"(KIS 명세 권고)
        "ORD_DVSN": "00" if limit_price is not None else "01",  # 지정가/시장가
        "CRDT_TYPE": credit_type,
        "CMA_EVLU_AMT_ICLD_YN": "N", "OVRS_ICLD_YN": "N",
    }
    resp = transport.request(
        method="GET", path=_CREDIT_BUYABLE_PATH, tr_id=_CREDIT_BUYABLE_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise KISError(
            "신용매수가능조회 응답에 output 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return _parse_buyable(output, symbol=symbol)


def _parse_buyable(output: Mapping[str, Any], *, symbol: str) -> DomesticBuyableAmount:
    return DomesticBuyableAmount(
        symbol=symbol,
        currency="KRW",
        orderable_cash=required_decimal(output.get("ord_psbl_cash"), "ord_psbl_cash"),
        reusable_cash=required_decimal(output.get("ruse_psbl_amt"), "ruse_psbl_amt"),
        cash_buyable_amount=field_decimal_or_zero(output.get("nrcvb_buy_amt"), "nrcvb_buy_amt"),
        cash_buyable_quantity=field_decimal_or_zero(output.get("nrcvb_buy_qty"), "nrcvb_buy_qty"),
        max_buyable_amount=field_decimal_or_zero(output.get("max_buy_amt"), "max_buy_amt"),
        max_buyable_quantity=field_decimal_or_zero(output.get("max_buy_qty"), "max_buy_qty"),
        _raw=output,
    )


def _parse_sellable(output1: Mapping[str, Any], *, symbol: str) -> SellableQuantity:
    return SellableQuantity(
        symbol=symbol,
        name=str(output1.get("prdt_name", "")).strip(),
        quantity=field_decimal_or_zero(output1.get("cblc_qty"), "cblc_qty"),
        sellable_quantity=field_decimal_or_zero(output1.get("ord_psbl_qty"), "ord_psbl_qty"),
        _raw=output1,
    )


# --- 기간별 매매손익 -------------------------------------------------------
def fetch_trade_profits(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str, symbol: str | None = None, sort: ProfitSort = "recent",
) -> TradeProfitHistory:
    """기간별 매매손익(실현손익). ``start``/``end`` 는 기간(YYYYMMDD), ``symbol`` 없으면 전체,
    ``sort`` = recent/oldest. output1 종목행을 연속조회로 모으고 output2 총계를 함께 담는다.
    **모의투자 미지원**."""
    if environment == "paper":
        raise KISUsageError(
            "기간별매매손익현황조회(inquire-period-trade-profit)는 모의투자 미지원 -- 실전에서만."
        )
    try:
        sort_code = _TRADE_PROFIT_SORT[sort]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 sort: {sort!r} ({'/'.join(_TRADE_PROFIT_SORT)})."
        ) from None
    base_params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "SORT_DVSN": sort_code, "PDNO": symbol or "",
        "INQR_STRT_DT": start, "INQR_END_DT": end, "CBLC_DVSN": "00",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    }
    rows, summary = _fetch_paginated_rows_with_summary(
        transport, path=_TRADE_PROFIT_PATH, tr_id=_TRADE_PROFIT_TR,
        base_params=base_params, output_key="output1", max_pages=_MAX_TRADE_PROFIT_PAGES,
        cap_message=(
            f"매매손익 조회가 {_MAX_TRADE_PROFIT_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다."
        ),
        summary_from=lambda body: _mapping_or_none(body, "output2"), ctx_width=100,
    )
    if summary is None:
        raise KISError("매매손익 응답에 총계(output2)가 없다.")
    trades = tuple(
        _parse_trade_profit(row) for row in rows if str(row.get("pdno", "")).strip()
    )
    return TradeProfitHistory(
        trades=trades,
        total_realized_pnl=required_decimal(summary.get("tot_rlzt_pfls"), "tot_rlzt_pfls"),
        total_return_percent=required_decimal(summary.get("tot_pftrt"), "tot_pftrt"),
        total_buy_amount=required_decimal(summary.get("buy_tr_amt_smtl"), "buy_tr_amt_smtl"),
        total_sell_amount=required_decimal(summary.get("sll_tr_amt_smtl"), "sll_tr_amt_smtl"),
        total_fee=required_decimal(summary.get("tot_fee"), "tot_fee"),
        total_tax=required_decimal(summary.get("tot_tltx"), "tot_tltx"),
        _raw=summary,
    )


def _parse_trade_profit(row: Mapping[str, Any]) -> TradeProfit:
    return TradeProfit(
        trade_date=_parse_date(row.get("trad_dt")),
        symbol=str(row.get("pdno", "")).strip(),
        name=str(row.get("prdt_name", "")).strip(),
        trade_type_name=str(row.get("trad_dvsn_name", "")).strip(),
        holding_quantity=field_decimal_or_zero(row.get("hldg_qty"), "hldg_qty"),
        purchase_price=field_decimal_or_zero(row.get("pchs_unpr"), "pchs_unpr"),
        buy_quantity=field_decimal_or_zero(row.get("buy_qty"), "buy_qty"),
        buy_amount=field_decimal_or_zero(row.get("buy_amt"), "buy_amt"),
        sell_price=field_decimal_or_zero(row.get("sll_pric"), "sll_pric"),
        sell_quantity=field_decimal_or_zero(row.get("sll_qty"), "sll_qty"),
        sell_amount=field_decimal_or_zero(row.get("sll_amt"), "sll_amt"),
        realized_pnl=field_decimal_or_zero(row.get("rlzt_pfls"), "rlzt_pfls"),
        return_percent=field_decimal_or_zero(row.get("pfls_rt"), "pfls_rt"),
        fee=field_decimal_or_zero(row.get("fee"), "fee"),
        tax=field_decimal_or_zero(row.get("tl_tax"), "tl_tax"),
        loan_interest=field_decimal_or_zero(row.get("loan_int"), "loan_int"),
        _raw=row,
    )


def fetch_daily_profits(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str, symbol: str | None = None, sort: ProfitSort = "recent",
) -> DailyProfitHistory:
    """기간별 일별 매매손익 합산. 파라미터는 :func:`fetch_trade_profits` 와 같되 output1 이 하루
    단위(종목 구분 없음)다. **모의투자 미지원**."""
    if environment == "paper":
        raise KISUsageError(
            "기간별손익일별합산조회(inquire-period-profit)는 모의투자 미지원 -- 실전에서만."
        )
    try:
        sort_code = _TRADE_PROFIT_SORT[sort]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 sort: {sort!r} ({'/'.join(_TRADE_PROFIT_SORT)})."
        ) from None
    base_params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "INQR_STRT_DT": start, "INQR_END_DT": end, "PDNO": symbol or "",
        "SORT_DVSN": sort_code, "INQR_DVSN": "00", "CBLC_DVSN": "00",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    }
    rows, summary = _fetch_paginated_rows_with_summary(
        transport, path=_DAILY_PROFIT_PATH, tr_id=_DAILY_PROFIT_TR,
        base_params=base_params, output_key="output1", max_pages=_MAX_TRADE_PROFIT_PAGES,
        cap_message=(
            f"일별손익 조회가 {_MAX_TRADE_PROFIT_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다."
        ),
        summary_from=lambda body: _mapping_or_none(body, "output2"), ctx_width=100,
    )
    if summary is None:
        raise KISError("일별손익 응답에 총계(output2)가 없다.")
    days = tuple(_parse_daily_profit(row) for row in rows if str(row.get("trad_dt", "")).strip())
    return DailyProfitHistory(
        days=days,
        total_realized_pnl=required_decimal(summary.get("tot_rlzt_pfls"), "tot_rlzt_pfls"),
        total_buy_amount=required_decimal(summary.get("buy_tr_amt_smtl"), "buy_tr_amt_smtl"),
        total_sell_amount=required_decimal(summary.get("sll_tr_amt_smtl"), "sll_tr_amt_smtl"),
        total_fee=required_decimal(summary.get("tot_fee"), "tot_fee"),
        total_tax=required_decimal(summary.get("tot_tltx"), "tot_tltx"),
        _raw=summary,
    )


def _parse_daily_profit(row: Mapping[str, Any]) -> DailyProfit:
    return DailyProfit(
        trade_date=_parse_date(row.get("trad_dt")),
        buy_amount=field_decimal_or_zero(row.get("buy_amt"), "buy_amt"),
        sell_amount=field_decimal_or_zero(row.get("sll_amt"), "sll_amt"),
        realized_pnl=field_decimal_or_zero(row.get("rlzt_pfls"), "rlzt_pfls"),
        return_percent=field_decimal_or_zero(row.get("pfls_rt"), "pfls_rt"),
        fee=field_decimal_or_zero(row.get("fee"), "fee"),
        tax=field_decimal_or_zero(row.get("tl_tax"), "tl_tax"),
        loan_interest=field_decimal_or_zero(row.get("loan_int"), "loan_int"),
        buy_quantity=field_decimal_or_zero(row.get("buy_qty1"), "buy_qty1"),
        sell_quantity=field_decimal_or_zero(row.get("sll_qty1"), "sll_qty1"),
        _raw=row,
    )


def fetch_stock_order_history(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str, side: SideFilter = "all", symbol: str | None = None,
    unfilled_only: bool = False, older_than_three_months: bool = False,
) -> StockOrderHistory:
    """국내주식 일별 주문·체결 내역(개별 행 + 기간 합계 요약). ``start``~``end`` (YYYYMMDD) 기간,
    ``side`` = ``"all"``/``"sell"``/``"buy"``, ``symbol`` 없으면 전체, ``unfilled_only`` 면 미체결만.
    ``older_than_three_months`` 면 3개월 **이전** 내역(그 외에는 3개월 **이내**). 두 구간은 상호
    배타라 한 콜로는 한쪽만 받는다(KIS 설계).

    output1 체결 행을 연속조회로 소진까지 모으고, 기간 합계(output2)는 첫 페이지에서 완결한다
    (기간 단위라 페이지 불변). ``GET .../domestic-stock/v1/trading/inquire-daily-ccld`` -- 3개월
    이내는 실전 ``TTTC0081R`` / 모의 ``VTTC0081R``, 3개월 이전은 실전 ``CTSC9215R`` / 모의
    ``VTSC9215R`` (tr_id 만 다르고 경로·파라미터·응답은 동일). 실전·모의 모두 지원한다."""
    try:
        side_code = _STOCK_FILLS_SIDE[side]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 side: {side!r} ({'/'.join(_STOCK_FILLS_SIDE)})."
        ) from None
    base_params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "INQR_STRT_DT": start, "INQR_END_DT": end,
        "SLL_BUY_DVSN_CD": side_code, "PDNO": symbol or "",
        "ORD_GNO_BRNO": "", "ODNO": "",
        "CCLD_DVSN": "02" if unfilled_only else "00",
        "INQR_DVSN": "00", "INQR_DVSN_1": "", "INQR_DVSN_3": "00",
        "EXCG_ID_DVSN_CD": "", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    }
    tr_table = _STOCK_FILLS_OLDER_TR if older_than_three_months else _STOCK_FILLS_TR
    rows, summary = _fetch_paginated_rows_with_summary(
        transport, path=_STOCK_FILLS_PATH, tr_id=tr_table[environment],
        base_params=base_params, output_key="output1", max_pages=_MAX_STOCK_FILLS_PAGES,
        cap_message=(
            f"주식일별주문체결조회가 {_MAX_STOCK_FILLS_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다."
        ),
        summary_from=lambda body: (
            body.get("output2") if isinstance(body.get("output2"), Mapping) else None
        ),
    )
    if summary is None:
        raise KISError("주식일별주문체결조회 응답에 합계 요약(output2)이 없다.")
    return StockOrderHistory(
        orders=tuple(_parse_stock_orders(rows)),
        total_order_quantity=required_decimal(summary.get("tot_ord_qty"), "tot_ord_qty"),
        total_filled_quantity=required_decimal(summary.get("tot_ccld_qty"), "tot_ccld_qty"),
        total_filled_amount=required_decimal(summary.get("tot_ccld_amt"), "tot_ccld_amt"),
        average_purchase_price=required_decimal(summary.get("pchs_avg_pric"), "pchs_avg_pric"),
        estimated_expenses=required_decimal(summary.get("prsm_tlex_smtl"), "prsm_tlex_smtl"),
        _raw=summary,
    )


def _parse_fill_exchange(value: object, warned: set[str]) -> DomesticOrderExchange | None:
    """체결 거래소ID구분(excg_id_dvsn_cd)을 타입드 거래소로. NXT 도입 전 주문은 미채움(공란)->None.

    이건 조회(READ) 경로다 -- KIS 가 새 거래소(ATS) 코드를 추가하면(NXT 자체가 2025 신설) 모르는
    코드 한 건이 전체 체결내역 조회를 깨뜨려선 안 된다. 그래서 미지 코드는 경고를 남기고 None 으로
    저하한다(원본 코드는 ``_raw['excg_id_dvsn_cd']`` 에 보존 -- 조용한 손실 아님). 주문 생성 경로는
    여전히 fail-closed(모르는 거래소로는 주문하지 않는다 -- Order.__post_init__ 가 거부). ``warned``
    는 한 조회 안에서 같은 미지 코드가 여러 행에 있어도 경고를 코드당 한 번만 내게 하는 집합이다."""
    code = str(value or "").strip()
    if not code:
        return None
    if code not in _DOMESTIC_ORDER_EXCHANGES:
        if code not in warned:
            warned.add(code)
            _logger.warning(
                "알 수 없는 체결 거래소ID구분 excg_id_dvsn_cd=%r -- None 으로 저하(원본은 _raw 에 보존)", code,
            )
        return None
    return cast("DomesticOrderExchange", code)


def _parse_stop_datetime(value: object) -> datetime | None:
    """스톱지정가 효력발생시각(stpm_efct_occr_dtmd, YYYYMMDDHHMMSS). 미발생/공란/비규격이면 None."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=_KST)
    except ValueError:
        return None


def _parse_stop_trigger(row: Mapping[str, Any]) -> StopTrigger | None:
    """스톱지정가 트리거. 조건가격(stpm_cndt_pric)이 0/공란이면 비-스톱 주문 -> None(joint-presence)."""
    trigger = field_decimal_or_zero(row.get("stpm_cndt_pric"), "stpm_cndt_pric")
    if trigger == 0:
        return None
    return StopTrigger(
        trigger_price=trigger, effective_datetime=_parse_stop_datetime(row.get("stpm_efct_occr_dtmd"))
    )


def _parse_stock_orders(rows: list[Mapping[str, Any]]) -> list[StockOrder]:
    # 행 배열의 매핑 여부는 상류 _require_mapping_rows 가 이미 fail-closed 로 보장한다.
    orders: list[StockOrder] = []
    warned_exchanges: set[str] = set()  # 미지 거래소 코드 경고를 조회당 코드별 1회로 집약
    for row in rows:
        order_id = str(row.get("odno", "")).strip()
        if not order_id:  # 주문번호 없는 패딩 행 -- 건너뜀
            continue
        orders.append(
            StockOrder(
                order_date=_parse_date(row.get("ord_dt")),
                order_id=order_id,
                original_order_id=str(row.get("orgn_odno", "")).strip(),
                order_type_name=str(row.get("ord_dvsn_name", "")).strip(),
                side=_side_from_code(row.get("sll_buy_dvsn_cd")),
                symbol=str(row.get("pdno", "")).strip(),
                name=str(row.get("prdt_name", "")).strip(),
                order_quantity=field_decimal_or_zero(row.get("ord_qty"), "ord_qty"),
                order_price=field_decimal_or_zero(row.get("ord_unpr"), "ord_unpr"),
                order_time=_parse_hhmmss(row.get("ord_tmd")),
                filled_quantity=field_decimal_or_zero(row.get("tot_ccld_qty"), "tot_ccld_qty"),
                average_price=field_decimal_or_zero(row.get("avg_prvs"), "avg_prvs"),
                filled_amount=field_decimal_or_zero(row.get("tot_ccld_amt"), "tot_ccld_amt"),
                unfilled_quantity=field_decimal_or_zero(row.get("rmn_qty"), "rmn_qty"),
                rejected_quantity=field_decimal_or_zero(row.get("rjct_qty"), "rjct_qty"),
                canceled=str(row.get("cncl_yn", "")).strip().upper() == "Y",
                canceled_quantity=field_decimal_or_zero(row.get("cnc_cfrm_qty"), "cnc_cfrm_qty"),
                branch_number=str(row.get("ord_gno_brno", "")).strip(),
                exchange_division_code=str(row.get("excg_dvsn_cd", "")).strip(),
                fill_condition_name=str(row.get("ccld_cndt_name", "")).strip(),
                order_type_code=str(row.get("ord_dvsn_cd", "")).strip(),
                product_type_code=str(row.get("prdt_type_cd", "")).strip(),
                order_organization_number=str(row.get("ord_orgno", "")).strip(),
                exchange=_parse_fill_exchange(row.get("excg_id_dvsn_cd"), warned_exchanges),
                reservation_end_date=_parse_date(row.get("rsvn_ord_end_dt")),
                loan_date=_parse_date(row.get("loan_dt")),
                stop=_parse_stop_trigger(row),
                _raw=row,
            )
        )
    return orders


def fetch_account_rights(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str,
) -> list[AccountRight]:
    """기간별 계좌 권리현황(유상·무상 배정·배당·상환 등). ``start``/``end`` 는 기간(YYYYMMDD).
    응답 배열 키는 KIS 예시 기준 ``output``(레이아웃의 output1 과 다름). **모의투자 미지원**."""
    if environment == "paper":
        raise KISUsageError(
            "기간별계좌권리현황조회(period-rights)는 모의투자 미지원 -- 실전에서만."
        )
    rows = _fetch_paginated_rows(
        transport,
        path=_RIGHTS_PATH, tr_id=_RIGHTS_TR,
        base_params={
            "INQR_DVSN": "03", "CUST_RNCNO25": "", "HMID": "",
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "INQR_STRT_DT": start, "INQR_END_DT": end,
            "RGHT_TYPE_CD": "", "PDNO": "", "PRDT_TYPE_CD": "",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        },
        output_key="output",  # KIS 예시 키는 output(레이아웃 output1 과 불일치)
        max_pages=_MAX_BALANCE_PAGES,
        cap_message=(
            f"계좌권리현황 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다."
        ),
    )
    return [_parse_account_right(row) for row in rows if str(row.get("pdno", "")).strip()]


def _parse_account_right(row: Mapping[str, Any]) -> AccountRight:
    return AccountRight(
        account_number=str(row.get("acno10", "")).strip(),
        right_type_code=str(row.get("rght_type_cd", "")).strip(),
        base_date=_parse_date(row.get("bass_dt")),
        symbol=str(row.get("pdno", "")).strip(),
        short_symbol=str(row.get("shtn_pdno", "")).strip(),
        name=str(row.get("prdt_name", "")).strip(),
        quantity=field_decimal_or_zero(row.get("cblc_qty"), "cblc_qty"),
        allocated_quantity=field_decimal_or_zero(row.get("last_alct_qty"), "last_alct_qty"),
        excess_allocated_quantity=field_decimal_or_zero(row.get("excs_alct_qty"), "excs_alct_qty"),
        total_allocated_quantity=field_decimal_or_zero(row.get("tot_alct_qty"), "tot_alct_qty"),
        allocated_amount=field_decimal_or_zero(row.get("last_alct_amt"), "last_alct_amt"),
        subscription_price=field_decimal_or_zero(row.get("sbsc_unpr"), "sbsc_unpr"),
        requested_quantity=field_decimal_or_zero(row.get("rqst_qty"), "rqst_qty"),
        requested_amount=field_decimal_or_zero(row.get("rqst_amt"), "rqst_amt"),
        request_date=_parse_date(row.get("rqst_dt")),
        subscription_end_date=_parse_date(row.get("sbsc_end_dt")),
        listing_date=_parse_date(row.get("lstg_dt")),
        cash_payment_date=_parse_date(row.get("cash_dfrm_dt")),
        refund_date=_parse_date(row.get("rfnd_dt")),
        refund_amount=field_decimal_or_zero(row.get("rfnd_amt"), "rfnd_amt"),
        tax_amount=field_decimal_or_zero(row.get("tax_amt"), "tax_amt"),
        _raw=row,
    )


# --- 실현손익 잔고 (TTTC8494R) --------------------------------------------
def fetch_realized_profit_balance(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> RealizedProfitBalance:
    """실현손익 포함 국내 체결기준잔고. 보유 종목(output1)과 계좌 요약(output2, 실현손익 포함)을
    :class:`RealizedProfitBalance` 로. **모의투자 미지원**.

    .. note:: output2 요약 필드는 KIS 응답예시로 확증되지 않았다(레이아웃 기준) -- 실제 응답과 다를
       수 있어 전체 원본을 결과의 ``_raw`` 로 함께 노출한다.
    """
    if environment == "paper":
        raise KISUsageError(
            "주식잔고조회_실현손익(inquire-balance-rlz-pl)은 모의투자 미지원 -- 실전에서만."
        )
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] = {}
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_REALIZED_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "00", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
            "COST_ICLD_YN": "", "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_REALIZED_BALANCE_PATH, tr_id=_REALIZED_BALANCE_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        page = resp.body.get("output1")
        if not isinstance(page, (list, tuple)):
            raise KISError(
                "실현손익 잔고 응답의 output1 이 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        summary = _first_summary(resp.body.get("output2")) or summary
        if resp.tr_cont not in ("F", "M"):
            break
        nxt = _advance_cursor(resp.body, ctx_width=100, prev_nk=ctx_nk)
        if nxt is None:  # 비진전 커서(빈 키/반복/종료 센티널) -> 재요청 중단(이중집계 방지)
            break
        ctx_fk, ctx_nk = nxt
        tr_cont = "N"
    else:
        raise KISError("실현손익 잔고가 페이지 상한에 도달했으나 연속조회가 남아있다.")
    if not summary:
        # 요약(output2)이 한 페이지에서도 안 잡히면 전 필드 0 인 RealizedProfitBalance 가 성공으로
        # 반환된다(0 = "실현손익 없음" 으로 읽히는 실값). 형제 파서들처럼 fail-closed 한다.
        raise KISError(
            "실현손익 잔고 응답에 계좌 요약(output2)이 없다 -- 전 필드 0 으로 조용히 반환하지 않는다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    positions = tuple(
        RealizedProfitPosition(
            symbol=str(row.get("pdno", "")).strip(),
            name=str(row.get("prdt_name", "")).strip(),
            trade_type_name=str(row.get("trad_dvsn_name", "")).strip(),
            holding_quantity=field_decimal_or_zero(row.get("hldg_qty"), "hldg_qty"),
            orderable_quantity=field_decimal_or_zero(row.get("ord_psbl_qty"), "ord_psbl_qty"),
            average_purchase_price=field_decimal_or_zero(row.get("pchs_avg_pric"), "pchs_avg_pric"),
            purchase_amount=field_decimal_or_zero(row.get("pchs_amt"), "pchs_amt"),
            current_price=field_decimal_or_zero(row.get("prpr"), "prpr"),
            market_value=field_decimal_or_zero(row.get("evlu_amt"), "evlu_amt"),
            unrealized_pnl=field_decimal_or_zero(row.get("evlu_pfls_amt"), "evlu_pfls_amt"),
            unrealized_pnl_percent=field_decimal_or_zero(row.get("evlu_pfls_rt"), "evlu_pfls_rt"),
            loan_date=_parse_date(row.get("loan_dt")),
            loan_amount=field_decimal_or_zero(row.get("loan_amt"), "loan_amt"),
            expiry_date=_parse_date(row.get("expd_dt")),
            _raw=row,
        )
        for row in rows if str(row.get("pdno", "")).strip()
    )
    return RealizedProfitBalance(
        positions=positions,
        total_deposit=field_decimal_or_zero(summary.get("dnca_tot_amt"), "dnca_tot_amt"),
        net_asset=field_decimal_or_zero(summary.get("nass_amt"), "nass_amt"),
        total_evaluation=field_decimal_or_zero(summary.get("tot_evlu_amt"), "tot_evlu_amt"),
        total_purchase_amount=field_decimal_or_zero(summary.get("pchs_amt_smtl_amt"), "pchs_amt_smtl_amt"),
        total_evaluation_amount=field_decimal_or_zero(summary.get("evlu_amt_smtl_amt"), "evlu_amt_smtl_amt"),
        total_unrealized_pnl=field_decimal_or_zero(summary.get("evlu_pfls_smtl_amt"), "evlu_pfls_smtl_amt"),
        asset_change=field_decimal_or_zero(summary.get("asst_icdc_amt"), "asst_icdc_amt"),
        asset_change_percent=field_decimal_or_zero(summary.get("asst_icdc_erng_rt"), "asst_icdc_erng_rt"),
        realized_pnl=field_decimal_or_zero(summary.get("rlzt_pfls"), "rlzt_pfls"),
        realized_return_percent=field_decimal_or_zero(summary.get("rlzt_erng_rt"), "rlzt_erng_rt"),
        real_evaluation_pnl=field_decimal_or_zero(summary.get("real_evlu_pfls"), "real_evlu_pfls"),
        real_evaluation_return_percent=field_decimal_or_zero(
            summary.get("real_evlu_pfls_erng_rt"), "real_evlu_pfls_erng_rt"
        ),
        _raw=summary,
    )


# --- 통합증거금 현황 (TTTC0869R) ------------------------------------------
def fetch_integrated_margin(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    include_cma: bool = False, won_basis: bool = True,
) -> IntegratedMargin:
    """주식통합증거금 현황(원화+외화 통합 주문가능금액). headline 만 타입화하고 전체(레이아웃
    100여 필드)는 ``_raw``. **모의투자 미지원**.

    ``include_cma`` CMA평가금액 포함 여부, ``won_basis`` 원화(True)/외화(False) 기준 표시.

    .. note:: 필드가 방대하고 KIS 예시에만 있는 (레이아웃 미기재) 홍콩위안화 재사용 필드가 있어,
       핵심 외 필드는 ``_raw`` 로만 노출한다.
    """
    if environment == "paper":
        raise KISUsageError(
            "주식통합증거금 현황(intgr-margin)은 모의투자 미지원 -- 실전에서만."
        )
    basis = "02" if won_basis else "01"
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "CMA_EVLU_AMT_ICLD_YN": "Y" if include_cma else "N",
        "WCRC_FRCR_DVSN_CD": basis, "FWEX_CTRT_FRCR_DVSN_CD": basis,
    }
    resp = transport.request(
        method="GET", path=_INTEGRATED_MARGIN_PATH, tr_id=_INTEGRATED_MARGIN_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise KISError(
            "통합증거금 현황 응답의 output 이 객체가 아니다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return IntegratedMargin(
        account_margin_rate=field_decimal_or_zero(output.get("acmga_rt"), "acmga_rt"),
        cash_orderable=field_decimal_or_zero(output.get("stck_cash_ord_psbl_amt"), "stck_cash_ord_psbl_amt"),
        substitute_orderable=field_decimal_or_zero(output.get("stck_sbst_ord_psbl_amt"), "stck_sbst_ord_psbl_amt"),
        receivable=field_decimal_or_zero(output.get("rcvb_amt"), "rcvb_amt"),
        limit_amount=field_decimal_or_zero(output.get("lmt_amt"), "lmt_amt"),
        integrated_margin_type_name=str(output.get("ovrs_stck_itgr_mgna_dvsn_name", "")).strip(),
        usd_orderable=field_decimal_or_zero(output.get("usd_itgr_ord_psbl_amt"), "usd_itgr_ord_psbl_amt"),
        hkd_orderable=field_decimal_or_zero(output.get("hkd_itgr_ord_psbl_amt"), "hkd_itgr_ord_psbl_amt"),
        jpy_orderable=field_decimal_or_zero(output.get("jpy_itgr_ord_psbl_amt"), "jpy_itgr_ord_psbl_amt"),
        cny_orderable=field_decimal_or_zero(output.get("cny_itgr_ord_psbl_amt"), "cny_itgr_ord_psbl_amt"),
        usd_exchange_rate=field_decimal_or_zero(output.get("usd_frst_bltn_exrt"), "usd_frst_bltn_exrt"),
        hkd_exchange_rate=field_decimal_or_zero(output.get("hkd_frst_bltn_exrt"), "hkd_frst_bltn_exrt"),
        jpy_exchange_rate=field_decimal_or_zero(output.get("jpy_frst_bltn_exrt"), "jpy_frst_bltn_exrt"),
        cny_exchange_rate=field_decimal_or_zero(output.get("cny_frst_bltn_exrt"), "cny_frst_bltn_exrt"),
        _raw=output,
    )


def _first_summary(block: object) -> Mapping[str, Any] | None:
    """output2 가 객체배열이면 첫 원소를, 객체면 그대로. 비면 None."""
    if isinstance(block, Mapping):
        return block
    if isinstance(block, (list, tuple)) and block and isinstance(block[0], Mapping):
        return block[0]
    return None


# --- 투자계좌 자산현황 -----------------------------------------------------
def fetch_account_assets(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> AccountAssets:
    """투자계좌 자산현황 요약(output2). 자산군별 내역(output1)은 위치기반이라 ``_raw`` 로만 둔다.
    **모의투자 미지원**."""
    if environment == "paper":
        raise KISUsageError(
            "투자계좌자산현황조회(inquire-account-balance)는 모의투자 미지원 -- 실전에서만."
        )
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "INQR_DVSN_1": "", "BSPR_BF_DT_APLY_YN": "",
    }
    resp = transport.request(
        method="GET", path=_ASSETS_PATH, tr_id=_ASSETS_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    summary = resp.body.get("output2")
    if not isinstance(summary, Mapping):
        raise KISError(
            "투자계좌자산현황 응답에 요약(output2)이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return AccountAssets(
        total_asset_amount=required_decimal(summary.get("tot_asst_amt"), "tot_asst_amt"),
        total_net_asset_amount=required_decimal(summary.get("nass_tot_amt"), "nass_tot_amt"),
        total_purchase_amount=required_decimal(summary.get("pchs_amt_smtl"), "pchs_amt_smtl"),
        total_evaluation_amount=required_decimal(summary.get("evlu_amt_smtl"), "evlu_amt_smtl"),
        total_unrealized_pnl=required_decimal(summary.get("evlu_pfls_amt_smtl"), "evlu_pfls_amt_smtl"),
        total_loan_amount=required_decimal(summary.get("loan_amt_smtl"), "loan_amt_smtl"),
        total_deposit=required_decimal(summary.get("tot_dncl_amt"), "tot_dncl_amt"),
        deposit=required_decimal(summary.get("dncl_amt"), "dncl_amt"),
        total_foreign_evaluation=required_decimal(summary.get("frcr_evlu_tota"), "frcr_evlu_tota"),
        overseas_stock_evaluation=required_decimal(summary.get("ovrs_stck_evlu_amt1"), "ovrs_stck_evlu_amt1"),
        total_substitute_amount=required_decimal(summary.get("tot_sbst_amt"), "tot_sbst_amt"),
        today_receivable=required_decimal(summary.get("thdt_rcvb_amt"), "thdt_rcvb_amt"),
        _raw=resp.body,
    )


# --- 미체결(정정·취소 가능) 주문 -------------------------------------------
def fetch_open_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> list[DomesticOpenOrder]:
    """미체결(정정·취소 가능) 주문 전체(연속조회 소진까지). **모의투자 미지원**.

    브로커 측 뷰라 우리 ``client_order_id`` 는 없다 -- 정정/취소는 KIS ``order_id``/``branch_number``
    로 지목한다. 응답은 ``output`` 배열, 페이지당 최대 50건.
    """
    if environment == "paper":
        raise KISUsageError(
            "정정취소가능주문조회(inquire-psbl-rvsecncl)는 모의투자 미지원 -- 실전에서만."
        )
    rows = _fetch_paginated_rows(
        transport,
        path=_OPEN_ORDERS_PATH, tr_id=_OPEN_ORDERS_TR,
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            "INQR_DVSN_1": "0",  # 0 주문 단위
            "INQR_DVSN_2": "0",  # 0 전체(매도+매수)
        },
        output_key="output",
        max_pages=_MAX_OPEN_ORDER_PAGES,
        cap_message=(
            f"정정취소가능주문조회가 {_MAX_OPEN_ORDER_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다."
        ),
    )
    return _parse_open_orders(rows)


def _parse_open_orders(rows: list[Mapping[str, Any]]) -> list[DomesticOpenOrder]:
    orders: list[DomesticOpenOrder] = []
    for row in rows:
        order_id = str(row.get("odno", "")).strip()
        if not order_id:  # 주문번호 없는 패딩 행 -- 건너뜀
            continue
        quantity = field_decimal_or_zero(row.get("ord_qty"), "ord_qty")
        filled = field_decimal_or_zero(row.get("tot_ccld_qty"), "tot_ccld_qty")
        orders.append(
            DomesticOpenOrder(
                symbol=str(row.get("pdno", "")).strip(),
                name=str(row.get("prdt_name", "")).strip(),
                order_id=order_id,
                original_order_id=str(row.get("orgn_odno", "")).strip(),
                branch_number=str(row.get("ord_gno_brno", "")).strip(),
                side=_side_from_code(row.get("sll_buy_dvsn_cd")),
                order_type_name=str(row.get("ord_dvsn_name", "")).strip(),
                order_quantity=quantity,
                filled_quantity=filled,
                unfilled_quantity=quantity - filled,
                cancelable_quantity=field_decimal_or_zero(row.get("psbl_qty"), "psbl_qty"),
                order_price=field_decimal_or_zero(row.get("ord_unpr"), "ord_unpr"),
                order_time=_parse_hhmmss(row.get("ord_tmd")),
                _raw=row,
            )
        )
    return orders




# --- 공용 ------------------------------------------------------------------
def _format_order_unit_price(limit_price: Numeric | None) -> str:
    """주문 단가를 KIS 와이어 정본으로 -- ``None`` 이면 빈 문자열. 유한 양수 아니면 거부."""
    if limit_price is None:
        return ""
    try:
        price = Decimal(str(limit_price))
    except (ArithmeticError, ValueError) as err:
        raise KISUsageError(f"limit_price 는 숫자여야 한다: {limit_price!r}") from err
    if not price.is_finite() or price <= 0:
        raise KISUsageError(f"limit_price 는 0보다 큰 유한값이어야 한다: {limit_price!r}")
    return format_wire_decimal(price)
