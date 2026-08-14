"""해외주식 계좌 조회 (내부) -- 잔고(보유종목)를 :class:`OverseasPosition` 리스트로.

국내(:mod:`kis_trader._domestic.account`)와 대칭. 해외 잔고는 **거래소 그룹(OVRS_EXCG_CD)+통화
(TR_CRCY_CD)별**로 조회하며 금액이 외화라 :class:`~kis_trader.money.Money` 로 통화를 함께 담는다.

KIS URL/TR-ID (KIS 명세 대조):
- 잔고: ``GET /uapi/overseas-stock/v1/trading/inquire-balance`` (실전 ``TTTS3012R`` / 모의 ``VTTS3012R``).
  ``OVRS_EXCG_CD`` 는 NASD(미국전체)/SEHK(홍콩)/SHAA(상해)/SZAA(심천)/TKSE(일본)/HASE(하노이)/VNSE(호치민),
  ``TR_CRCY_CD`` 는 USD/HKD/CNY/JPY/VND. (시세 조회의 EXCD 코드와 다르다.)
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .._datetime import parse_optional_kst_date
from .._response import _fetch_paginated_rows, _raise_if_error
from .._wire import (
    format_wire_decimal,
    optional_decimal,
    required_decimal,
    required_int,
)
from ..errors import KISError, KISUsageError
from ..money import Money
from ..overseas_items import (
    OverseasAlgoExecution,
    OverseasAlgoOrder,
    OverseasBalance,
    OverseasBalancePosition,
    OverseasBuyableAmount,
    OverseasCurrencyBalance,
    OverseasForeignMargin,
    OverseasOpenOrder,
    OverseasPeriodProfit,
    OverseasPeriodProfitRow,
    OverseasPosition,
    OverseasPresentBalance,
    OverseasSettlementBalance,
    OverseasTransaction,
)
from ..transport import Environment, Transport
from .orders import _ORDER_EXCHANGE

if TYPE_CHECKING:
    from .._literals import Numeric

_POSITIONS_PATH = "/uapi/overseas-stock/v1/trading/inquire-balance"
_POSITIONS_TR = {"real": "TTTS3012R", "paper": "VTTS3012R"}
#: 잔고 종목배열 연속조회 페이지 상한. 여기 닿으면 부분 결과로 자르지 않고 fail-closed.
_MAX_PAGES = 100

_OPEN_ORDERS_PATH = "/uapi/overseas-stock/v1/trading/inquire-nccs"
_OPEN_ORDERS_TR = "TTTS3018R"           # 모의투자 미지원(실전만)

_BUYABLE_PATH = "/uapi/overseas-stock/v1/trading/inquire-psamount"
_BUYABLE_TR = {"real": "TTTS3007R", "paper": "VTTS3007R"}

_TRANSACTIONS_PATH = "/uapi/overseas-stock/v1/trading/inquire-period-trans"
_TRANSACTIONS_TR = "CTOS4001R"          # 모의투자 미지원

_FOREIGN_MARGIN_PATH = "/uapi/overseas-stock/v1/trading/foreign-margin"
_FOREIGN_MARGIN_TR = "TTTC2101R"        # 모의투자 미지원

_ALGO_ORDNO_PATH = "/uapi/overseas-stock/v1/trading/algo-ordno"
_ALGO_ORDNO_TR = "TTTS6058R"            # 모의투자 미지원
_ALGO_CCNL_PATH = "/uapi/overseas-stock/v1/trading/inquire-algo-ccnl"
_ALGO_CCNL_TR = "TTTS6059R"             # 모의투자 미지원

_PRESENT_BALANCE_PATH = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
_PRESENT_BALANCE_TR = {"real": "CTRP6504R", "paper": "VTRP6504R"}  # 모의는 output3(요약)만
_SETTLEMENT_BALANCE_PATH = "/uapi/overseas-stock/v1/trading/inquire-paymt-stdr-balance"
_SETTLEMENT_BALANCE_TR = "CTRP6010R"    # 모의투자 미지원
_PERIOD_PROFIT_PATH = "/uapi/overseas-stock/v1/trading/inquire-period-profit"
_PERIOD_PROFIT_TR = "TTTS3039R"         # 모의투자 미지원

#: 잔고 리포트 국가코드(NATN_CD). 000:전체/840:미국/344:홍콩/156:중국/392:일본/704:베트남.
_NATION_CODE = {"all": "000", "US": "840", "HK": "344", "CN": "156", "JP": "392", "VN": "704"}
#: 거래내역 매도매수 필터 -> SLL_BUY_DVSN_CD. all:전체/sell:매도/buy:매수.
_TX_SIDE_FILTER = {"all": "00", "sell": "01", "buy": "02"}

#: 미체결 매매구분코드(KIS 명세). 01:매도, 02:매수.
_SIDE = {"01": "sell", "02": "buy"}

#: 해외 잔고 시장 -> (OVRS_EXCG_CD, TR_CRCY_CD). KIS 코드표. 미국은 NASD(실전=미국전체).
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
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    market: str | None = None,
) -> list[OverseasPosition]:
    """해외 보유 종목 전체(연속조회 소진까지). ``market`` 은 US/HK/CN_SH/CN_SZ/JP/VN_HN/VN_HCM;
    ``None`` 이면 전체 시장 그룹을 순회해 합친다(KIS는 그룹별 조회만 제공하므로 그룹 수만큼 호출)."""
    if market is None:
        out: list[OverseasPosition] = []
        for group in _MARKETS:
            out.extend(fetch_positions(
                transport, cano=cano, product_code=product_code, environment=environment,
                market=group,
            ))
        return out
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
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    market: str | None = None,
) -> list[OverseasOpenOrder]:
    """해외 미체결 주문 전체(연속조회 소진까지). ``market`` 생략(``None``)이면 전체 시장 그룹을 순회해
    합친다. **모의투자 미지원**(demo면 :class:`KISUsageError`)."""
    if environment == "paper":
        raise KISUsageError("해외 미체결내역 조회는 모의투자 미지원이다(실전 계좌만).")
    if market is None:
        out: list[OverseasOpenOrder] = []
        for group in _MARKETS:
            out.extend(fetch_open_orders(
                transport, cano=cano, product_code=product_code, environment=environment,
                market=group,
            ))
        return out
    try:
        exchange, currency = _MARKETS[market]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 해외 시장: {market!r} ({'/'.join(_MARKETS)})."
        ) from None
    rows = _fetch_paginated_rows(
        transport,
        path=_OPEN_ORDERS_PATH, tr_id=_OPEN_ORDERS_TR,
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code, "OVRS_EXCG_CD": exchange,
            "SORT_SQN": "DS", "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        },
        output_key="output", max_pages=_MAX_PAGES, ctx_width=200,
        cap_message=(
            f"해외 미체결 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        ),
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
                order_price=_money(row, "ft_ord_unpr3", currency),
                _raw=row,
            )
        )
    return orders


def fetch_buyable_amount(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    symbol: str, exchange: str, price: Numeric,
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
    if environment == "paper":
        raise KISUsageError("해외주식 일별거래내역(inquire-period-trans)은 모의투자 미지원 -- 실전에서만.")
    try:
        side_code = _TX_SIDE_FILTER[side]
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 side: {side!r} ({'/'.join(_TX_SIDE_FILTER)})."
        ) from None
    rows = _fetch_paginated_rows(
        transport,
        path=_TRANSACTIONS_PATH, tr_id=_TRANSACTIONS_TR,
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "ERLM_STRT_DT": start, "ERLM_END_DT": end,
            "OVRS_EXCG_CD": "", "PDNO": symbol or "",
            "SLL_BUY_DVSN_CD": side_code, "LOAN_DVSN_CD": "",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        },
        output_key="output1", max_pages=_MAX_PAGES,  # 해외인데 커서 폭이 100(엔드포인트별 상이)
        cap_message=(
            f"해외 거래내역 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다."
        ),
    )
    return [_parse_transaction(row) for row in rows if str(row.get("pdno", "")).strip()]


def _parse_transaction(row: Mapping[str, Any]) -> OverseasTransaction:
    currency = str(row.get("crcy_cd", "")).strip()
    return OverseasTransaction(
        trade_date=parse_optional_kst_date(row.get("trad_dt")),
        settlement_date=parse_optional_kst_date(row.get("sttl_dt")),
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


def fetch_foreign_margin(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> list[OverseasForeignMargin]:
    """통화별 해외증거금(외화 예수금·증거금·주문가능금액). 단발 조회. **모의투자 미지원**."""
    if environment == "paper":
        raise KISUsageError("해외증거금 통화별조회(foreign-margin)는 모의투자 미지원 -- 실전에서만.")
    params = {"CANO": cano, "ACNT_PRDT_CD": product_code}
    resp = transport.request(
        method="GET", path=_FOREIGN_MARGIN_PATH, tr_id=_FOREIGN_MARGIN_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):  # 빈 계좌도 배열 -> 부재/비배열은 손상
        raise KISError(
            "해외증거금 응답의 output 이 배열이 아니다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    margins: list[OverseasForeignMargin] = []
    for row in rows:
        currency = str(row.get("crcy_cd", "")).strip()
        if not currency:  # 통화코드 없는 패딩 행 -- 건너뜀
            continue
        margins.append(
            OverseasForeignMargin(
                country_name=str(row.get("natn_name", "")).strip(),
                currency=currency,
                deposit=_money_or_zero(row, "frcr_dncl_amt1", currency),
                unsettled_buy_amount=_money_or_zero(row, "ustl_buy_amt", currency),
                unsettled_sell_amount=_money_or_zero(row, "ustl_sll_amt", currency),
                receivable_amount=_money_or_zero(row, "frcr_rcvb_amt", currency),
                margin_amount=_money_or_zero(row, "frcr_mgn_amt", currency),
                general_orderable_amount=_money_or_zero(row, "frcr_gnrl_ord_psbl_amt", currency),
                orderable_amount=_money_or_zero(row, "frcr_ord_psbl_amt1", currency),
                integrated_orderable_amount=_money_or_zero(row, "itgr_ord_psbl_amt", currency),
                exchange_rate=_decimal_or_zero(row, "bass_exrt"),
                _raw=row,
            )
        )
    return margins


def fetch_algo_orders(
    transport: Transport, *, cano: str, product_code: str, environment: Environment
) -> list[OverseasAlgoOrder]:
    """해외 지정가(TWAP/VWAP 등 알고) 주문 목록. 각 건의 ``order_id``/``branch_number`` 로 체결내역을
    조회한다(:func:`fetch_algo_executions`). **모의투자 미지원**."""
    if environment == "paper":
        raise KISUsageError("해외 지정가주문번호조회(algo-ordno)는 모의투자 미지원 -- 실전에서만.")
    rows = _fetch_paginated_rows(
        transport,
        path=_ALGO_ORDNO_PATH, tr_id=_ALGO_ORDNO_TR,
        base_params={"CANO": cano, "ACNT_PRDT_CD": product_code,
                     "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""},
        output_key="output", max_pages=_MAX_PAGES, ctx_width=200,
        cap_message="해외 지정가주문번호조회가 페이지 상한에 도달했으나 연속조회가 남아있다.",
    )
    return [
        OverseasAlgoOrder(
            order_id=str(row.get("odno", "")).strip(),
            trade_type=str(row.get("trad_dvsn_name", "")).strip(),
            symbol=str(row.get("pdno", "")).strip(),
            name=str(row.get("item_name", "")).strip(),
            quantity=_decimal_or_zero(row, "ft_ord_qty"),
            order_price=_decimal_or_zero(row, "ft_ord_unpr3"),
            filled_quantity=_decimal_or_zero(row, "ft_ccld_qty"),
            split_attribute=str(row.get("splt_buy_attr_name", "")).strip(),
            branch_number=str(row.get("ord_gno_brno", "")).strip(),
            _raw=row,
        )
        for row in rows if str(row.get("odno", "")).strip()
    ]


def fetch_algo_executions(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    order_date: str, order_id: str, branch_number: str = "",
) -> list[OverseasAlgoExecution]:
    """한 해외 알고주문(``order_id``)의 체결내역. ``order_date``(YYYYMMDD)는 주문일자, ``branch_number``
    는 주문채번지점번호(:func:`fetch_algo_orders` 의 ``branch_number``). 응답 키가 대문자다. **모의투자 미지원**."""
    if environment == "paper":
        raise KISUsageError("해외 지정가체결내역조회(inquire-algo-ccnl)는 모의투자 미지원 -- 실전에서만.")
    rows = _fetch_paginated_rows(
        transport,
        path=_ALGO_CCNL_PATH, tr_id=_ALGO_CCNL_TR,
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "ORD_DT": order_date, "ORD_GNO_BRNO": branch_number, "ODNO": order_id,
            "TTLZ_ICLD_YN": "", "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        },
        output_key="output", max_pages=_MAX_PAGES, ctx_width=200,
        cap_message="해외 지정가체결내역조회가 페이지 상한에 도달했으나 연속조회가 남아있다.",
    )
    return [
        OverseasAlgoExecution(
            sequence=str(row.get("CCLD_SEQ", "")).strip(),
            executed_at=_parse_hhmmss(row.get("CCLD_BTWN")),
            symbol=str(row.get("PDNO", "")).strip(),
            name=str(row.get("ITEM_NAME", "")).strip(),
            quantity=_decimal_or_zero(row, "FT_CCLD_QTY"),
            price=_decimal_or_zero(row, "FT_CCLD_UNPR3"),
            amount=_decimal_or_zero(row, "FT_CCLD_AMT3"),
            _raw=row,
        )
        for row in rows if str(row.get("CCLD_SEQ", "")).strip()
    ]


# --- 체결기준현재잔고 (CTRP6504R) -----------------------------------------
def fetch_present_balance(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    won_basis: bool = True, nation: str = "all", market_code: str = "00", inquiry: str = "00",
) -> OverseasPresentBalance:
    """해외주식 체결기준현재잔고 -- 보유 종목(output1)·통화별 예수금(output2)·계좌 요약(output3).
    실전(CTRP6504R)은 3블록 전부, 모의(VTRP6504R)는 요약만 온다.

    ``won_basis`` 원화(True)/외화(False) 기준, ``nation`` 국가(``"all"``/``"US"``/``"HK"``/``"CN"``/``"JP"``/
    ``"VN"``), ``market_code`` 거래시장코드(KIS 코드표, ``"00"``=전체), ``inquiry`` 조회구분(``"00"`` 전체/
    ``"01"`` 일반/``"02"`` 미니스탁).

    .. note:: 요약(output3) 필드는 KIS 예시가 output1 에서 잘려 레이아웃 기준이다 -- 전체 원본은 결과 ``_raw``.
    """
    try:
        nation_code = _NATION_CODE[nation]      # 알 수 없는 nation 은 조용히 전체(000)로 넓히지 않고 거부한다
    except KeyError:
        raise KISUsageError(
            f"지원하지 않는 nation: {nation!r} ({'/'.join(_NATION_CODE)})."
        ) from None
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "WCRC_FRCR_DVSN_CD": "01" if won_basis else "02",
        "NATN_CD": nation_code,
        "TR_MKET_CD": market_code, "INQR_DVSN_CD": inquiry,
    }
    resp = transport.request(
        method="GET", path=_PRESENT_BALANCE_PATH, tr_id=_PRESENT_BALANCE_TR[environment],
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    summary = _first_object(resp.body.get("output3"))
    return OverseasPresentBalance(
        positions=tuple(_report_position(row) for row in _as_rows(resp.body.get("output1"))),
        currencies=tuple(_currency_balance(row) for row in _as_rows(resp.body.get("output2"))),
        total_purchase_amount=_decimal_or_zero(summary, "pchs_amt_smtl_amt"),
        total_evaluation_amount=_decimal_or_zero(summary, "evlu_amt_smtl_amt"),
        total_eval_pnl=_decimal_or_zero(summary, "tot_evlu_pfls_amt"),
        total_asset=_decimal_or_zero(summary, "tot_asst_amt"),
        eval_return_rate=_decimal_or_zero(summary, "evlu_erng_rt1"),
        _raw=summary,
    )


# --- 결제기준잔고 (CTRP6010R) ---------------------------------------------
def fetch_settlement_balance(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    basis_date: str, won_basis: bool = True, inquiry: str = "00",
) -> OverseasSettlementBalance:
    """해외주식 결제기준잔고 -- ``basis_date``(YYYYMMDD) 결제 기준의 보유 종목·통화별 예수금·계좌 요약.
    **모의투자 미지원**. ``won_basis`` 원화(True)/외화(False) 기준, ``inquiry`` 조회구분(``"00"`` 전체)."""
    if environment == "paper":
        raise KISUsageError(
            "해외주식 결제기준잔고(inquire-paymt-stdr-balance)는 모의투자 미지원 -- 실전에서만."
        )
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code, "BASS_DT": basis_date,
        "WCRC_FRCR_DVSN_CD": "01" if won_basis else "02", "INQR_DVSN_CD": inquiry,
    }
    resp = transport.request(
        method="GET", path=_SETTLEMENT_BALANCE_PATH, tr_id=_SETTLEMENT_BALANCE_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    summary = _first_object(resp.body.get("output3"))
    return OverseasSettlementBalance(
        positions=tuple(_report_position(row) for row in _as_rows(resp.body.get("output1"))),
        currencies=tuple(_currency_balance(row) for row in _as_rows(resp.body.get("output2"))),
        total_purchase_amount=_decimal_or_zero(summary, "pchs_amt_smtl_amt"),
        total_eval_pnl=_decimal_or_zero(summary, "tot_evlu_pfls_amt"),
        eval_return_rate=_decimal_or_zero(summary, "evlu_erng_rt1"),
        total_deposit=_decimal_or_zero(summary, "tot_dncl_amt"),
        total_won_evaluation=_decimal_or_zero(summary, "wcrc_evlu_amt_smtl"),
        total_asset=_decimal_or_zero(summary, "tot_asst_amt2"),
        total_loan=_decimal_or_zero(summary, "tot_loan_amt"),
        _raw=summary,
    )


# --- 기간손익 (TTTS3039R) --------------------------------------------------
def fetch_period_profit(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    start: str, end: str, exchange: str = "", nation: str = "", currency: str = "",
    symbol: str = "", won_basis: bool = False,
) -> OverseasPeriodProfit:
    """해외주식 기간손익 -- ``start``~``end``(YYYYMMDD) 매도청산 종목별 실현손익(output1)과 총계(output2).
    **모의투자 미지원**. ``exchange`` 거래소(OVRS_EXCG_CD, 공란=전체), ``currency`` 통화(공란=전체),
    ``symbol`` 종목(공란=전체), ``won_basis`` 원화(True)/외화(False) 기준.

    .. note:: KIS 응답예시가 비어 있어 필드는 레이아웃 기준이다 -- 실제 응답과 다를 수 있으므로 각 행과
       결과의 ``_raw`` 로 원본을 함께 노출한다.
    """
    if environment == "paper":
        raise KISUsageError(
            "해외주식 기간손익(inquire-period-profit)은 모의투자 미지원 -- 실전에서만."
        )
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] = {}
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(_MAX_PAGES):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "OVRS_EXCG_CD": exchange, "NATN_CD": nation, "CRCY_CD": currency, "PDNO": symbol,
            "INQR_STRT_DT": start, "INQR_END_DT": end,
            "WCRC_FRCR_DVSN_CD": "02" if won_basis else "01",
            "CTX_AREA_FK200": ctx_fk, "CTX_AREA_NK200": ctx_nk,
        }
        resp = transport.request(
            method="GET", path=_PERIOD_PROFIT_PATH, tr_id=_PERIOD_PROFIT_TR,
            params=params, idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        page = resp.body.get("output1")
        if not isinstance(page, list):
            raise KISError(
                "해외 기간손익 응답의 output1 이 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        summary = _first_object(resp.body.get("output2")) or summary
        if resp.tr_cont not in ("F", "M"):
            break
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        tr_cont = "N"
    else:
        raise KISError("해외 기간손익이 페이지 상한에 도달했으나 연속조회가 남아있다.")
    profit_rows = tuple(
        OverseasPeriodProfitRow(
            trade_day=parse_optional_kst_date(row.get("trad_day")),
            symbol=str(row.get("ovrs_pdno", "")).strip(),
            name=str(row.get("ovrs_item_name", "")).strip(),
            sold_quantity=_decimal_or_zero(row, "slcl_qty"),
            average_purchase_price=_decimal_or_zero(row, "pchs_avg_pric"),
            purchase_amount=_decimal_or_zero(row, "frcr_pchs_amt1"),
            average_sell_price=_decimal_or_zero(row, "avg_sll_unpr"),
            sell_amount=_decimal_or_zero(row, "frcr_sll_amt_smtl1"),
            sell_expense=_decimal_or_zero(row, "stck_sll_tlex"),
            realized_pnl=_decimal_or_zero(row, "ovrs_rlzt_pfls_amt"),
            return_rate=_decimal_or_zero(row, "pftrt"),
            exchange_rate=_decimal_or_zero(row, "exrt"),
            exchange=str(row.get("ovrs_excg_cd", "")).strip(),
            first_exchange_rate=_decimal_or_zero(row, "frst_bltn_exrt"),
            _raw=row,
        )
        for row in rows
    )
    return OverseasPeriodProfit(
        rows=profit_rows,
        total_sell_amount=_decimal_or_zero(summary, "stck_sll_amt_smtl"),
        total_buy_amount=_decimal_or_zero(summary, "stck_buy_amt_smtl"),
        total_fee=_decimal_or_zero(summary, "smtl_fee1"),
        settlement_amount=_decimal_or_zero(summary, "excc_dfrm_amt"),
        total_realized_pnl=_decimal_or_zero(summary, "ovrs_rlzt_pfls_tot_amt"),
        total_return_rate=_decimal_or_zero(summary, "tot_pftrt"),
        basis_date=parse_optional_kst_date(summary.get("bass_dt")),
        exchange_rate=_decimal_or_zero(summary, "exrt"),
        _raw=summary,
    )


def _as_rows(block: object) -> list[Mapping[str, Any]]:
    """output 배열을 종목 dict 리스트로. 객체 하나면 1원소 리스트, 비면 빈 리스트(fail-soft)."""
    if isinstance(block, list):
        return [r for r in block if isinstance(r, Mapping)]
    if isinstance(block, Mapping):
        return [block]
    return []


def _first_object(block: object) -> Mapping[str, Any]:
    """output(요약)이 객체면 그대로, 1원소 배열이면 첫 원소, 비면 빈 dict(fail-soft)."""
    if isinstance(block, Mapping):
        return block
    if isinstance(block, list) and block and isinstance(block[0], Mapping):
        return block[0]
    return {}


def _report_position(row: Mapping[str, Any]) -> OverseasBalancePosition:
    currency = str(row.get("buy_crcy_cd", "")).strip()
    return OverseasBalancePosition(
        symbol=str(row.get("pdno", "")).strip(),
        name=str(row.get("prdt_name", "")).strip(),
        balance_quantity=_decimal_or_zero(row, "cblc_qty13"),
        orderable_quantity=_decimal_or_zero(row, "ord_psbl_qty1"),
        average_price=_money_or_zero(row, "avg_unpr3", currency),
        current_price=_money_or_zero(row, "ovrs_now_pric1", currency),
        purchase_amount=_money_or_zero(row, "frcr_pchs_amt", currency),
        market_value=_money_or_zero(row, "frcr_evlu_amt2", currency),
        unrealized_pnl=_money_or_zero(row, "evlu_pfls_amt2", currency),
        unrealized_pnl_rate=_decimal_or_zero(row, "evlu_pfls_rt1"),
        loan_balance=_money_or_zero(row, "loan_rmnd", currency),
        collateral_quantity=_decimal_or_zero(row, "mgge_qty"),
        exchange=str(row.get("ovrs_excg_cd", "")).strip(),
        market_name=str(row.get("tr_mket_name", "")).strip(),
        country_name=str(row.get("natn_kor_name", "")).strip(),
        currency=currency,
        exchange_rate=_decimal_or_zero(row, "bass_exrt"),
        _raw=row,
    )


def _currency_balance(row: Mapping[str, Any]) -> OverseasCurrencyBalance:
    currency = str(row.get("crcy_cd", "")).strip()
    return OverseasCurrencyBalance(
        currency=currency,
        currency_name=str(row.get("crcy_cd_name", "")).strip(),
        deposit=_money_or_zero(row, "frcr_dncl_amt_2", currency),
        first_exchange_rate=_decimal_or_zero(row, "frst_bltn_exrt"),
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
    return _fetch_paginated_rows(
        transport,
        path=_POSITIONS_PATH, tr_id=_POSITIONS_TR[environment],
        base_params={
            "CANO": cano, "ACNT_PRDT_CD": product_code,
            "OVRS_EXCG_CD": exchange, "TR_CRCY_CD": currency,
            "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        },
        output_key="output1", max_pages=_MAX_PAGES, ctx_width=200,
        cap_message=(
            f"해외 잔고 조회가 {_MAX_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        ),
    )


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
