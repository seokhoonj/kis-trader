"""해외주식 계좌 조회 (내부) -- 잔고(보유종목)를 :class:`OverseasPosition` 리스트로.

국내(:mod:`kis_openapi._domestic.account`)와 대칭. 해외 잔고는 **거래소 그룹(OVRS_EXCG_CD)+통화
(TR_CRCY_CD)별**로 조회하며 금액이 외화라 :class:`~kis_openapi.money.Money` 로 통화를 함께 담는다.

KIS URL/TR-id (원장 대조):
- 잔고: ``GET /uapi/overseas-stock/v1/trading/inquire-balance`` (실전 ``TTTS3012R`` / 모의 ``VTTS3012R``).
  ``OVRS_EXCG_CD`` 는 NASD(미국전체)/SEHK(홍콩)/SHAA(상해)/SZAA(심천)/TKSE(일본)/HASE(하노이)/VNSE(호치민),
  ``TR_CRCY_CD`` 는 USD/HKD/CNY/JPY/VND. (시세 조회의 EXCD 코드와 다르다.)
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .._domestic.market_data import _raise_if_error
from .._wire import (
    format_wire_decimal,
    optional_decimal,
    required_decimal,
    required_int,
)
from ..errors import KISError, KISUsageError
from ..money import Money
from ..overseas_items import (
    OverseasBalance,
    OverseasBuyableAmount,
    OverseasOpenOrder,
    OverseasPosition,
    OverseasTransaction,
)
from ..transport import Environment, Transport
from .orders import _ORDER_EXCHANGE

_POSITIONS_PATH = "/uapi/overseas-stock/v1/trading/inquire-balance"
_POSITIONS_TR = {"real": "TTTS3012R", "demo": "VTTS3012R"}
#: 잔고 종목배열 연속조회 페이지 상한. 여기 닿으면 부분 결과로 자르지 않고 fail-closed.
_MAX_PAGES = 100

_OPEN_ORDERS_PATH = "/uapi/overseas-stock/v1/trading/inquire-nccs"
_OPEN_ORDERS_TR = "TTTS3018R"           # 모의투자 미지원(실전만)

_BUYABLE_PATH = "/uapi/overseas-stock/v1/trading/inquire-psamount"
_BUYABLE_TR = {"real": "TTTS3007R", "demo": "VTTS3007R"}

_TRANSACTIONS_PATH = "/uapi/overseas-stock/v1/trading/inquire-period-trans"
_TRANSACTIONS_TR = "CTOS4001R"          # 모의투자 미지원
#: 거래내역 매도매수 필터 -> SLL_BUY_DVSN_CD. all:전체/sell:매도/buy:매수.
_TX_SIDE_FILTER = {"all": "00", "sell": "01", "buy": "02"}

#: 미체결 매매구분코드(원장). 01:매도, 02:매수.
_SIDE = {"01": "sell", "02": "buy"}

#: 해외 잔고 시장 -> (OVRS_EXCG_CD, TR_CRCY_CD). 원장 코드표. 미국은 NASD(실전=미국전체).
_MARKETS: dict[str, tuple[str, str]] = {
    "US": ("NASD", "USD"),
    "HK": ("SEHK", "HKD"),
    "CN_SH": ("SHAA", "CNY"),
    "CN_SZ": ("SZAA", "CNY"),
    "JP": ("TKSE", "JPY"),
    "VN_HN": ("HASE", "VND"),
    "VN_HCM": ("VNSE", "VND"),
}


def fetch_positions(
    transport: Transport, *, cano: str, product_code: str, environment: Environment, market: str
) -> list[OverseasPosition]:
    """해외 보유 종목 전체(연속조회 소진까지). ``market`` 은 US/HK/CN_SH/CN_SZ/JP/VN_HN/VN_HCM."""
    try:
        exchange, currency = _MARKETS[market]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 해외 시장: {market!r} ({'/'.join(_MARKETS)})."
        ) from None
    rows = _walk_holdings(transport, cano, product_code, environment, exchange, currency)
    return _parse_positions(rows, exchange=exchange, default_currency=currency)


def fetch_balance(
    transport: Transport, *, cano: str, product_code: str, environment: Environment, market: str
) -> OverseasBalance:
    """해외 계좌 손익 요약(1콜, output2). ``market`` 은 US/HK/CN_SH/CN_SZ/JP/VN_HN/VN_HCM."""
    try:
        exchange, currency = _MARKETS[market]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 해외 시장: {market!r} ({'/'.join(_MARKETS)})."
        ) from None
    resp = _request_page(transport, cano, product_code, environment, exchange, currency, "", "")
    _raise_if_error(resp)
    summary = resp.body.get("output2")     # 계좌 요약(계좌 단위라 첫 페이지로 완결)
    if not isinstance(summary, Mapping):
        raise KISError(
            "해외 잔고 응답에 계좌 요약(output2)이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return OverseasBalance(
        exchange=exchange,
        purchase_amount=_money(summary, "frcr_pchs_amt1", currency),
        unrealized_pnl=_money(summary, "tot_evlu_pfls_amt", currency),
        realized_pnl=_money(summary, "ovrs_rlzt_pfls_amt", currency),
        total_pnl=_money(summary, "ovrs_tot_pfls", currency),
        return_percent=required_decimal(summary.get("tot_pftrt"), "tot_pftrt"),
        _raw=summary,
    )


def fetch_open_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment, market: str
) -> list[OverseasOpenOrder]:
    """해외 미체결 주문 전체(연속조회 소진까지). **모의투자 미지원**(demo면 :class:`KISUsageError`)."""
    if environment == "demo":
        raise KISUsageError("해외 미체결내역 조회는 모의투자 미지원이다(실전 계좌만).")
    try:
        exchange, currency = _MARKETS[market]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 해외 시장: {market!r} ({'/'.join(_MARKETS)})."
        ) from None
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code, "OVRS_EXCG_CD": exchange,
            "SORT_SQN": "DS", "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_OPEN_ORDERS_PATH, tr_id=_OPEN_ORDERS_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        page = resp.body.get("output")
        if not isinstance(page, list):  # 빈 미체결도 배열 -> 부재/비배열은 손상
            raise KISError(
                "해외 미체결 응답의 output 이 배열이 아니다.",
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
            f"해외 미체결 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    return _parse_open_orders(rows, default_currency=currency)


def _parse_open_orders(
    rows: list[Mapping[str, Any]], *, default_currency: str
) -> list[OverseasOpenOrder]:
    orders: list[OverseasOpenOrder] = []
    for row in rows:
        order_id = str(row.get("odno", "")).strip()
        if not order_id:  # 빈 행 skip
            continue
        currency = str(row.get("tr_crcy_cd", "")).strip() or default_currency
        orders.append(
            OverseasOpenOrder(
                symbol=str(row.get("pdno", "")).strip(),
                name=str(row.get("prdt_name", "")).strip(),
                exchange=str(row.get("ovrs_excg_cd", "")).strip(),
                order_id=order_id,
                side=_SIDE.get(str(row.get("sll_buy_dvsn_cd", "")).strip(), ""),
                quantity=required_int(row.get("ft_ord_qty"), "ft_ord_qty"),
                filled_quantity=required_int(row.get("ft_ccld_qty"), "ft_ccld_qty"),
                unfilled_quantity=required_int(row.get("nccs_qty"), "nccs_qty"),
                price=_money(row, "ft_ord_unpr3", currency),
                _raw=row,
            )
        )
    return orders


def fetch_buyable(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    symbol: str, exchange: str, price: object,
) -> OverseasBuyableAmount:
    """해외주식 매수가능금액. ``exchange`` 는 시세 거래소코드(NAS/NYS/AMS/HKS/SHS/SZS/TSE/HNX/HSX),
    ``price`` 는 의도한 주문단가(0보다 큰 유한값). 단발 조회(다음조회 불가)."""
    try:
        order_exchange = _ORDER_EXCHANGE[exchange][0]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 해외 거래소코드: {exchange!r} ({'/'.join(_ORDER_EXCHANGE)})."
        ) from None
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "OVRS_EXCG_CD": order_exchange,
        "OVRS_ORD_UNPR": _format_order_unit_price(price),
        "ITEM_CD": symbol,
    }
    resp = transport.request(
        method="GET", path=_BUYABLE_PATH, tr_id=_BUYABLE_TR[environment],
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise KISError(
            "해외 매수가능금액 응답에 output 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    currency = str(output.get("tr_crcy_cd", "")).strip()
    return OverseasBuyableAmount(
        symbol=symbol,
        exchange=order_exchange,
        currency=currency,
        orderable_foreign_cash=_money_or_zero(output, "ord_psbl_frcr_amt", currency),
        reusable_sell_amount=_money_or_zero(output, "sll_ruse_psbl_amt", currency),
        orderable_amount=_money_or_zero(output, "ovrs_ord_psbl_amt", currency),
        max_quantity=_decimal_or_zero(output, "max_ord_psbl_qty"),
        integrated_orderable_amount=_money_or_zero(output, "frcr_ord_psbl_amt1", currency),
        integrated_max_quantity=_decimal_or_zero(output, "ovrs_max_ord_psbl_qty"),
        exchange_rate=_decimal_or_zero(output, "exrt"),
        _raw=output,
    )


def fetch_transactions(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str, symbol: str | None = None, side: str = "all",
) -> list[OverseasTransaction]:
    """해외주식 일별 거래내역(연속조회 소진까지). ``start``/``end`` 는 등록일자 기간(YYYYMMDD),
    ``symbol`` 없으면 전체 종목, ``side`` = all/sell/buy. **모의투자 미지원**."""
    if environment == "demo":
        raise KISUsageError("해외주식 일별거래내역(inquire-period-trans)은 모의투자 미지원 -- 실전에서만.")
    try:
        side_code = _TX_SIDE_FILTER[side]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 side: {side!r} ({'/'.join(_TX_SIDE_FILTER)})."
        ) from None
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "ERLM_STRT_DT": start, "ERLM_END_DT": end,
            "OVRS_EXCG_CD": "", "PDNO": symbol or "",
            "SLL_BUY_DVSN_CD": side_code, "LOAN_DVSN_CD": "",
            "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_TRANSACTIONS_PATH, tr_id=_TRANSACTIONS_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 내역도 배열 -> 부재/비배열은 손상
            raise KISError(
                "해외 거래내역 응답의 output1 이 배열이 아니다.",
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
            f"해외 거래내역 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다."
        )
    return [_parse_transaction(row) for row in rows if str(row.get("pdno", "")).strip()]


def _parse_transaction(row: Mapping[str, Any]) -> OverseasTransaction:
    currency = str(row.get("crcy_cd", "")).strip()
    return OverseasTransaction(
        trade_date=_YYYYMMDD(row.get("trad_dt")),
        settlement_date=_YYYYMMDD(row.get("sttl_dt")),
        side=_SIDE.get(str(row.get("sll_buy_dvsn_cd", "")).strip(), ""),
        symbol=str(row.get("pdno", "")).strip(),
        name=str(row.get("ovrs_item_name", "")).strip(),
        quantity=_decimal_or_zero(row, "ccld_qty"),
        price=_money_or_zero(row, "ft_ccld_unpr2", currency),
        trade_amount=_money_or_zero(row, "tr_frcr_amt2", currency),
        settlement_amount=_money_or_zero(row, "frcr_excc_amt_1", currency),
        foreign_fee=_money_or_zero(row, "frcr_fee1", currency),
        domestic_won_fee=_decimal_or_zero(row, "dmst_wcrc_fee"),
        overseas_won_fee=_decimal_or_zero(row, "ovrs_wcrc_fee"),
        currency=currency,
        loan_type=str(row.get("loan_dvsn_name", "")).strip(),
        _raw=row,
    )


def _YYYYMMDD(value: object) -> date | None:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()  # noqa: DTZ007
    except ValueError:
        return None


def _format_order_unit_price(price: object) -> str:
    """주문단가를 KIS 와이어 정본으로. 0보다 큰 유한값이 아니면 거부."""
    try:
        amount = Decimal(str(price))
    except (ArithmeticError, ValueError) as err:
        raise KISUsageError(f"price 는 숫자여야 한다: {price!r}") from err
    if not amount.is_finite() or amount <= 0:
        raise KISUsageError(f"price 는 0보다 큰 유한값이어야 한다: {price!r}")
    return format_wire_decimal(amount)


def _money_or_zero(row: Mapping[str, Any], key: str, currency: str) -> Money:
    """없으면 0(해당 통화), 있으면 파싱(값 있는데 실패면 예외). 매수가능금액 필드는 모두 optional."""
    amount = optional_decimal(row.get(key), key)
    return Money(Decimal(0) if amount is None else amount, currency)


def _decimal_or_zero(row: Mapping[str, Any], key: str) -> Decimal:
    amount = optional_decimal(row.get(key), key)
    return Decimal(0) if amount is None else amount


def _request_page(
    transport: Transport, cano: str, product_code: str, environment: Environment,
    exchange: str, currency: str, ctx_fk: str, ctx_nk: str,
    *, tr_cont: str = "",
):
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "OVRS_EXCG_CD": exchange, "TR_CRCY_CD": currency,
        "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
    }
    return transport.request(
        method="GET", path=_POSITIONS_PATH, tr_id=_POSITIONS_TR[environment],
        params=params, idempotent=True, tr_cont=tr_cont,
    )


def _walk_holdings(
    transport: Transport, cano: str, product_code: str, environment: Environment,
    exchange: str, currency: str,
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        resp = _request_page(
            transport, cano, product_code, environment, exchange, currency, ctx_fk, ctx_nk,
            tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 계좌도 output1 을 빈 배열로 준다 -> 부재/비배열은 손상
            raise KISError(
                "해외 잔고 응답의 output1 이 종목 배열이 아니다.",
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
            f"해외 잔고 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    return rows


def _money(row: Mapping[str, Any], key: str, currency: str) -> Money:
    return Money(required_decimal(row.get(key), key), currency)


def _parse_positions(
    rows: list[Mapping[str, Any]], *, exchange: str, default_currency: str
) -> list[OverseasPosition]:
    positions: list[OverseasPosition] = []
    for row in rows:
        symbol = str(row.get("ovrs_pdno", "")).strip()
        if not symbol:  # 빈 행 skip
            continue
        currency = str(row.get("tr_crcy_cd", "")).strip() or default_currency
        positions.append(
            OverseasPosition(
                symbol=symbol,
                name=str(row.get("ovrs_item_name", "")).strip(),
                exchange=exchange,
                quantity=required_int(row.get("ovrs_cblc_qty"), "ovrs_cblc_qty"),
                sellable_quantity=required_int(row.get("ord_psbl_qty"), "ord_psbl_qty"),
                average_price=_money(row, "pchs_avg_pric", currency),
                current_price=_money(row, "now_pric2", currency),
                purchase_amount=_money(row, "frcr_pchs_amt1", currency),
                market_value=_money(row, "ovrs_stck_evlu_amt", currency),
                unrealized_pnl=_money(row, "frcr_evlu_pfls_amt", currency),
                pnl_percent=required_decimal(row.get("evlu_pfls_rt"), "evlu_pfls_rt"),
                _raw=row,
            )
        )
    return positions
