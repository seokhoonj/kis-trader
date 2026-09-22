"""해외 선물/옵션 시세 조회 (내부) -- 계약 현재가를 :class:`OverseasDerivativeQuote` 로.

사용자면은 해외 파생 핸들(:class:`~kis_trader.overseas.derivative.OverseasDerivative`,
``kis.overseas.futures(series_code)`` / ``kis.overseas.option(series_code)``)이다. 계약은 시리즈코드(``series_code``)
하나로 식별한다. 선물/옵션은 URL/TR 만 다르고 출력 구조는 같아 한 파서를 공유한다(KIS 명세 대조).

KIS URL/TR-ID:
- 선물 현재가: ``GET .../overseas-futureoption/v1/quotations/inquire-price`` ``HHDFC55010000``.
- 옵션 현재가: ``GET .../overseas-futureoption/v1/quotations/opt-price`` ``HHDFO55010000``.
  (둘 다 파라미터 ``SRS_CD``, 응답은 ``output1`` 단일 객체.)
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any, Literal

from ..._bars import _parse_bar_timestamp
from ..._internal._datetime import (
    _KST,
    _parse_kst_date,
    _to_yyyymmdd,
    _today_kst,
)
from ..._internal._response import (
    _CONTINUATION_END,
    _missing_block_error,
    _raise_if_error,
    _require_mapping_rows,
)
from ..._internal._wire import (
    _apply_change_sign,
    optional_decimal,
    optional_int,
    required_decimal,
    required_int,
)
from ..._literals import DerivativeProduct
from ...bar import Bar, Interval
from ...errors import KISError, KISUsageError
from ...order_book import OrderBook, PriceLevel
from ...trade import Trade
from ...transport import Environment, RawResponse, Transport
from ..entities.derivative import (
    OverseasDerivativeDetail,
    OverseasDerivativeMarketHours,
    OverseasDerivativeQuote,
    OverseasFuturesOpenInterest,
)

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

_BARS = {
    "future": {
        "1m": ("inquire-time-futurechartprice", "HHDFC55020400", 120),
        "1d": ("daily-ccnl", "HHDFC55020100", 40),
        "1wk": ("weekly-ccnl", "HHDFC55020000", 40),
        "1mo": ("monthly-ccnl", "HHDFC55020300", 40),
    },
    "option": {
        "1m": ("inquire-time-optchartprice", "HHDFO55020400", 120),
        "1d": ("opt-daily-ccnl", "HHDFO55020100", 120),
        "1wk": ("opt-weekly-ccnl", "HHDFO55020000", 120),
        "1mo": ("opt-monthly-ccnl", "HHDFO55020300", 120),
    },
}

_TRADES = {
    "future": ("tick-ccnl", "HHDFC55020200"),
    "option": ("opt-tick-ccnl", "HHDFO55020200"),
}
_QUOTATIONS_BASE = "/uapi/overseas-futureoption/v1/quotations"
_OPEN_INTEREST_PATH = f"{_QUOTATIONS_BASE}/investor-unpd-trend"
_OPEN_INTEREST_TR = "HHDDB95030000"


def fetch_quote(
    transport: Transport, *, srs_cd: str, market: DerivativeProduct
) -> OverseasDerivativeQuote:
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
        current_price=required_decimal(output.get("last_price"), "last_price"),
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


def fetch_order_book(
    transport: Transport, *, srs_cd: str, market: DerivativeProduct
) -> OrderBook:
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
    rows = _require_mapping_rows("output2", resp)

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


def fetch_bars(
    transport: Transport,
    *,
    srs_cd: str,
    market: DerivativeProduct,
    exchange: str,
    interval: Interval,
    max_bars: int,
    environment: Environment,
) -> list[Bar]:
    """해외 선물/옵션의 최근 분·일·주·월 OHLCV 한 페이지."""
    if environment == "paper":
        raise KISUsageError("해외 선물/옵션 시계열 조회는 모의투자 미지원이다(실전만).")
    try:
        endpoint, tr, limit = _BARS[market][interval]
    except KeyError:
        raise KISUsageError(f"지원하지 않는 해외 파생 interval: {interval!r}") from None
    if not exchange.strip():
        raise KISUsageError("해외 선물/옵션 시계열에는 exchange 가 필요하다.")
    if max_bars <= 0 or max_bars > limit:
        raise KISUsageError(f"interval={interval!r} max_bars 는 1..{limit}: {max_bars}")
    query_count = (
        max(1, max_bars - 1) if market == "option" and interval == "1d" else max_bars
    )
    params = _history_params(
        srs_cd=srs_cd, exchange=exchange, count=query_count,
        gap="1" if interval == "1m" else "",
        close_date=_today_kst() if market == "future" else "",
    )
    resp = transport.request(
        method="GET", path=f"{_QUOTATIONS_BASE}/{endpoint}", tr_id=tr,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output1" if interval == "1m" else "output2")
    if not isinstance(rows, list):
        raise _missing_block_error("output1" if interval == "1m" else "output2", resp)
    bars = _parse_history_bars(rows, symbol=srs_cd, intraday=interval == "1m", resp=resp)
    return bars[-max_bars:]


def fetch_trades(
    transport: Transport,
    *,
    srs_cd: str,
    market: DerivativeProduct,
    exchange: str,
    max_trades: int,
    environment: Environment,
) -> list[Trade]:
    """해외 선물/옵션 최근 틱 체결(최대 40건, 시간 오름차순)."""
    if environment == "paper":
        raise KISUsageError("해외 선물/옵션 틱 조회는 모의투자 미지원이다(실전만).")
    if not exchange.strip():
        raise KISUsageError("해외 선물/옵션 틱 조회에는 exchange 가 필요하다.")
    if max_trades <= 0 or max_trades > 40:
        raise KISUsageError(f"max_trades 는 1..40: {max_trades}")
    endpoint, tr = _TRADES[market]
    params = _history_params(
        srs_cd=srs_cd, exchange=exchange, count=max_trades, gap="",
        close_date=_today_kst() if market == "future" else "",
    )
    resp = transport.request(
        method="GET", path=f"{_QUOTATIONS_BASE}/{endpoint}", tr_id=tr,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output2", resp)
    trades: list[Trade] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output2[]", resp)
        date_text = str(row.get("data_date", "")).strip()
        time_text = str(row.get("data_time", "")).strip()
        price_text = str(row.get("last_price", "")).strip()
        if not date_text or not time_text or not price_text:
            continue
        sign = str(row.get("prev_diff_flag", "")).strip()
        trades.append(
            Trade(
                symbol=srs_cd,
                timestamp=_parse_history_timestamp(date_text, time_text),
                price=required_decimal(price_text, "last_price"),
                quantity=required_int(row.get("last_qntt"), "last_qntt"),
                change=_apply_change_sign(
                    required_decimal(row.get("prev_diff_price"), "prev_diff_price"), sign
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prev_diff_rate"), "prev_diff_rate"), sign
                ),
                _raw=row,
            )
        )
    trades.sort(key=lambda trade: trade.timestamp)
    return trades[-max_trades:]


def _history_params(
    *, srs_cd: str, exchange: str, count: int, gap: str, close_date: str
) -> dict[str, str]:
    return {
        "SRS_CD": srs_cd,
        "EXCH_CD": exchange.strip(),
        "START_DATE_TIME": "",
        "CLOSE_DATE_TIME": close_date,
        "QRY_TP": "Q",
        "QRY_CNT": str(count),
        "QRY_GAP": gap,
        "INDEX_KEY": "",
    }


def _parse_history_bars(
    rows: list[object], *, symbol: str, intraday: bool, resp: RawResponse
) -> list[Bar]:
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("history output[]", resp)
        date_text = str(row.get("data_date", "")).strip()
        close_text = str(row.get("last_price", "")).strip()
        if not date_text or not close_text:
            continue
        time_text = str(row.get("data_time", "")).strip()
        timestamp = (
            _parse_history_timestamp(date_text, time_text)
            if intraday
            else _parse_bar_timestamp(date_text)
        )
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=timestamp,
                open=required_decimal(row.get("open_price"), "open_price"),
                high=required_decimal(row.get("high_price"), "high_price"),
                low=required_decimal(row.get("low_price"), "low_price"),
                close=required_decimal(close_text, "last_price"),
                volume=required_int(row.get("vol"), "vol"),
                _raw=row,
            )
        )
    bars.sort(key=lambda bar: bar.timestamp)
    return bars


def _parse_history_timestamp(date_text: str, time_text: str) -> datetime:
    try:
        timestamp = datetime.strptime(  # noqa: DTZ007 -- 아래에서 KST-aware 로 변환
            date_text + time_text, "%Y%m%d%H%M%S"
        )
    except ValueError as err:
        raise KISError(
            f"해외 선물/옵션 시계열 일시 파싱 실패: {date_text!r} {time_text!r}"
        ) from err
    return timestamp.replace(tzinfo=_KST)


def fetch_open_interest(
    transport: Transport,
    *,
    product: str,
    as_of: str | date,
    mode: Literal["quantity", "change"],
    environment: Environment,
) -> list[OverseasFuturesOpenInterest]:
    """해외선물 상품의 CFTC 미결제약정 수량 또는 증감 추이."""
    if environment == "paper":
        raise KISUsageError("해외선물 미결제추이는 모의투자 미지원이다(실전만).")
    if not product.strip():
        raise KISUsageError("해외선물 미결제추이에는 product 가 필요하다.")
    mode_code = {"quantity": "0", "change": "1"}.get(mode)
    if mode_code is None:
        raise KISUsageError(f"mode 는 'quantity' 또는 'change': {mode!r}")
    params = {
        "PROD_ISCD": product.strip(),
        "BSOP_DATE": _to_yyyymmdd(as_of, "as_of"),
        "UPMU_GUBUN": mode_code,
        "CTS_KEY": "",
    }
    resp = transport.request(
        method="GET", path=_OPEN_INTEREST_PATH, tr_id=_OPEN_INTEREST_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output2", resp)
    points: list[OverseasFuturesOpenInterest] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output2[]", resp)
        points.append(
            OverseasFuturesOpenInterest(
                product=str(row.get("prod_iscd", "")).strip() or product.strip(),
                cftc_code=str(row.get("cftc_iscd", "")).strip(),
                date=_parse_kst_date(str(row.get("bsop_date", "")).strip()),
                speculative_long=required_int(row.get("bidp_spec"), "bidp_spec"),
                speculative_short=required_int(row.get("askp_spec"), "askp_spec"),
                speculative_spread=required_int(row.get("spread_spec"), "spread_spec"),
                hedging_long=required_int(row.get("bidp_hedge"), "bidp_hedge"),
                hedging_short=required_int(row.get("askp_hedge"), "askp_hedge"),
                total_open_interest=required_int(row.get("hts_otst_smtn"), "hts_otst_smtn"),
                unclassified_long=required_int(row.get("bidp_missing"), "bidp_missing"),
                unclassified_short=required_int(row.get("askp_missing"), "askp_missing"),
                customer_speculative_long=required_int(
                    row.get("bidp_spec_cust"), "bidp_spec_cust"
                ),
                customer_speculative_short=required_int(
                    row.get("askp_spec_cust"), "askp_spec_cust"
                ),
                customer_speculative_spread=required_int(
                    row.get("spread_spec_cust"), "spread_spec_cust"
                ),
                customer_hedging_long=required_int(
                    row.get("bidp_hedge_cust"), "bidp_hedge_cust"
                ),
                customer_hedging_short=required_int(
                    row.get("askp_hedge_cust"), "askp_hedge_cust"
                ),
                customer_total=required_int(row.get("cust_smtn"), "cust_smtn"),
                _raw=row,
            )
        )
    points.sort(key=lambda point: point.date)
    return points


_DETAIL = {
    "future": ("/uapi/overseas-futureoption/v1/quotations/stock-detail", "HHDFC55010100"),
    "option": ("/uapi/overseas-futureoption/v1/quotations/opt-detail", "HHDFO55010100"),
}

_BATCH_DETAIL = {
    "future": (
        "/uapi/overseas-futureoption/v1/quotations/search-contract-detail",
        "HHDFC55200000",
        32,
    ),
    "option": (
        "/uapi/overseas-futureoption/v1/quotations/search-opt-detail",
        "HHDFO55200000",
        30,
    ),
}


def fetch_detail(
    transport: Transport, *, srs_cd: str, market: DerivativeProduct
) -> OverseasDerivativeDetail:
    """해외 선물/옵션 계약 명세. ``market`` 은 ``"future"``/``"option"``, ``srs_cd`` 는 시리즈코드."""
    path, tr = _DETAIL[market]
    resp = transport.request(
        method="GET", path=path, tr_id=tr, params={"SRS_CD": srs_cd}, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output1")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output1", resp)
    return _parse_detail(output, srs_cd=srs_cd)


def fetch_details(
    transport: Transport, *, srs_codes: list[str], market: DerivativeProduct,
    environment: Environment
) -> list[OverseasDerivativeDetail]:
    """해외 선물/옵션 계약 명세를 한 번에 조회한다(선물 32개, 옵션 30개 한도)."""
    if environment == "paper":
        raise KISUsageError("해외 선물/옵션 상품기본정보 조회는 모의투자 미지원이다(실전만).")
    path, tr, limit = _BATCH_DETAIL[market]
    if not srs_codes:
        raise KISUsageError("조회할 해외 선물/옵션 시리즈코드가 하나 이상 필요하다.")
    if len(srs_codes) > limit:
        raise KISUsageError(f"해외 {market} 상품기본정보는 최대 {limit}개: {len(srs_codes)}")
    if any(not isinstance(code, str) or not code.strip() for code in srs_codes):
        raise KISUsageError("해외 선물/옵션 시리즈코드는 비어 있지 않은 문자열이어야 한다.")
    normalized_codes = [code.strip() for code in srs_codes]
    params = {"QRY_CNT": str(len(normalized_codes))}
    params.update(
        {f"SRS_CD_{position:02d}": code for position, code in enumerate(normalized_codes, 1)}
    )
    resp = transport.request(
        method="GET", path=path, tr_id=tr, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(rows, list):
        raise _missing_block_error("output2", resp)
    if len(rows) != len(normalized_codes) or not all(isinstance(row, Mapping) for row in rows):
        raise KISError(
            "해외 선물/옵션 상품기본정보 응답이 요청 계약 수·순서와 일치하지 않는다.",
            raw=resp.body,
        )
    return [
        _parse_detail(row, srs_cd=code)
        for code, row in zip(normalized_codes, rows, strict=True)
    ]


def _parse_detail(
    output: Mapping[str, Any], *, srs_cd: str
) -> OverseasDerivativeDetail:
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


_MARKET_HOURS_PATH = "/uapi/overseas-futureoption/v1/quotations/market-time"
_MARKET_HOURS_TR = "OTFM2229R"
#: 장운영시간 CTX_AREA 연속조회 페이지 상한. 도달하면 부분 결과로 자르지 않고 fail-closed.
_MAX_MARKET_HOURS_PAGES = 100


def _parse_hhmmss(value: object) -> time | None:
    text = str(value or "").strip()
    if len(text) != 6 or not text.isdigit():
        return None
    try:
        return time(int(text[:2]), int(text[2:4]), int(text[4:]))
    except ValueError:
        return None


def fetch_market_hours(
    transport: Transport,
    *,
    environment: Environment,
    product_group: str = "",
    asset_class: str = "",
    exchange: str = "",
    kind: str = "%",
) -> list[OverseasDerivativeMarketHours]:
    """해외 선물/옵션 상품군별 장운영시간 전체를 조회한다.

    계약 시리즈코드와 무관한 시장 전체 일정이며 상품군·클래스·거래소·선물옵션 구분으로 필터한다.

    KIS ``GET /uapi/overseas-futureoption/v1/quotations/market-time``
    (``OTFM2229R``)를 사용하며 모의투자는 지원하지 않는다.

    이 TR은 ``tr_cont`` 미지원이므로 ``CTX_AREA`` 커서만으로 연속조회한다.

    성공 응답의 ``output`` 배열이 없거나 페이지 상한 뒤에도 커서가 남으면 부분 결과 대신 실패한다.
    """
    if environment == "paper":
        raise KISUsageError("해외 선물/옵션 장운영시간 조회는 모의투자 미지원이다(실전만).")

    rows: list[Mapping[str, Any]] = []
    ctx_fk, ctx_nk = "", ""
    for _page in range(_MAX_MARKET_HOURS_PAGES):
        params = {
            "FM_PDGR_CD": product_group,
            "FM_CLAS_CD": asset_class,
            "FM_EXCG_CD": exchange,
            "OPT_YN": kind,
            "CTX_AREA_NK200": ctx_nk,
            "CTX_AREA_FK200": ctx_fk,
        }
        resp = transport.request(
            method="GET",
            path=_MARKET_HOURS_PATH,
            tr_id=_MARKET_HOURS_TR,
            params=params,
            idempotent=True,
        )
        _raise_if_error(resp)
        page = resp.body.get("output")
        if not isinstance(page, list):
            raise _missing_block_error("output", resp)
        if not all(isinstance(row, Mapping) for row in page):
            raise KISError("장운영시간 응답의 output 항목이 객체가 아니다.", raw=resp.body)
        rows.extend(page)
        prev_nk = ctx_nk
        ctx_nk = str(resp.body.get("ctx_area_nk200") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk200") or "").strip()
        # 비진전 커서(빈 키/직전과 같은 키 반복/종료 센티널) -> 재요청 중단(이중집계/무한 재요청 방지)
        if not ctx_nk or ctx_nk == prev_nk or ctx_nk == _CONTINUATION_END:
            break
    else:
        raise KISError(
            f"해외 선물/옵션 장운영시간 조회가 {_MAX_MARKET_HOURS_PAGES}페이지 상한에 "
            "도달했으나 연속조회가 남아있다 -- 부분 결과로 자르지 않는다. 재시도하거나 "
            "수동 확인하라."
        )
    return [_parse_market_hours(row) for row in rows]


def _parse_market_hours(row: Mapping[str, Any]) -> OverseasDerivativeMarketHours:
    return OverseasDerivativeMarketHours(
        product_group_code=str(row.get("fm_pdgr_cd", "")).strip(),
        product_group_name=str(row.get("fm_pdgr_name", "")).strip(),
        exchange_code=str(row.get("fm_excg_cd", "")).strip(),
        exchange_name=str(row.get("fm_excg_name", "")).strip(),
        kind=str(row.get("fuop_dvsn_name", "")).strip(),
        class_code=str(row.get("fm_clas_cd", "")).strip(),
        class_name=str(row.get("fm_clas_name", "")).strip(),
        am_open=_parse_hhmmss(row.get("am_mkmn_strt_tmd")),
        am_close=_parse_hhmmss(row.get("am_mkmn_end_tmd")),
        pm_open=_parse_hhmmss(row.get("pm_mkmn_strt_tmd")),
        pm_close=_parse_hhmmss(row.get("pm_mkmn_end_tmd")),
        next_day_open=_parse_hhmmss(row.get("mkmn_nxdy_strt_tmd")),
        next_day_close=_parse_hhmmss(row.get("mkmn_nxdy_end_tmd")),
        base_open=_parse_hhmmss(row.get("base_mket_strt_tmd")),
        base_close=_parse_hhmmss(row.get("base_mket_end_tmd")),
        _raw=row,
    )
