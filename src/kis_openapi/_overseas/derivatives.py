"""해외 선물/옵션 시세 조회 (내부) -- 계약 현재가를 :class:`OverseasDerivativeQuote` 로.

사용자면은 해외 파생 핸들(:class:`~kis_openapi.overseas_derivative.OverseasDerivative`,
``kis.overseas_futures(srs_cd)`` / ``kis.overseas_option(srs_cd)``)이다. 계약은 시리즈코드(``srs_cd``)
하나로 식별한다. 선물/옵션은 URL/TR 만 다르고 출력 구조는 같아 한 파서를 공유한다(원장 대조).

KIS URL/TR-id:
- 선물 현재가: ``GET .../overseas-futureoption/v1/quotations/inquire-price`` ``HHDFC55010000``.
- 옵션 현재가: ``GET .../overseas-futureoption/v1/quotations/opt-price`` ``HHDFO55010000``.
  (둘 다 파라미터 ``SRS_CD``, 응답은 ``output1`` 단일 객체.)
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .._domestic.market_data import (
    _KST,
    _apply_change_sign,
    _missing_block_error,
    _parse_bar_timestamp,
    _raise_if_error,
)
from .._wire import optional_decimal, optional_int, required_decimal, required_int
from ..errors import KISError
from ..order_book import OrderBook, PriceLevel
from ..overseas_derivative_items import (
    OverseasDerivativeDetail,
    OverseasDerivativeQuote,
)
from ..transport import Transport

_QUOTE = {
    "future": ("/uapi/overseas-futureoption/v1/quotations/inquire-price", "HHDFC55010000"),
    "option": ("/uapi/overseas-futureoption/v1/quotations/opt-price", "HHDFO55010000"),
}

_ORDER_BOOK = {
    "future": (
        "/uapi/overseas-futureoption/v1/quotations/inquire-asking-price",
        "HHDFC86000000",
    ),
    "option": (
        "/uapi/overseas-futureoption/v1/quotations/opt-asking-price",
        "HHDFO86000000",
    ),
}


def fetch_quote(transport: Transport, *, srs_cd: str, market: str) -> OverseasDerivativeQuote:
    """해외 선물/옵션 계약 현재가. ``market`` 은 ``"future"``/``"option"``, ``srs_cd`` 는 시리즈코드."""
    path, tr = _QUOTE[market]
    resp = transport.request(
        method="GET", path=path, tr_id=tr, params={"SRS_CD": srs_cd}, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output1")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output1", resp)
    return _parse_quote(output, srs_cd=srs_cd, as_of=datetime.now(_KST))


def _optional_date(value: object) -> datetime | None:
    text = str(value or "").strip()
    return _parse_bar_timestamp(text) if text else None


def _parse_quote(
    output: Mapping[str, Any], *, srs_cd: str, as_of: datetime
) -> OverseasDerivativeQuote:
    sign = str(output.get("prev_diff_flag", "")).strip()
    return OverseasDerivativeQuote(
        symbol=srs_cd,
        last=required_decimal(output.get("last_price"), "last_price"),
        open=required_decimal(output.get("open_price"), "open_price"),
        high=required_decimal(output.get("high_price"), "high_price"),
        low=required_decimal(output.get("low_price"), "low_price"),
        previous_close=required_decimal(output.get("prev_price"), "prev_price"),
        settlement_price=optional_decimal(output.get("sttl_price"), "sttl_price"),
        change=_apply_change_sign(
            required_decimal(output.get("prev_diff_price"), "prev_diff_price"), sign
        ),
        change_percent=_apply_change_sign(
            required_decimal(output.get("prev_diff_rate"), "prev_diff_rate"), sign
        ),
        volume=required_int(output.get("vol"), "vol"),
        bid=optional_decimal(output.get("bid_price"), "bid_price"),
        ask=optional_decimal(output.get("ask_price"), "ask_price"),
        bid_size=optional_int(output.get("bid_qntt"), "bid_qntt"),
        ask_size=optional_int(output.get("ask_qntt"), "ask_qntt"),
        total_bid_quantity=optional_int(output.get("tot_bid_qntt"), "tot_bid_qntt"),
        total_ask_quantity=optional_int(output.get("tot_ask_qntt"), "tot_ask_qntt"),
        currency=str(output.get("crc_cd", "")).strip(),
        exchange=str(output.get("exch_cd", "")).strip(),
        expiry_date=_optional_date(output.get("expr_date")),
        last_trade_date=_optional_date(output.get("trd_to_date")),
        remaining_days=optional_int(output.get("remn_cnt"), "remn_cnt"),
        tick_size=optional_decimal(output.get("tick_size"), "tick_size"),
        margin=optional_decimal(output.get("trst_mgn"), "trst_mgn"),
        as_of=as_of,
        _raw=output,
    )


def _level(
    row: Mapping[str, Any], price_key: str, quantity_key: str
) -> PriceLevel | None:
    """배열의 호가 한쪽을 읽는다. 빈/0 가격은 건너뛰고 손상된 가격·수량은 fail-closed."""
    price = optional_decimal(row.get(price_key), price_key)
    if price is None or price == 0:
        return None
    if price < 0:
        raise KISError(f"호가 단계 {price_key} 의 가격이 음수다: {price}")
    return PriceLevel(
        price=price,
        quantity=required_int(row.get(quantity_key), quantity_key),
    )


def fetch_order_book(transport: Transport, *, srs_cd: str, market: str) -> OrderBook:
    """해외 선물/옵션 계약의 호가창(5단계 매수/매도 심도).

    KIS 선물 ``GET .../overseas-futureoption/v1/quotations/inquire-asking-price``
    (``HHDFC86000000``), 옵션 ``GET .../quotations/opt-asking-price``
    (``HHDFO86000000``)를 조회한다. 호가 사다리는 ``output2``(배열, 5단계)이며 각 행의
    ``bid_price``/``bid_qntt`` 와 ``ask_price``/``ask_qntt`` 를 최우선부터 읽는다.

    KIS가 해외 파생 호가 전체 잔량을 주지 않으므로 총잔량은 반환된 유효 단계 잔량의 합이다.
    """
    path, tr = _ORDER_BOOK[market]
    resp = transport.request(
        method="GET", path=path, tr_id=tr, params={"SRS_CD": srs_cd}, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(rows, list):
        raise _missing_block_error("output2", resp)

    bids: list[PriceLevel] = []
    asks: list[PriceLevel] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise KISError("시세 응답의 output2 호가 단계가 객체가 아니다.", raw=resp.body)
        bid = _level(row, "bid_price", "bid_qntt")
        ask = _level(row, "ask_price", "ask_qntt")
        if bid is not None:
            bids.append(bid)
        if ask is not None:
            asks.append(ask)

    return OrderBook(
        symbol=srs_cd,
        market=market,
        bids=tuple(bids),
        asks=tuple(asks),
        total_bid_quantity=sum(level.quantity for level in bids),
        total_ask_quantity=sum(level.quantity for level in asks),
        as_of=datetime.now(_KST),
        _raw=resp.body,
    )


_DETAIL = {
    "future": ("/uapi/overseas-futureoption/v1/quotations/stock-detail", "HHDFC55010100"),
    "option": ("/uapi/overseas-futureoption/v1/quotations/opt-detail", "HHDFO55010100"),
}


def fetch_detail(transport: Transport, *, srs_cd: str, market: str) -> OverseasDerivativeDetail:
    """해외 선물/옵션 계약 명세. ``market`` 은 ``"future"``/``"option"``, ``srs_cd`` 는 시리즈코드."""
    path, tr = _DETAIL[market]
    resp = transport.request(
        method="GET", path=path, tr_id=tr, params={"SRS_CD": srs_cd}, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output1")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output1", resp)
    return OverseasDerivativeDetail(
        symbol=srs_cd,
        exchange=str(output.get("exch_cd", "")).strip(),
        currency=str(output.get("crc_cd", "")).strip(),
        product_class=str(output.get("clas_cd", "")).strip(),
        tick_size=optional_decimal(output.get("tick_sz"), "tick_sz"),
        tick_value=optional_decimal(output.get("tick_val"), "tick_val"),
        contract_size=optional_decimal(output.get("ctrt_size"), "ctrt_size"),
        margin=optional_decimal(output.get("trst_mgn"), "trst_mgn"),
        price_digits=optional_int(output.get("disp_digit"), "disp_digit"),
        listing_date=_optional_date(output.get("trd_fr_date")),
        expiry_date=_optional_date(output.get("expr_date")),
        last_trade_date=_optional_date(output.get("trd_to_date")),
        remaining_days=optional_int(output.get("remn_cnt"), "remn_cnt"),
        settlement_type=str(output.get("stl_tp", "")).strip(),
        tradable=str(output.get("stat_tp", "")).strip(),
        _raw=output,
    )
