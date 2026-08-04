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
from typing import Any

from .._domestic.market_data import _raise_if_error
from .._wire import required_decimal, required_int
from ..errors import KisError, KisUsageError
from ..money import Money
from ..overseas_items import OverseasBalance, OverseasOpenOrder, OverseasPosition
from ..transport import Environment, Transport

_POSITIONS_PATH = "/uapi/overseas-stock/v1/trading/inquire-balance"
_POSITIONS_TR = {"real": "TTTS3012R", "demo": "VTTS3012R"}
#: 잔고 종목배열 연속조회 페이지 상한. 여기 닿으면 부분 결과로 자르지 않고 fail-closed.
_MAX_PAGES = 100

_OPEN_ORDERS_PATH = "/uapi/overseas-stock/v1/trading/inquire-nccs"
_OPEN_ORDERS_TR = "TTTS3018R"           # 모의투자 미지원(실전만)
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
        raise KisUsageError(
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
        raise KisUsageError(
            f"지원하지 않는 해외 시장: {market!r} ({'/'.join(_MARKETS)})."
        ) from None
    resp = _request_page(transport, cano, product_code, environment, exchange, currency, "", "")
    _raise_if_error(resp)
    summary = resp.body.get("output2")     # 계좌 요약(계좌 단위라 첫 페이지로 완결)
    if not isinstance(summary, Mapping):
        raise KisError(
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
    """해외 미체결 주문 전체(연속조회 소진까지). **모의투자 미지원**(demo면 :class:`KisUsageError`)."""
    if environment == "demo":
        raise KisUsageError("해외 미체결내역 조회는 모의투자 미지원이다(실전 계좌만).")
    try:
        exchange, currency = _MARKETS[market]
    except KeyError:
        raise KisUsageError(
            f"지원하지 않는 해외 시장: {market!r} ({'/'.join(_MARKETS)})."
        ) from None
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk = "", ""
    for _page in range(_MAX_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code, "OVRS_EXCG_CD": exchange,
            "SORT_SQN": "DS", "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_OPEN_ORDERS_PATH, tr_id=_OPEN_ORDERS_TR,
            params=params, idempotent=True,
        )
        _raise_if_error(resp)
        page = resp.body.get("output")
        if not isinstance(page, list):  # 빈 미체결도 배열 -> 부재/비배열은 손상
            raise KisError(
                "해외 미체결 응답의 output 이 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        if not ctx_nk:
            break
    else:
        raise KisError(
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


def _request_page(
    transport: Transport, cano: str, product_code: str, environment: Environment,
    exchange: str, currency: str, ctx_fk: str, ctx_nk: str,
):
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "OVRS_EXCG_CD": exchange, "TR_CRCY_CD": currency,
        "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
    }
    return transport.request(
        method="GET", path=_POSITIONS_PATH, tr_id=_POSITIONS_TR[environment],
        params=params, idempotent=True,
    )


def _walk_holdings(
    transport: Transport, cano: str, product_code: str, environment: Environment,
    exchange: str, currency: str,
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk = "", ""
    for _page in range(_MAX_PAGES):
        resp = _request_page(
            transport, cano, product_code, environment, exchange, currency, ctx_fk, ctx_nk
        )
        _raise_if_error(resp)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 계좌도 output1 을 빈 배열로 준다 -> 부재/비배열은 손상
            raise KisError(
                "해외 잔고 응답의 output1 이 종목 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        # JSON null 은 str(...) 로 "None"(truthy) 이 되니 None 을 먼저 ""로 눌러 종료 판정을 지킨다.
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        if not ctx_nk:
            break
    else:
        raise KisError(
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
