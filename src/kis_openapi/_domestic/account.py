"""국내주식 계좌 조회 (내부) -- 잔고/보유종목/매수가능/매도가능.

사용자면(KISClient/Ticker)이 이 함수들을 호출해 통합 반환 타입을 받는다. 계좌 식별정보
(``cano``/``product_code``)와 환경(``environment``)은 세션에서 온다. KIS 원본 필드 매핑과
fail-closed 파싱은 여기 갇힌다.

KIS URL/TR-id:
- 잔고: ``GET .../trading/inquire-balance`` (실전 ``TTTC8434R`` / 모의 ``VTTC8434R``).
- 매수가능: ``GET .../trading/inquire-psbl-order`` (실전 ``TTTC8908R`` / 모의 ``VTTC8908R``).
- 매도가능수량: ``GET .../trading/inquire-psbl-sell`` (``TTTC8408R``, **모의 미지원**).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, time
from decimal import Decimal
from typing import Any

from .._wire import format_wire_decimal, optional_decimal, required_decimal
from ..account_reports import (
    IntegratedMargin,
    RealizedProfitBalance,
    RealizedProfitPosition,
)
from ..account_right import AccountRight
from ..balance import AccountAssets, Balance, Portfolio, Position
from ..errors import KISError, KISUsageError
from ..open_order import OpenOrder
from ..orderable import BuyableAmount, SellableQuantity
from ..trade_profit import (
    DailyProfit,
    DailyProfitHistory,
    TradeProfit,
    TradeProfitHistory,
)
from ..transport import Environment, RawResponse, Transport

_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
_BALANCE_TR = {"real": "TTTC8434R", "demo": "VTTC8434R"}
#: 잔고 종목배열 연속조회 페이지 상한. 여기 닿으면 부분 결과로 자르지 않고 fail-closed.
_MAX_BALANCE_PAGES = 100

_BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
_BUYABLE_TR = {"real": "TTTC8908R", "demo": "VTTC8908R"}
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
#: 신용유형(원장 코드표). 21 자기융자신규/22 유통대주신규/23 유통융자신규/24 자기대주신규/
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
#: 미체결 주문 연속조회 페이지 상한(한 콜 최대 50건). 닿으면 fail-closed.
_MAX_OPEN_ORDER_PAGES = 100
_SIDE = {"01": "sell", "02": "buy"}


# --- 잔고 / 보유종목 / 포트폴리오 -----------------------------------------
def fetch_balance(transport: Transport, *, cano: str, product_code: str, environment: Environment) -> Balance:
    """계좌 현금·자산 요약(1콜, output2). 요약은 계좌 단위라 첫 페이지로 완결."""
    resp = _fetch_balance_page(transport, cano, product_code, environment, "", "")
    _raise_if_error(resp)
    summary = _extract_summary(resp.body)
    if summary is None:
        raise KISError(
            "잔고 응답에 계좌 요약(output2)이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return _parse_balance(summary)


def fetch_positions(transport: Transport, *, cano: str, product_code: str, environment: Environment) -> list[Position]:
    """보유 종목 전체(연속조회 소진까지). 0수량 잔여 lot 포함."""
    rows, _summary = _walk_holdings(transport, cano, product_code, environment)
    return _parse_positions(rows)


def fetch_portfolio(transport: Transport, *, cano: str, product_code: str, environment: Environment) -> Portfolio:
    """현금·자산 요약과 보유 종목을 한 번의 조회 순회로 함께."""
    rows, summary = _walk_holdings(transport, cano, product_code, environment)
    if summary is None:
        raise KISError("잔고 응답에 계좌 요약(output2)이 없다.")
    return Portfolio(balance=_parse_balance(summary), positions=tuple(_parse_positions(rows)))


def _walk_holdings(
    transport: Transport, cano: str, product_code: str, environment: Environment
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any] | None]:
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_BALANCE_PAGES):
        resp = _fetch_balance_page(
            transport, cano, product_code, environment, ctx_fk, ctx_nk, tr_cont=tr_cont
        )
        _raise_if_error(resp)
        if summary is None:  # 계좌 요약은 첫 페이지에서(계좌 단위라 페이지 불변)
            summary = _extract_summary(resp.body)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 계좌도 output1 을 빈 배열로 준다 -> 부재/비배열은 손상
            raise KISError(
                "잔고 응답의 output1 이 종목 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        if resp.tr_cont not in ("F", "M"):
            break
        ctx_nk = str(resp.body.get("ctx_area_nk100") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk100") or "").strip()
        tr_cont = "N"
    else:
        raise KISError(
            f"잔고 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    return rows, summary


def _fetch_balance_page(
    transport: Transport, cano: str, product_code: str, environment: Environment, ctx_fk: str, ctx_nk: str,
    *, tr_cont: str = "",
) -> RawResponse:
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


def _parse_positions(rows: list[Mapping[str, Any]]) -> list[Position]:
    positions: list[Position] = []
    for row in rows:
        symbol = str(row.get("pdno", "")).strip()
        if not symbol:  # 종목코드 없는 패딩 행 -- 건너뜀
            continue
        positions.append(
            # 종목별 수치는 빈 값을 0으로 읽는다 -- 정산 완료된 0수량 잔여 lot 등에서 일부 필드가
            # 빌 수 있는데, 그 한 종목 때문에 보유목록 전체 조회가 깨지면 안 된다(값이 있는데
            # 파싱 실패면 여전히 예외). 종목 식별은 위의 빈 pdno 가드가 이미 처리.
            Position(
                symbol=symbol,
                security_name=str(row.get("prdt_name", "")).strip(),
                currency="KRW",
                quantity=_decimal_or_zero(row.get("hldg_qty"), "hldg_qty"),
                sellable_quantity=_decimal_or_zero(row.get("ord_psbl_qty"), "ord_psbl_qty"),
                average_purchase_price=_decimal_or_zero(row.get("pchs_avg_pric"), "pchs_avg_pric"),
                purchase_amount=_decimal_or_zero(row.get("pchs_amt"), "pchs_amt"),
                current_price=_decimal_or_zero(row.get("prpr"), "prpr"),
                market_value=_decimal_or_zero(row.get("evlu_amt"), "evlu_amt"),
                unrealized_pnl=_decimal_or_zero(row.get("evlu_pfls_amt"), "evlu_pfls_amt"),
                unrealized_pnl_percent=_decimal_or_zero(row.get("evlu_pfls_rt"), "evlu_pfls_rt"),
                _raw=row,
            )
        )
    return positions


def _parse_balance(summary: Mapping[str, Any]) -> Balance:
    return Balance(
        currency="KRW",
        deposit=required_decimal(summary.get("dnca_tot_amt"), "dnca_tot_amt"),
        settlement_cash_d1=required_decimal(summary.get("nxdy_excc_amt"), "nxdy_excc_amt"),
        settlement_cash_d2=required_decimal(summary.get("prvs_rcdl_excc_amt"), "prvs_rcdl_excc_amt"),
        total_evaluation=required_decimal(summary.get("tot_evlu_amt"), "tot_evlu_amt"),
        net_asset=required_decimal(summary.get("nass_amt"), "nass_amt"),
        purchase_amount=required_decimal(summary.get("pchs_amt_smtl_amt"), "pchs_amt_smtl_amt"),
        market_value=required_decimal(summary.get("evlu_amt_smtl_amt"), "evlu_amt_smtl_amt"),
        unrealized_pnl=required_decimal(summary.get("evlu_pfls_smtl_amt"), "evlu_pfls_smtl_amt"),
        _raw=summary,
    )


def _extract_summary(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """잔고 요약(output2) -- KIS가 '길이 1 배열'로도, 단일 객체로도 준다. 비매핑이면 None."""
    summary = body.get("output2")
    if isinstance(summary, list):
        first = summary[0] if summary else None
        return first if isinstance(first, Mapping) else None
    if isinstance(summary, Mapping):
        return summary
    return None


# --- 매수가능 / 매도가능 ---------------------------------------------------
def fetch_buyable(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    symbol: str | None = None, limit_price: object | None = None,
) -> BuyableAmount:
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
    if environment == "demo":
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
    if not isinstance(output1, Mapping):
        raise KISError(
            "매도가능수량조회 응답에 output1 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return _parse_sellable(output1, symbol=symbol)


def fetch_credit_buyable(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    symbol: str, credit_type: str, limit_price: object | None = None,
) -> BuyableAmount:
    """신용(융자/대주) 매수가능 여력. ``credit_type`` 은 신용유형(21 자기융자신규 등),
    ``limit_price`` 없으면 시장가 기준. **모의투자 미지원**.

    현금 매수가능(:func:`fetch_buyable`)과 output 형상이 같아 :class:`BuyableAmount` 를 공유한다
    -- 신용 전용 필드(주문가능대용·펀드환매대금·CMA평가금액 등)는 ``_raw`` 로 접근한다.
    """
    if environment == "demo":
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
        "ORD_UNPR": unit_price or "0",  # 시장가면 공란 대신 "0"(원장 권고)
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


def _parse_buyable(output: Mapping[str, Any], *, symbol: str) -> BuyableAmount:
    return BuyableAmount(
        symbol=symbol,
        currency="KRW",
        orderable_cash=required_decimal(output.get("ord_psbl_cash"), "ord_psbl_cash"),
        reusable_cash=required_decimal(output.get("ruse_psbl_amt"), "ruse_psbl_amt"),
        cash_buyable_amount=_decimal_or_zero(output.get("nrcvb_buy_amt"), "nrcvb_buy_amt"),
        cash_buyable_quantity=_decimal_or_zero(output.get("nrcvb_buy_qty"), "nrcvb_buy_qty"),
        max_buyable_amount=_decimal_or_zero(output.get("max_buy_amt"), "max_buy_amt"),
        max_buyable_quantity=_decimal_or_zero(output.get("max_buy_qty"), "max_buy_qty"),
        _raw=output,
    )


def _parse_sellable(output1: Mapping[str, Any], *, symbol: str) -> SellableQuantity:
    return SellableQuantity(
        symbol=symbol,
        security_name=str(output1.get("prdt_name", "")).strip(),
        quantity=_decimal_or_zero(output1.get("cblc_qty"), "cblc_qty"),
        sellable_quantity=_decimal_or_zero(output1.get("ord_psbl_qty"), "ord_psbl_qty"),
        _raw=output1,
    )


# --- 기간별 매매손익 -------------------------------------------------------
def fetch_trade_profits(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str, symbol: str | None = None, sort: str = "recent",
) -> TradeProfitHistory:
    """기간별 매매손익(실현손익). ``start``/``end`` 는 기간(YYYYMMDD), ``symbol`` 없으면 전체,
    ``sort`` = recent/oldest. output1 종목행을 연속조회로 모으고 output2 총계를 함께 담는다.
    **모의투자 미지원**."""
    if environment == "demo":
        raise KISUsageError(
            "기간별매매손익현황조회(inquire-period-trade-profit)는 모의투자 미지원 -- 실전에서만."
        )
    try:
        sort_code = _TRADE_PROFIT_SORT[sort]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 sort: {sort!r} ({'/'.join(_TRADE_PROFIT_SORT)})."
        ) from None
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_TRADE_PROFIT_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "SORT_DVSN": sort_code, "PDNO": symbol or "",
            "INQR_STRT_DT": start, "INQR_END_DT": end, "CBLC_DVSN": "00",
            "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_TRADE_PROFIT_PATH, tr_id=_TRADE_PROFIT_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        if summary is None:  # 기간 총계(output2)는 기간 단위라 첫 페이지로 완결
            candidate = resp.body.get("output2")
            summary = candidate if isinstance(candidate, Mapping) else None
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 내역도 배열 -> 부재/비배열은 손상
            raise KISError(
                "매매손익 응답의 output1 이 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        if resp.tr_cont not in ("F", "M"):
            break
        ctx_nk = str(resp.body.get("ctx_area_nk100") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk100") or "").strip()
        tr_cont = "N"
    else:
        raise KISError(
            f"매매손익 조회가 {_MAX_TRADE_PROFIT_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다."
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
        trade_type=str(row.get("trad_dvsn_name", "")).strip(),
        holding_quantity=_decimal_or_zero(row.get("hldg_qty"), "hldg_qty"),
        purchase_price=_decimal_or_zero(row.get("pchs_unpr"), "pchs_unpr"),
        buy_quantity=_decimal_or_zero(row.get("buy_qty"), "buy_qty"),
        buy_amount=_decimal_or_zero(row.get("buy_amt"), "buy_amt"),
        sell_price=_decimal_or_zero(row.get("sll_pric"), "sll_pric"),
        sell_quantity=_decimal_or_zero(row.get("sll_qty"), "sll_qty"),
        sell_amount=_decimal_or_zero(row.get("sll_amt"), "sll_amt"),
        realized_pnl=_decimal_or_zero(row.get("rlzt_pfls"), "rlzt_pfls"),
        return_percent=_decimal_or_zero(row.get("pfls_rt"), "pfls_rt"),
        fee=_decimal_or_zero(row.get("fee"), "fee"),
        tax=_decimal_or_zero(row.get("tl_tax"), "tl_tax"),
        loan_interest=_decimal_or_zero(row.get("loan_int"), "loan_int"),
        _raw=row,
    )


def fetch_daily_profits(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str, symbol: str | None = None, sort: str = "recent",
) -> DailyProfitHistory:
    """기간별 일별 매매손익 합산. 파라미터는 :func:`fetch_trade_profits` 와 같되 output1 이 하루
    단위(종목 구분 없음)다. **모의투자 미지원**."""
    if environment == "demo":
        raise KISUsageError(
            "기간별손익일별합산조회(inquire-period-profit)는 모의투자 미지원 -- 실전에서만."
        )
    try:
        sort_code = _TRADE_PROFIT_SORT[sort]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 sort: {sort!r} ({'/'.join(_TRADE_PROFIT_SORT)})."
        ) from None
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_TRADE_PROFIT_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "INQR_STRT_DT": start, "INQR_END_DT": end, "PDNO": symbol or "",
            "SORT_DVSN": sort_code, "INQR_DVSN": "00", "CBLC_DVSN": "00",
            "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_DAILY_PROFIT_PATH, tr_id=_DAILY_PROFIT_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        if summary is None:
            candidate = resp.body.get("output2")
            summary = candidate if isinstance(candidate, Mapping) else None
        page = resp.body.get("output1")
        if not isinstance(page, list):
            raise KISError(
                "일별손익 응답의 output1 이 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        if resp.tr_cont not in ("F", "M"):
            break
        ctx_nk = str(resp.body.get("ctx_area_nk100") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk100") or "").strip()
        tr_cont = "N"
    else:
        raise KISError(
            f"일별손익 조회가 {_MAX_TRADE_PROFIT_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다."
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
        buy_amount=_decimal_or_zero(row.get("buy_amt"), "buy_amt"),
        sell_amount=_decimal_or_zero(row.get("sll_amt"), "sll_amt"),
        realized_pnl=_decimal_or_zero(row.get("rlzt_pfls"), "rlzt_pfls"),
        return_percent=_decimal_or_zero(row.get("pfls_rt"), "pfls_rt"),
        fee=_decimal_or_zero(row.get("fee"), "fee"),
        tax=_decimal_or_zero(row.get("tl_tax"), "tl_tax"),
        loan_interest=_decimal_or_zero(row.get("loan_int"), "loan_int"),
        buy_quantity=_decimal_or_zero(row.get("buy_qty1"), "buy_qty1"),
        sell_quantity=_decimal_or_zero(row.get("sll_qty1"), "sll_qty1"),
        _raw=row,
    )


def fetch_account_rights(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str,
) -> list[AccountRight]:
    """기간별 계좌 권리현황(유상·무상 배정·배당·상환 등). ``start``/``end`` 는 기간(YYYYMMDD).
    응답 배열 키는 원장 예시 기준 ``output``(레이아웃의 output1 과 다름). **모의투자 미지원**."""
    if environment == "demo":
        raise KISUsageError(
            "기간별계좌권리현황조회(period-rights)는 모의투자 미지원 -- 실전에서만."
        )
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_BALANCE_PAGES):
        params = {
            "INQR_DVSN": "03", "CUST_RNCNO25": "", "HMID": "",
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "INQR_STRT_DT": start, "INQR_END_DT": end,
            "RGHT_TYPE_CD": "", "PDNO": "", "PRDT_TYPE_CD": "",
            "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_RIGHTS_PATH, tr_id=_RIGHTS_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        page = resp.body.get("output")  # 원장 예시 키는 output(레이아웃 output1 과 불일치)
        if not isinstance(page, list):  # 빈 내역도 배열 -> 부재/비배열은 손상
            raise KISError(
                "계좌권리현황 응답의 output 이 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        if resp.tr_cont not in ("F", "M"):
            break
        ctx_nk = str(resp.body.get("ctx_area_nk100") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk100") or "").strip()
        tr_cont = "N"
    else:
        raise KISError(
            f"계좌권리현황 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다."
        )
    return [_parse_account_right(row) for row in rows if str(row.get("pdno", "")).strip()]


def _parse_account_right(row: Mapping[str, Any]) -> AccountRight:
    return AccountRight(
        account_number=str(row.get("acno10", "")).strip(),
        right_type_code=str(row.get("rght_type_cd", "")).strip(),
        record_date=_parse_date(row.get("bass_dt")),
        symbol=str(row.get("pdno", "")).strip(),
        short_symbol=str(row.get("shtn_pdno", "")).strip(),
        name=str(row.get("prdt_name", "")).strip(),
        balance_quantity=_decimal_or_zero(row.get("cblc_qty"), "cblc_qty"),
        allocated_quantity=_decimal_or_zero(row.get("last_alct_qty"), "last_alct_qty"),
        excess_allocated_quantity=_decimal_or_zero(row.get("excs_alct_qty"), "excs_alct_qty"),
        total_allocated_quantity=_decimal_or_zero(row.get("tot_alct_qty"), "tot_alct_qty"),
        allocated_amount=_decimal_or_zero(row.get("last_alct_amt"), "last_alct_amt"),
        subscription_price=_decimal_or_zero(row.get("sbsc_unpr"), "sbsc_unpr"),
        requested_quantity=_decimal_or_zero(row.get("rqst_qty"), "rqst_qty"),
        requested_amount=_decimal_or_zero(row.get("rqst_amt"), "rqst_amt"),
        request_date=_parse_date(row.get("rqst_dt")),
        subscription_end_date=_parse_date(row.get("sbsc_end_dt")),
        listing_date=_parse_date(row.get("lstg_dt")),
        cash_payment_date=_parse_date(row.get("cash_dfrm_dt")),
        refund_date=_parse_date(row.get("rfnd_dt")),
        refund_amount=_decimal_or_zero(row.get("rfnd_amt"), "rfnd_amt"),
        tax_amount=_decimal_or_zero(row.get("tax_amt"), "tax_amt"),
        _raw=row,
    )


# --- 실현손익 잔고 (TTTC8494R) --------------------------------------------
def fetch_realized_profit_balance(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> RealizedProfitBalance:
    """실현손익 포함 국내 체결기준잔고. 보유 종목(output1)과 계좌 요약(output2, 실현손익 포함)을
    :class:`RealizedProfitBalance` 로. **모의투자 미지원**.

    .. note:: output2 요약 필드는 원장 응답예시로 확증되지 않았다(레이아웃 기준) -- 실제 응답과 다를
       수 있어 전체 원본을 결과의 ``_raw`` 로 함께 노출한다.
    """
    if environment == "demo":
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
        if not isinstance(page, list):
            raise KISError(
                "실현손익 잔고 응답의 output1 이 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        summary = _first_summary(resp.body.get("output2")) or summary
        if resp.tr_cont not in ("F", "M"):
            break
        ctx_nk = str(resp.body.get("ctx_area_nk100") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk100") or "").strip()
        tr_cont = "N"
    else:
        raise KISError("실현손익 잔고가 페이지 상한에 도달했으나 연속조회가 남아있다.")
    positions = tuple(
        RealizedProfitPosition(
            symbol=str(row.get("pdno", "")).strip(),
            name=str(row.get("prdt_name", "")).strip(),
            trade_type=str(row.get("trad_dvsn_name", "")).strip(),
            holding_quantity=_decimal_or_zero(row.get("hldg_qty"), "hldg_qty"),
            orderable_quantity=_decimal_or_zero(row.get("ord_psbl_qty"), "ord_psbl_qty"),
            average_purchase_price=_decimal_or_zero(row.get("pchs_avg_pric"), "pchs_avg_pric"),
            purchase_amount=_decimal_or_zero(row.get("pchs_amt"), "pchs_amt"),
            current_price=_decimal_or_zero(row.get("prpr"), "prpr"),
            market_value=_decimal_or_zero(row.get("evlu_amt"), "evlu_amt"),
            unrealized_pnl=_decimal_or_zero(row.get("evlu_pfls_amt"), "evlu_pfls_amt"),
            unrealized_pnl_rate=_decimal_or_zero(row.get("evlu_pfls_rt"), "evlu_pfls_rt"),
            loan_date=_parse_date(row.get("loan_dt")),
            loan_amount=_decimal_or_zero(row.get("loan_amt"), "loan_amt"),
            expiry_date=_parse_date(row.get("expd_dt")),
            _raw=row,
        )
        for row in rows if str(row.get("pdno", "")).strip()
    )
    return RealizedProfitBalance(
        positions=positions,
        deposit_total=_decimal_or_zero(summary.get("dnca_tot_amt"), "dnca_tot_amt"),
        net_asset=_decimal_or_zero(summary.get("nass_amt"), "nass_amt"),
        total_value=_decimal_or_zero(summary.get("tot_evlu_amt"), "tot_evlu_amt"),
        purchase_total=_decimal_or_zero(summary.get("pchs_amt_smtl_amt"), "pchs_amt_smtl_amt"),
        evaluation_total=_decimal_or_zero(summary.get("evlu_amt_smtl_amt"), "evlu_amt_smtl_amt"),
        evaluation_pnl_total=_decimal_or_zero(summary.get("evlu_pfls_smtl_amt"), "evlu_pfls_smtl_amt"),
        asset_change=_decimal_or_zero(summary.get("asst_icdc_amt"), "asst_icdc_amt"),
        asset_change_rate=_decimal_or_zero(summary.get("asst_icdc_erng_rt"), "asst_icdc_erng_rt"),
        realized_pnl=_decimal_or_zero(summary.get("rlzt_pfls"), "rlzt_pfls"),
        realized_return_rate=_decimal_or_zero(summary.get("rlzt_erng_rt"), "rlzt_erng_rt"),
        real_eval_pnl=_decimal_or_zero(summary.get("real_evlu_pfls"), "real_evlu_pfls"),
        real_eval_return_rate=_decimal_or_zero(
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

    .. note:: 필드가 방대하고 원장 예시에만 있는 (레이아웃 미기재) 홍콩위안화 재사용 필드가 있어,
       핵심 외 필드는 ``_raw`` 로만 노출한다.
    """
    if environment == "demo":
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
        account_margin_rate=_decimal_or_zero(output.get("acmga_rt"), "acmga_rt"),
        cash_orderable=_decimal_or_zero(output.get("stck_cash_ord_psbl_amt"), "stck_cash_ord_psbl_amt"),
        substitute_orderable=_decimal_or_zero(output.get("stck_sbst_ord_psbl_amt"), "stck_sbst_ord_psbl_amt"),
        receivable=_decimal_or_zero(output.get("rcvb_amt"), "rcvb_amt"),
        limit_amount=_decimal_or_zero(output.get("lmt_amt"), "lmt_amt"),
        integrated_margin_type=str(output.get("ovrs_stck_itgr_mgna_dvsn_name", "")).strip(),
        usd_orderable=_decimal_or_zero(output.get("usd_itgr_ord_psbl_amt"), "usd_itgr_ord_psbl_amt"),
        hkd_orderable=_decimal_or_zero(output.get("hkd_itgr_ord_psbl_amt"), "hkd_itgr_ord_psbl_amt"),
        jpy_orderable=_decimal_or_zero(output.get("jpy_itgr_ord_psbl_amt"), "jpy_itgr_ord_psbl_amt"),
        cny_orderable=_decimal_or_zero(output.get("cny_itgr_ord_psbl_amt"), "cny_itgr_ord_psbl_amt"),
        usd_exchange_rate=_decimal_or_zero(output.get("usd_frst_bltn_exrt"), "usd_frst_bltn_exrt"),
        hkd_exchange_rate=_decimal_or_zero(output.get("hkd_frst_bltn_exrt"), "hkd_frst_bltn_exrt"),
        jpy_exchange_rate=_decimal_or_zero(output.get("jpy_frst_bltn_exrt"), "jpy_frst_bltn_exrt"),
        cny_exchange_rate=_decimal_or_zero(output.get("cny_frst_bltn_exrt"), "cny_frst_bltn_exrt"),
        _raw=output,
    )


def _first_summary(block: object) -> Mapping[str, Any] | None:
    """output2 가 객체배열이면 첫 원소를, 객체면 그대로. 비면 None."""
    if isinstance(block, Mapping):
        return block
    if isinstance(block, list) and block and isinstance(block[0], Mapping):
        return block[0]
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


# --- 투자계좌 자산현황 -----------------------------------------------------
def fetch_account_assets(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> AccountAssets:
    """투자계좌 자산현황 요약(output2). 자산군별 내역(output1)은 위치기반이라 ``_raw`` 로만 둔다.
    **모의투자 미지원**."""
    if environment == "demo":
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
        net_asset_total=required_decimal(summary.get("nass_tot_amt"), "nass_tot_amt"),
        purchase_amount_total=required_decimal(summary.get("pchs_amt_smtl"), "pchs_amt_smtl"),
        evaluation_amount_total=required_decimal(summary.get("evlu_amt_smtl"), "evlu_amt_smtl"),
        evaluation_pnl_total=required_decimal(summary.get("evlu_pfls_amt_smtl"), "evlu_pfls_amt_smtl"),
        loan_amount_total=required_decimal(summary.get("loan_amt_smtl"), "loan_amt_smtl"),
        deposit_total=required_decimal(summary.get("tot_dncl_amt"), "tot_dncl_amt"),
        deposit=required_decimal(summary.get("dncl_amt"), "dncl_amt"),
        foreign_evaluation_total=required_decimal(summary.get("frcr_evlu_tota"), "frcr_evlu_tota"),
        overseas_stock_evaluation=required_decimal(summary.get("ovrs_stck_evlu_amt1"), "ovrs_stck_evlu_amt1"),
        substitute_amount_total=required_decimal(summary.get("tot_sbst_amt"), "tot_sbst_amt"),
        today_receivable=required_decimal(summary.get("thdt_rcvb_amt"), "thdt_rcvb_amt"),
        _raw=resp.body,
    )


# --- 미체결(정정·취소 가능) 주문 -------------------------------------------
def fetch_open_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> list[OpenOrder]:
    """미체결(정정·취소 가능) 주문 전체(연속조회 소진까지). **모의투자 미지원**.

    브로커 측 뷰라 우리 ``client_order_id`` 는 없다 -- 정정/취소는 KIS ``order_id``/``branch_number``
    로 지목한다. 응답은 ``output`` 배열, 페이지당 최대 50건.
    """
    if environment == "demo":
        raise KISUsageError(
            "정정취소가능주문조회(inquire-psbl-rvsecncl)는 모의투자 미지원 -- 실전에서만."
        )
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_OPEN_ORDER_PAGES):
        resp = _fetch_open_orders_page(
            transport, cano, product_code, environment, ctx_fk, ctx_nk, tr_cont=tr_cont
        )
        _raise_if_error(resp)
        page = resp.body.get("output")
        if not isinstance(page, list):  # 미체결 없어도 빈 배열 -> 부재/비배열은 손상
            raise KISError(
                "정정취소가능주문조회 응답의 output 이 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        if resp.tr_cont not in ("F", "M"):
            break
        ctx_fk = str(resp.body.get("ctx_area_fk100") or "").strip()
        ctx_nk = str(resp.body.get("ctx_area_nk100") or "").strip()
        tr_cont = "N"
    else:
        raise KISError(
            f"정정취소가능주문조회가 {_MAX_OPEN_ORDER_PAGES}페이지 상한에 도달했으나 연속조회가 "
            f"남아있다 -- 부분 결과로 자르지 않는다."
        )
    return _parse_open_orders(rows)


def _fetch_open_orders_page(
    transport: Transport, cano: str, product_code: str, environment: Environment, ctx_fk: str, ctx_nk: str,
    *, tr_cont: str = "",
) -> RawResponse:
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk,
        "INQR_DVSN_1": "0",  # 0 주문 단위
        "INQR_DVSN_2": "0",  # 0 전체(매도+매수)
    }
    return transport.request(
        method="GET", path=_OPEN_ORDERS_PATH, tr_id=_OPEN_ORDERS_TR,
        params=params, idempotent=True, tr_cont=tr_cont,
    )


def _parse_open_orders(rows: list[Mapping[str, Any]]) -> list[OpenOrder]:
    orders: list[OpenOrder] = []
    for row in rows:
        order_id = str(row.get("odno", "")).strip()
        if not order_id:  # 주문번호 없는 패딩 행 -- 건너뜀
            continue
        quantity = _decimal_or_zero(row.get("ord_qty"), "ord_qty")
        filled = _decimal_or_zero(row.get("tot_ccld_qty"), "tot_ccld_qty")
        orders.append(
            OpenOrder(
                symbol=str(row.get("pdno", "")).strip(),
                name=str(row.get("prdt_name", "")).strip(),
                order_id=order_id,
                original_order_id=str(row.get("orgn_odno", "")).strip(),
                branch_number=str(row.get("ord_gno_brno", "")).strip(),
                side=_SIDE.get(str(row.get("sll_buy_dvsn_cd", "")).strip(), ""),
                order_type=str(row.get("ord_dvsn_name", "")).strip(),
                quantity=quantity,
                filled_quantity=filled,
                unfilled_quantity=quantity - filled,
                cancelable_quantity=_decimal_or_zero(row.get("psbl_qty"), "psbl_qty"),
                price=_decimal_or_zero(row.get("ord_unpr"), "ord_unpr"),
                order_time=_parse_hhmmss(row.get("ord_tmd")),
                _raw=row,
            )
        )
    return orders


def _parse_hhmmss(value: object) -> time | None:
    """``"131438"`` -> ``time(13, 14, 38)``. 공백/형식오류면 None(fail-soft)."""
    text = str(value or "").strip()
    if len(text) != 6 or not text.isdigit():
        return None
    hour, minute, second = int(text[0:2]), int(text[2:4]), int(text[4:6])
    if hour > 23 or minute > 59 or second > 59:
        return None
    return time(hour, minute, second)


# --- 공용 ------------------------------------------------------------------
def _decimal_or_zero(value: object, field_name: str) -> Decimal:
    """없으면 0, 있으면 Decimal(파싱 실패면 예외). '없음=0'인 수량·금액 필드용.

    부재(None)만 0으로 본다 -- 값 "0"도 Decimal(0)이라 결과는 같지만, 판정을 truthiness 가
    아니라 명시적 None 검사로 해 의도를 분명히 한다.
    """
    amount = optional_decimal(value, field_name)
    return Decimal(0) if amount is None else amount


def _format_order_unit_price(limit_price: object | None) -> str:
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


def _raise_if_error(resp: RawResponse) -> None:
    if not resp.ok:
        raise KISError(
            f"KIS 조회 요청 실패: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
