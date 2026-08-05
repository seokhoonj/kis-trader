"""장내채권 시세 조회 (내부) -- 채권 현재가를 :class:`BondQuote` 로.

사용자면은 채권 핸들(:class:`~kis_openapi.bond.Bond`, ``kis.bond(code)``)이다. 채권은 시장구분 ``B`` +
표준코드(ISIN, 예: KR2033022D33)로 조회한다.

KIS URL/TR-id (원장 대조):
- 채권 현재가: ``GET .../domestic-bond/v1/quotations/inquire-price`` ``FHKBJ773400C0``.
- 채권 호가: ``GET .../domestic-bond/v1/quotations/inquire-asking-price`` ``FHKBJ773401C0``.
- 채권 체결: ``GET .../domestic-bond/v1/quotations/inquire-ccnl`` ``FHKBJ773403C0``.
  (모두 ``FID_COND_MRKT_DIV_CODE=B`` + ``FID_INPUT_ISCD=표준코드``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .._wire import optional_decimal, optional_int, required_decimal, required_int
from ..bond_items import BondInfo, BondQuote
from ..order_book import OrderBook
from ..trade import Trade
from ..transport import Transport
from .market_data import (
    _KST,
    _apply_change_sign,
    _missing_block_error,
    _parse_bar_timestamp,
    _parse_intraday_timestamp,
    _price_levels,
    _raise_if_error,
)

_QUOTE_PATH = "/uapi/domestic-bond/v1/quotations/inquire-price"
_QUOTE_TR = "FHKBJ773400C0"
_ORDER_BOOK_PATH = "/uapi/domestic-bond/v1/quotations/inquire-asking-price"
_ORDER_BOOK_TR = "FHKBJ773401C0"
_TRADES_PATH = "/uapi/domestic-bond/v1/quotations/inquire-ccnl"
_TRADES_TR = "FHKBJ773403C0"
#: 채권 조회의 시장구분 코드(원장: 채권 B).
_MARKET_DIV = "B"


def fetch_quote(transport: Transport, *, code: str) -> BondQuote:
    """채권 현재가 스냅샷. ``code`` 는 표준코드(ISIN)."""
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_QUOTE_PATH, tr_id=_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_quote(output, code=code, as_of=datetime.now(_KST))


def _parse_quote(output: Mapping[str, Any], *, code: str, as_of: datetime) -> BondQuote:
    sign = str(output.get("prdy_vrss_sign", "")).strip()
    return BondQuote(
        code=code,
        name=str(output.get("hts_kor_isnm", "")).strip(),
        price=required_decimal(output.get("bond_prpr"), "bond_prpr"),
        open=required_decimal(output.get("bond_oprc"), "bond_oprc"),
        high=required_decimal(output.get("bond_hgpr"), "bond_hgpr"),
        low=required_decimal(output.get("bond_lwpr"), "bond_lwpr"),
        previous_close=required_decimal(output.get("bond_prdy_clpr"), "bond_prdy_clpr"),
        change=_apply_change_sign(
            required_decimal(output.get("bond_prdy_vrss"), "bond_prdy_vrss"), sign
        ),
        change_percent=_apply_change_sign(
            required_decimal(output.get("prdy_ctrt"), "prdy_ctrt"), sign
        ),
        volume=required_int(output.get("acml_vol"), "acml_vol"),
        yield_rate=optional_decimal(output.get("ernn_rate"), "ernn_rate"),
        as_of=as_of,
        _raw=output,
    )


def fetch_order_book(transport: Transport, *, code: str) -> OrderBook:
    """채권 호가창(5단계 매수/매도 심도). ``code`` 는 표준코드(ISIN).

    채권 호가는 종목 :meth:`~kis_openapi.ticker.Ticker.order_book` 과 같은 :class:`OrderBook`
    로 돌려주되, 채권은 5단계(주식은 10단계)다. 가격 키는 ``bond_askp``/``bond_bidp``, 잔량 키는
    ``askp_rsqn``/``bidp_rsqn`` -- 빈/0 단계는 건너뛴다.
    """
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_ORDER_BOOK_PATH, tr_id=_ORDER_BOOK_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return OrderBook(
        symbol=code,
        market=_MARKET_DIV,
        bids=_price_levels(output, "bond_bidp", "bidp_rsqn"),
        asks=_price_levels(output, "bond_askp", "askp_rsqn"),
        total_bid_quantity=optional_int(output.get("total_bidp_rsqn"), "total_bidp_rsqn") or 0,
        total_ask_quantity=optional_int(output.get("total_askp_rsqn"), "total_askp_rsqn") or 0,
        as_of=datetime.now(_KST),
        _raw=output,
    )


def fetch_trades(transport: Transport, *, code: str) -> list[Trade]:
    """채권의 최근 체결 목록(최신순). ``code`` 는 표준코드(ISIN).

    종목 :meth:`~kis_openapi.ticker.Ticker.trades` 와 같은 :class:`Trade` 로 돌려준다. 체결가는
    채권가(``bond_prpr``), 시각은 조회일 날짜를 붙인 KST-aware(장 밖 조회면 직전 세션 체결이 조회일
    날짜로 찍힐 수 있다).
    """
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_TRADES_PATH, tr_id=_TRADES_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):             # 성공 응답인데 체결 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_trades(rows, code=code, as_of=datetime.now(_KST))


def _parse_trades(
    rows: Sequence[Mapping[str, Any]], *, code: str, as_of: datetime
) -> list[Trade]:
    trades: list[Trade] = []
    for row in rows:
        time_text = str(row.get("stck_cntg_hour", "")).strip()
        price_text = str(row.get("bond_prpr", "")).strip()
        if not time_text or not price_text:    # 빈 행 건너뜀
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        trades.append(
            Trade(
                symbol=code,
                timestamp=_parse_intraday_timestamp(time_text, as_of),
                price=required_decimal(price_text, "bond_prpr"),
                quantity=required_int(row.get("cntg_vol"), "cntg_vol"),
                change=_apply_change_sign(
                    required_decimal(row.get("bond_prdy_vrss"), "bond_prdy_vrss"), sign
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                _raw=row,
            )
        )
    return trades


_INFO_PATH = "/uapi/domestic-bond/v1/quotations/search-bond-info"
_INFO_TR = "CTPF1114R"


def _parse_optional_date(value: object) -> datetime | None:
    """YYYYMMDD 를 KST-aware datetime 으로. 빈 값이나 0-채움 센티넬("00000000")은 ``None``."""
    text = str(value or "").strip()
    return _parse_bar_timestamp(text) if text and text.strip("0") else None


def fetch_info(transport: Transport, *, code: str) -> BondInfo:
    """채권 기본/발행 정보(발행일·만기·표면금리·만기수익률·통화). ``code`` 는 표준코드(ISIN)."""
    params = {"PDNO": code, "PRDT_TYPE_CD": "302"}      # 302: 채권
    resp = transport.request(
        method="GET", path=_INFO_PATH, tr_id=_INFO_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output", resp)
    return BondInfo(
        code=code,
        name=str(output.get("ksd_bond_item_name", "")).strip(),
        english_name=str(output.get("ksd_bond_item_eng_name", "")).strip(),
        currency=str(output.get("iso_crcy_cd", "")).strip(),
        issue_date=_parse_optional_date(output.get("issu_dt")),
        maturity_date=_parse_optional_date(output.get("rdpt_dt")),
        listing_date=_parse_optional_date(output.get("lstg_dt")),
        coupon_rate=optional_decimal(output.get("ksd_rcvg_bond_srfc_inrt"),
                                     "ksd_rcvg_bond_srfc_inrt"),
        discount_rate=optional_decimal(output.get("ksd_rcvg_bond_dsct_rt"),
                                       "ksd_rcvg_bond_dsct_rt"),
        redemption_rate=optional_decimal(output.get("bond_expd_rdpt_rt"), "bond_expd_rdpt_rt"),
        yield_to_maturity=optional_decimal(output.get("bond_expd_asrc_erng_rt"),
                                           "bond_expd_asrc_erng_rt"),
        interest_period_months=optional_int(output.get("int_caltm_mcnt"), "int_caltm_mcnt"),
        _raw=output,
    )
