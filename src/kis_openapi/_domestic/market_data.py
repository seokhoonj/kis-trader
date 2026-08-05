"""국내주식 시세 조회 (내부) -- 현재가/기간별 바/호가창.

사용자면(Ticker)이 이 함수들을 호출해 통합 반환 타입(:class:`Quote`/:class:`Bar`/
:class:`OrderBook`)을 받는다. KIS 원본 필드 매핑과 fail-closed 파싱은 여기 갇힌다.

KIS URL/TR-id:
- 현재가: ``GET .../quotations/inquire-price`` (``FHKST01010100``, 모의 지원).
- 기간별 OHLCV: ``GET .../quotations/inquire-daily-itemchartprice`` (``FHKST03010100``, 모의 지원).
- 호가/예상체결: ``GET .../quotations/inquire-asking-price-exp-ccn`` (``FHKST01010200``, 모의 지원).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .._wire import optional_decimal, optional_int, required_decimal, required_int
from ..after_hours import AfterHoursQuote
from ..bar import Bar, Interval
from ..broker import BrokerActivity, BrokerActivitySummary
from ..errors import KISError, KISUsageError
from ..investor import InvestorActivity, InvestorFlow
from ..order_book import OrderBook, PriceLevel
from ..quote import Quote
from ..trade import Trade
from ..transport import RawResponse, Transport

_KST = timezone(timedelta(hours=9))

#: 시장 보드 -> KIS 조건시장분류코드(FID_COND_MRKT_DIV_CODE).
_MARKET_DIV = {"KRX": "J", "NXT": "NX", "UN": "UN"}

_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
_QUOTE_TR = "FHKST01010100"
#: 전일대비 부호코드(prdy_vrss_sign) 중 하락(4 하한, 5 하락). 나머지는 양(0 포함).
_DOWN_SIGNS = frozenset(("4", "5"))

_BARS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
_BARS_TR = "FHKST03010100"
_PERIOD_BY_INTERVAL = {"1d": "D", "1wk": "W", "1mo": "M"}
#: 날짜창 페이지네이션 안전 상한. 여기 닿으면 부분 결과로 자르지 않고 fail-closed.
_MAX_BAR_PAGES = 200

#: 당일 분봉(1분 고정). KIS는 당일치만 제공하고 한 번에 30건씩 시각을 뒤로 밀며 준다.
_MINUTE_BARS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
_MINUTE_BARS_TR = "FHKST03010200"
_SESSION_OPEN = "090000"          # 정규장 개장(HHMMSS) -- 여기까지 훑으면 종료
#: 조회 시작 기준시각. 미래시각을 주면 KIS가 현재시각으로 처리하므로, 하루 끝(235959)으로 두면
#: 어느 보드(KRX/NXT 연장)든 항상 최신 봉부터 받는다(고정 마감시각은 NXT 연장분을 놓칠 수 있음).
_MINUTE_ANCHOR_START = "235959"
#: 분봉 페이지 상한(30건/page). 여기 닿으면 부분 결과로 자르지 않고 fail-closed.
_MAX_MINUTE_PAGES = 60

_ORDER_BOOK_PATH = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
_ORDER_BOOK_TR = "FHKST01010200"
_DEPTH = 10

_TRADES_PATH = "/uapi/domestic-stock/v1/quotations/inquire-ccnl"
_TRADES_TR = "FHKST01010300"

_INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor"
_INVESTOR_TR = "FHKST01010900"
#: 투자자 주체 -> KIS 필드 접두어(개인/외국인/기관).
_INVESTOR_PREFIX = {"individual": "prsn", "foreign": "frgn", "institutional": "orgn"}

_MEMBER_PATH = "/uapi/domestic-stock/v1/quotations/inquire-member"
_MEMBER_TR = "FHKST01010600"
_BROKER_TOP_N = 5           # KIS 회원사 상위 제공 개수(매도/매수 각각)

_AFTER_HOURS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-overtime-price"
_AFTER_HOURS_TR = "FHPST02300000"


# --- 현재가 ----------------------------------------------------------------
def fetch_quote(transport: Transport, *, symbol: str, market: str) -> Quote:
    """한 종목의 현재가 스냅샷."""
    params = {"FID_COND_MRKT_DIV_CODE": _market_div(market), "FID_INPUT_ISCD": symbol}
    resp = transport.request(
        method="GET", path=_QUOTE_PATH, tr_id=_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output", resp)
    return _parse_quote(output, symbol=symbol, market=market, as_of=datetime.now(_KST))


def _parse_quote(output: Mapping[str, Any], *, symbol: str, market: str, as_of: datetime) -> Quote:
    sign = str(output.get("prdy_vrss_sign", "")).strip()
    return Quote(
        symbol=symbol,
        market=market,
        currency="KRW",
        last=required_decimal(output.get("stck_prpr"), "stck_prpr"),
        open=required_decimal(output.get("stck_oprc"), "stck_oprc"),
        high=required_decimal(output.get("stck_hgpr"), "stck_hgpr"),
        low=required_decimal(output.get("stck_lwpr"), "stck_lwpr"),
        previous_close=required_decimal(output.get("stck_sdpr"), "stck_sdpr"),
        change=_apply_change_sign(required_decimal(output.get("prdy_vrss"), "prdy_vrss"), sign),
        change_percent=_apply_change_sign(required_decimal(output.get("prdy_ctrt"), "prdy_ctrt"), sign),
        volume=required_int(output.get("acml_vol"), "acml_vol"),
        week_52_high=optional_decimal(output.get("w52_hgpr"), "w52_hgpr"),
        week_52_low=optional_decimal(output.get("w52_lwpr"), "w52_lwpr"),
        as_of=as_of,
        _raw=output,
    )


def _apply_change_sign(magnitude: Decimal, sign_code: str) -> Decimal:
    """전일대비 값에 방향 부호를 입힌다(하락 코드면 음수).

    KIS가 크기만 주든(부호 없는) 이미 부호를 실어 주든 상관없이 옳도록, 크기를 ``abs`` 로
    정규화한 뒤 부호코드로만 방향을 정한다(부호가 이중 적용돼 뒤집히는 일 방지).
    """
    size = abs(magnitude)
    return -size if sign_code in _DOWN_SIGNS else size


# --- 기간별 OHLCV 바 -------------------------------------------------------
def fetch_bars(
    transport: Transport,
    *,
    symbol: str,
    market: str,
    interval: Interval = "1d",
    start: str | date | None = None,
    end: str | date | None = None,
    adjusted: bool = True,
    max_bars: int | None = None,
) -> list[Bar]:
    """OHLCV 바를 과거->현재 오름차순으로.

    ``interval="1m"`` 은 **당일** 1분봉이라 ``start``/``end``/``adjusted`` 를 쓰지 않고 최신
    세션을 준다(``max_bars`` 로 최근 N개 제한). ``1d``/``1wk``/``1mo`` 는 [start, end] 구간
    기간봉이며 ``start`` 가 필요하다. 어느 쪽이든 페이지 상한에 닿으면 부분 결과로 자르지 않고 예외."""
    if max_bars is not None and max_bars <= 0:
        raise KISUsageError(f"max_bars 는 양의 정수여야 한다: {max_bars}")
    if interval == "1m":
        return _fetch_minute_bars(transport, symbol=symbol, market=market, max_bars=max_bars)
    if start is None:
        raise KISUsageError(f"interval={interval!r}(기간봉)에는 start 가 필요하다.")
    period = _period_code_for(interval)
    market_div = _market_div(market)
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _to_yyyymmdd(start, "start")
    if start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    adjusted_code = "0" if adjusted else "1"  # KIS 극성: 0=수정주가, 1=원주가
    base_params = {
        "FID_COND_MRKT_DIV_CODE": market_div,
        "FID_INPUT_ISCD": symbol,
        "FID_PERIOD_DIV_CODE": period,
        "FID_ORG_ADJ_PRC": adjusted_code,
    }
    return collect_period_bars(
        transport, path=_BARS_PATH, tr=_BARS_TR, base_params=base_params,
        start_date=start_date, end_date=end_date, max_bars=max_bars,
        parse_rows=lambda rows: _parse_bars(rows, symbol=symbol),
    )


def collect_period_bars(
    transport: Transport,
    *,
    path: str,
    tr: str,
    base_params: dict[str, str],
    start_date: str,
    end_date: str,
    max_bars: int | None,
    parse_rows: Callable[[Sequence[Mapping[str, Any]]], list[Bar]],
) -> list[Bar]:
    """[start, end] 기간봉을 과거->현재 오름차순으로. KIS 페이지 상한을 날짜창을 뒤로 밀며 넘고,
    중복 날짜는 병합, 빈 페이지면 종료, 페이지 상한에 닿으면 부분 결과로 자르지 않고 예외.

    종목/지수 공용 -- ``base_params`` 는 날짜 외 고정 파라미터(시장구분/코드/기간/수정주가 등),
    ``parse_rows`` 는 output2 행을 :class:`Bar` 로 바꾸는 파서(필드명이 종목/지수마다 다르다)."""
    bar_by_date: dict[str, Bar] = {}
    window_end = end_date
    for _page in range(_MAX_BAR_PAGES):
        params = {**base_params, "FID_INPUT_DATE_1": start_date, "FID_INPUT_DATE_2": window_end}
        resp = transport.request(
            method="GET", path=path, tr_id=tr, params=params, idempotent=True
        )
        _raise_if_error(resp)
        rows = resp.body.get("output2")
        if not isinstance(rows, list):  # 성공 응답인데 바 배열 아님 -> fail-closed
            raise _missing_block_error("output2", resp)
        page_by_date = {f"{bar.timestamp:%Y%m%d}": bar for bar in parse_rows(rows)}
        if not page_by_date:
            break
        bar_by_date.update(page_by_date)
        if max_bars is not None and len(bar_by_date) >= max_bars:
            break  # 최근 max_bars 면 충분 -> 더 안 훑음
        oldest_date = min(page_by_date)  # YYYYMMDD 고정폭 -> 문자열 비교 = 시간순
        if oldest_date <= start_date:
            break
        oldest = page_by_date[oldest_date].timestamp
        window_end = f"{oldest - timedelta(days=1):%Y%m%d}"
    else:
        raise KISError(
            f"바 조회가 {_MAX_BAR_PAGES}페이지 상한에 도달했으나 start({start_date})에 못 미쳤다 "
            f"-- 부분 결과로 자르지 않는다. 범위를 좁히거나 재시도하라."
        )

    bars = [
        bar_by_date[key]
        for key in sorted(bar_by_date)
        if start_date <= key <= end_date
    ]
    if max_bars is not None and len(bars) > max_bars:
        bars = bars[-max_bars:]
    return bars


def _period_code_for(interval: str) -> str:
    if interval in _PERIOD_BY_INTERVAL:
        return _PERIOD_BY_INTERVAL[interval]
    raise KISUsageError(f"지원하지 않는 기간봉 interval: {interval!r} (1d/1wk/1mo).")


def _parse_bars(rows: Sequence[Mapping[str, Any]], *, symbol: str) -> list[Bar]:
    bars: list[Bar] = []
    for row in rows:
        date_text = str(row.get("stck_bsop_date", "")).strip()
        close_text = str(row.get("stck_clpr", "")).strip()
        if not date_text or not close_text:  # 미체결 세션의 빈 바 -- 건너뜀
            continue
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(date_text),
                open=required_decimal(row.get("stck_oprc"), "stck_oprc"),
                high=required_decimal(row.get("stck_hgpr"), "stck_hgpr"),
                low=required_decimal(row.get("stck_lwpr"), "stck_lwpr"),
                close=required_decimal(close_text, "stck_clpr"),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                _raw=row,
            )
        )
    return bars


def _parse_bar_timestamp(date_text: str) -> datetime:
    try:
        day = datetime.strptime(date_text, "%Y%m%d")  # noqa: DTZ007 -- 아래 replace 로 KST-aware
    except ValueError as err:
        raise KISError(f"바 날짜(stck_bsop_date) 파싱 실패: {date_text!r}") from err
    return day.replace(tzinfo=_KST)


def _parse_kst_date(date_text: str) -> date:
    """"YYYYMMDD" -> date. 날짜만 필요한 곳(투자자 일자 등)에서 쓴다."""
    try:
        return datetime.strptime(date_text, "%Y%m%d").date()  # noqa: DTZ007 -- date 만 취함
    except ValueError as err:
        raise KISError(f"날짜(YYYYMMDD) 파싱 실패: {date_text!r}") from err


def _fetch_minute_bars(
    transport: Transport, *, symbol: str, market: str, max_bars: int | None
) -> list[Bar]:
    """당일 1분봉을 과거->현재 오름차순으로. 최신(장 마감 기준시각)부터 시각을 뒤로 밀며 30건씩
    모으고, 개장(_SESSION_OPEN)까지 닿거나 새 봉이 없으면 종료. 상한 초과는 fail-closed."""
    market_div = _market_div(market)
    bar_by_time: dict[str, Bar] = {}   # "HHMMSS" -> Bar (고정폭이라 문자열 정렬=시간순)
    anchor = _MINUTE_ANCHOR_START
    for _page in range(_MAX_MINUTE_PAGES):
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": market_div,
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": anchor,
            "FID_PW_DATA_INCU_YN": "N",
        }
        resp = transport.request(
            method="GET", path=_MINUTE_BARS_PATH, tr_id=_MINUTE_BARS_TR, params=params,
            idempotent=True,
        )
        _raise_if_error(resp)
        rows = resp.body.get("output2")
        if not isinstance(rows, list):  # 성공 응답인데 봉 배열 아님 -> fail-closed
            raise _missing_block_error("output2", resp)
        page = {f"{bar.timestamp:%H%M%S}": bar for bar in _parse_minute_bars(rows, symbol=symbol)}
        fresh = {time: bar for time, bar in page.items() if time not in bar_by_time}
        if not fresh:  # 빈 페이지거나 진전 없음 -> 종료(무한 루프 방지)
            break
        bar_by_time.update(fresh)
        if max_bars is not None and len(bar_by_time) >= max_bars:
            break
        oldest = min(page)
        if oldest <= _SESSION_OPEN:  # 개장까지 훑음
            break
        anchor = _subtract_one_minute(oldest)
    else:
        raise KISError(
            f"분봉 조회가 {_MAX_MINUTE_PAGES}페이지 상한에 도달했으나 개장까지 못 미쳤다 "
            f"-- 부분 결과로 자르지 않는다. max_bars 로 범위를 줄이거나 재시도하라."
        )
    bars = [bar_by_time[key] for key in sorted(bar_by_time)]
    if max_bars is not None and len(bars) > max_bars:
        bars = bars[-max_bars:]
    return bars


def _parse_minute_bars(rows: Sequence[Mapping[str, Any]], *, symbol: str) -> list[Bar]:
    """분봉 행 -> Bar. 분봉은 종가가 ``stck_prpr``, 그 분의 거래량이 ``cntg_vol`` 로 일봉과 다르다."""
    bars: list[Bar] = []
    for row in rows:
        date_text = str(row.get("stck_bsop_date", "")).strip()
        time_text = str(row.get("stck_cntg_hour", "")).strip()
        close_text = str(row.get("stck_prpr", "")).strip()
        if not date_text or not time_text or not close_text:  # 빈 봉 skip
            continue
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=_parse_minute_bar_timestamp(date_text, time_text),
                open=required_decimal(row.get("stck_oprc"), "stck_oprc"),
                high=required_decimal(row.get("stck_hgpr"), "stck_hgpr"),
                low=required_decimal(row.get("stck_lwpr"), "stck_lwpr"),
                close=required_decimal(close_text, "stck_prpr"),
                volume=required_int(row.get("cntg_vol"), "cntg_vol"),
                _raw=row,
            )
        )
    return bars


def _parse_minute_bar_timestamp(date_text: str, time_text: str) -> datetime:
    try:
        moment = datetime.strptime(date_text + time_text, "%Y%m%d%H%M%S")  # noqa: DTZ007 -- KST 결합
    except ValueError as err:
        raise KISError(f"분봉 시각 파싱 실패: {date_text!r} {time_text!r}") from err
    return moment.replace(tzinfo=_KST)


def _subtract_one_minute(hhmmss: str) -> str:
    """"HHMMSS" 에서 1분 뺀 "HHMMSS"(다음 페이지의 기준시각). 분 경계/시 경계 넘김 처리."""
    try:
        moment = datetime.strptime(hhmmss, "%H%M%S")  # noqa: DTZ007 -- 날짜 없는 시각 산술용
    except ValueError as err:
        raise KISError(f"분봉 기준시각 파싱 실패: {hhmmss!r}") from err
    return f"{moment - timedelta(minutes=1):%H%M%S}"


# --- 호가창 ----------------------------------------------------------------
def fetch_order_book(transport: Transport, *, symbol: str, market: str) -> OrderBook:
    """한 종목의 10단계 호가창(예상체결 블록은 다루지 않음)."""
    params = {"FID_COND_MRKT_DIV_CODE": _market_div(market), "FID_INPUT_ISCD": symbol}
    resp = transport.request(
        method="GET", path=_ORDER_BOOK_PATH, tr_id=_ORDER_BOOK_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output1 = resp.body.get("output1")
    if not isinstance(output1, Mapping):
        raise _missing_block_error("output1", resp)
    return _parse_order_book(output1, symbol=symbol, market=market, as_of=datetime.now(_KST))


def _parse_order_book(
    output1: Mapping[str, Any], *, symbol: str, market: str, as_of: datetime
) -> OrderBook:
    return OrderBook(
        symbol=symbol,
        market=market,
        bids=_price_levels(output1, "bidp", "bidp_rsqn"),
        asks=_price_levels(output1, "askp", "askp_rsqn"),
        total_bid_quantity=optional_int(output1.get("total_bidp_rsqn"), "total_bidp_rsqn") or 0,
        total_ask_quantity=optional_int(output1.get("total_askp_rsqn"), "total_askp_rsqn") or 0,
        as_of=as_of,
        _raw=output1,
    )


def _price_levels(
    output1: Mapping[str, Any], price_key: str, quantity_key: str
) -> tuple[PriceLevel, ...]:
    """실재 단계만 최우선->차선 순서로. 빈/0 가격은 건너뛰고, 음수 가격은 손상이라 fail-closed."""
    levels: list[PriceLevel] = []
    for step in range(1, _DEPTH + 1):
        price = optional_decimal(output1.get(f"{price_key}{step}"), f"{price_key}{step}")
        if price is None or price == 0:
            continue
        if price < 0:
            raise KISError(f"호가 단계 {price_key}{step} 의 가격이 음수다: {price}")
        quantity = required_int(output1.get(f"{quantity_key}{step}"), f"{quantity_key}{step}")
        levels.append(PriceLevel(price=price, quantity=quantity))
    return tuple(levels)


# --- 체결(time & sales) ----------------------------------------------------
def fetch_trades(transport: Transport, *, symbol: str, market: str) -> list[Trade]:
    """한 종목의 최근 체결 목록(최신순). 시장 구분은 심볼의 보드로 정해진다."""
    params = {"FID_COND_MRKT_DIV_CODE": _market_div(market), "FID_INPUT_ISCD": symbol}
    resp = transport.request(
        method="GET", path=_TRADES_PATH, tr_id=_TRADES_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):  # 성공 응답인데 체결 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_trades(rows, symbol=symbol, as_of=datetime.now(_KST))


def _parse_trades(
    rows: Sequence[Mapping[str, Any]], *, symbol: str, as_of: datetime
) -> list[Trade]:
    trades: list[Trade] = []
    for row in rows:
        time_text = str(row.get("stck_cntg_hour", "")).strip()
        price_text = str(row.get("stck_prpr", "")).strip()
        if not time_text or not price_text:  # 빈 행 건너뜀
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        trades.append(
            Trade(
                symbol=symbol,
                timestamp=_parse_intraday_timestamp(time_text, as_of),
                price=required_decimal(price_text, "stck_prpr"),
                quantity=required_int(row.get("cntg_vol"), "cntg_vol"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                _raw=row,
            )
        )
    return trades


def _parse_intraday_timestamp(time_text: str, as_of: datetime) -> datetime:
    """당일 체결 시각("HHMMSS")을 KST-aware datetime 으로(날짜는 조회일 ``as_of``)."""
    try:
        moment = datetime.strptime(time_text, "%H%M%S").time()  # noqa: DTZ007 -- 아래에서 KST 결합
    except ValueError as err:
        raise KISError(f"체결 시각(stck_cntg_hour) 파싱 실패: {time_text!r}") from err
    return datetime.combine(as_of.date(), moment, tzinfo=_KST)


# --- 투자자 수급 -----------------------------------------------------------
def fetch_investor_flows(transport: Transport, *, symbol: str, market: str) -> list[InvestorFlow]:
    """한 종목의 일자별 투자자(개인/외국인/기관) 매매(최신순)."""
    params = {"FID_COND_MRKT_DIV_CODE": _market_div(market), "FID_INPUT_ISCD": symbol}
    resp = transport.request(
        method="GET", path=_INVESTOR_PATH, tr_id=_INVESTOR_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):  # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    flows: list[InvestorFlow] = []
    for row in rows:
        date_text = str(row.get("stck_bsop_date", "")).strip()
        if not date_text:  # 날짜 없는 빈 행 skip
            continue
        flows.append(
            InvestorFlow(
                symbol=symbol,
                trading_date=_parse_kst_date(date_text),
                close=required_decimal(row.get("stck_clpr"), "stck_clpr"),
                individual=_parse_investor_activity(row, "individual"),
                foreign=_parse_investor_activity(row, "foreign"),
                institutional=_parse_investor_activity(row, "institutional"),
                _raw=row,
            )
        )
    return flows


def _parse_investor_activity(row: Mapping[str, Any], investor: str) -> InvestorActivity:
    prefix = _INVESTOR_PREFIX[investor]
    return InvestorActivity(
        buy_volume=required_int(row.get(f"{prefix}_shnu_vol"), f"{prefix}_shnu_vol"),
        sell_volume=required_int(row.get(f"{prefix}_seln_vol"), f"{prefix}_seln_vol"),
        net_buy_volume=required_int(row.get(f"{prefix}_ntby_qty"), f"{prefix}_ntby_qty"),
        buy_value=required_decimal(row.get(f"{prefix}_shnu_tr_pbmn"), f"{prefix}_shnu_tr_pbmn"),
        sell_value=required_decimal(row.get(f"{prefix}_seln_tr_pbmn"), f"{prefix}_seln_tr_pbmn"),
        net_buy_value=required_decimal(row.get(f"{prefix}_ntby_tr_pbmn"), f"{prefix}_ntby_tr_pbmn"),
    )


# --- 회원사(증권사) 매매 ---------------------------------------------------
def fetch_broker_activity(
    transport: Transport, *, symbol: str, market: str
) -> BrokerActivitySummary:
    """한 종목의 매도/매수 상위 회원사(증권사) 매매 비중."""
    params = {"FID_COND_MRKT_DIV_CODE": _market_div(market), "FID_INPUT_ISCD": symbol}
    resp = transport.request(
        method="GET", path=_MEMBER_PATH, tr_id=_MEMBER_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output", resp)
    return BrokerActivitySummary(
        symbol=symbol,
        sellers=_parse_broker_side(output, "seln"),
        buyers=_parse_broker_side(output, "shnu"),
        _raw=output,
    )


def _parse_broker_side(output: Mapping[str, Any], side: str) -> tuple[BrokerActivity, ...]:
    """한 방향(매도 seln / 매수 shnu)의 상위 회원사. 이름 빈 칸은 미기재라 건너뛴다."""
    brokers: list[BrokerActivity] = []
    for i in range(1, _BROKER_TOP_N + 1):
        name = str(output.get(f"{side}_mbcr_name{i}", "")).strip()
        if not name:  # 채워지지 않은 순위 -- skip
            continue
        brokers.append(
            BrokerActivity(
                member_name=name,
                member_number=str(output.get(f"{side}_mbcr_no{i}", "")).strip(),
                volume_share_percent=required_decimal(
                    output.get(f"{side}_mbcr_rlim{i}"), f"{side}_mbcr_rlim{i}"
                ),
                quantity_change=required_int(
                    output.get(f"{side}_qty_icdc{i}"), f"{side}_qty_icdc{i}"
                ),
                is_foreign=str(output.get(f"{side}_mbcr_glob_yn_{i}", "")).strip().upper() == "Y",
            )
        )
    return tuple(brokers)


# --- 시간외 단일가 ---------------------------------------------------------
def fetch_after_hours_quote(transport: Transport, *, symbol: str, market: str) -> AfterHoursQuote:
    """한 종목의 시간외 단일가 스냅샷(세션 밖이면 대부분 비어 올 수 있음)."""
    params = {"FID_COND_MRKT_DIV_CODE": _market_div(market), "FID_INPUT_ISCD": symbol}
    resp = transport.request(
        method="GET", path=_AFTER_HOURS_PATH, tr_id=_AFTER_HOURS_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output", resp)
    return _parse_after_hours_quote(output, symbol=symbol, as_of=datetime.now(_KST))


def _parse_after_hours_quote(
    output: Mapping[str, Any], *, symbol: str, as_of: datetime
) -> AfterHoursQuote:
    change_size = optional_decimal(
        output.get("ovtm_untp_antc_cntg_vrss"), "ovtm_untp_antc_cntg_vrss"
    )
    change_rate = optional_decimal(
        output.get("ovtm_untp_antc_cntg_ctrt"), "ovtm_untp_antc_cntg_ctrt"
    )
    sign = str(output.get("ovtm_untp_antc_cntg_vrss_sign", "")).strip()
    return AfterHoursQuote(
        symbol=symbol,
        bid=optional_decimal(output.get("bidp"), "bidp"),
        ask=optional_decimal(output.get("askp"), "askp"),
        expected_price=optional_decimal(output.get("ovtm_untp_antc_cnpr"), "ovtm_untp_antc_cnpr"),
        expected_quantity=optional_int(output.get("ovtm_untp_antc_cnqn"), "ovtm_untp_antc_cnqn"),
        change=None if change_size is None else _apply_change_sign(change_size, sign),
        change_percent=None if change_rate is None else _apply_change_sign(change_rate, sign),
        as_of=as_of,
        _raw=output,
    )


# --- 공용 ------------------------------------------------------------------
def _market_div(market: str) -> str:
    try:
        return _MARKET_DIV[market]
    except KeyError:
        raise KISUsageError(f"지원하지 않는 국내 시장 보드: {market!r} (KRX/NXT/UN).") from None


def _to_yyyymmdd(value: str | date, name: str) -> str:
    if isinstance(value, date):  # datetime 도 date 하위형
        return f"{value:%Y%m%d}"
    digits = str(value).strip().replace("-", "")
    if len(digits) == 8 and digits.isdigit():
        return digits
    raise KISUsageError(f"{name} 는 date 또는 YYYYMMDD/YYYY-MM-DD 문자열이어야 한다: {value!r}")


def _today_kst() -> str:
    return f"{datetime.now(_KST):%Y%m%d}"


def _missing_block_error(block: str, resp: RawResponse) -> KISError:
    return KISError(
        f"시세 응답에 {block} 블록이 없다.",
        rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
    )


def _raise_if_error(resp: RawResponse) -> None:
    if not resp.ok:
        raise KISError(
            f"시세 조회 실패: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
