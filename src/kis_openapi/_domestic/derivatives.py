"""국내 선물/옵션(파생) 시세 조회 (내부) -- 계약 현재가를 :class:`DerivativesQuote` 로.

사용자면은 파생 핸들(:class:`~kis_openapi.derivative.Derivative`, ``kis.futures(code)`` /
``kis.option(code)``)이다. 파생은 종목이 아니라 시장구분(F:지수선물 / O:지수옵션) + 계약코드로
조회한다. 스캘핑에 필요한 미결제약정·베이시스·이론가는 output1 에서 매핑한다.

KIS URL/TR-id (원장 대조):
- 선물옵션 현재가: ``GET .../domestic-futureoption/v1/quotations/inquire-price`` ``FHMIF10000000``.
- 선물옵션 호가: ``GET .../domestic-futureoption/v1/quotations/inquire-asking-price`` ``FHMIF10010000``
  (호가 사다리는 ``output2``).
- 선물옵션 기간봉: ``GET .../domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice``
  ``FHKIF03020100`` (``FID_PERIOD_DIV_CODE`` D/W/M + ``FID_INPUT_DATE_1/2`` 날짜창, 캔들은 ``output2``).
  (모두 ``FID_COND_MRKT_DIV_CODE`` F:지수선물 / O:지수옵션 + ``FID_INPUT_ISCD=계약코드``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from .._wire import optional_decimal, optional_int, required_decimal, required_int
from ..bar import Bar, Interval
from ..derivative_items import DerivativesQuote, UnderlyingQuote
from ..errors import KISError, KISUsageError
from ..order_book import OrderBook
from ..transport import Transport
from .market_data import (
    _KST,
    _MAX_MINUTE_PAGES,
    _apply_change_sign,
    _missing_block_error,
    _parse_bar_timestamp,
    _parse_minute_bar_timestamp,
    _period_code_for,
    _price_levels,
    _raise_if_error,
    _subtract_one_minute,
    _to_yyyymmdd,
    _today_kst,
    collect_period_bars,
)

_QUOTE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
_QUOTE_TR = "FHMIF10000000"
_ORDER_BOOK_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-asking-price"
_ORDER_BOOK_TR = "FHMIF10010000"
_BARS_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice"
_BARS_TR = "FHKIF03020100"
#: 선물옵션 분봉. 한 번에 최대 102건, FID_INPUT_DATE_1+FID_INPUT_HOUR_1 로 다음조회. 당일치만
#: (FID_PW_DATA_INCU_YN=N) 모으고, 파생은 야간장 등 세션 경계가 다양해 개장시각 가정 대신 새 봉이
#: 없으면 종료한다. FID_HOUR_CLS_CODE 60 = 1분.
_MINUTE_BARS_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
_MINUTE_BARS_TR = "FHKIF03020200"
_MINUTE_ANCHOR_START = "235959"


def fetch_quote(transport: Transport, *, code: str, market: str) -> DerivativesQuote:
    """선물/옵션 계약 현재가 스냅샷. ``market`` 은 F(지수선물)/O(지수옵션), ``code`` 는 계약코드."""
    params = {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_QUOTE_PATH, tr_id=_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output1")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output1", resp)
    return _parse_quote(output, code=code, as_of=datetime.now(_KST))


def _parse_quote(output: Mapping[str, Any], *, code: str, as_of: datetime) -> DerivativesQuote:
    sign = str(output.get("prdy_vrss_sign", "")).strip()
    return DerivativesQuote(
        code=code,
        name=str(output.get("hts_kor_isnm", "")).strip(),
        last=required_decimal(output.get("futs_prpr"), "futs_prpr"),
        open=required_decimal(output.get("futs_oprc"), "futs_oprc"),
        high=required_decimal(output.get("futs_hgpr"), "futs_hgpr"),
        low=required_decimal(output.get("futs_lwpr"), "futs_lwpr"),
        previous_close=required_decimal(output.get("futs_prdy_clpr"), "futs_prdy_clpr"),
        change=_apply_change_sign(
            required_decimal(output.get("futs_prdy_vrss"), "futs_prdy_vrss"), sign
        ),
        change_percent=_apply_change_sign(
            required_decimal(output.get("futs_prdy_ctrt"), "futs_prdy_ctrt"), sign
        ),
        volume=required_int(output.get("acml_vol"), "acml_vol"),
        open_interest=required_int(output.get("hts_otst_stpl_qty"), "hts_otst_stpl_qty"),
        theoretical_price=optional_decimal(output.get("hts_thpr"), "hts_thpr"),
        basis=optional_decimal(output.get("basis"), "basis"),
        premium=optional_decimal(output.get("dprt"), "dprt"),
        as_of=as_of,
        _raw=output,
    )


def fetch_order_book(transport: Transport, *, code: str, market: str) -> OrderBook:
    """선물/옵션 계약의 호가창(5단계 매수/매도 심도). ``market`` 은 F/O, ``code`` 는 계약코드.

    종목 :meth:`~kis_openapi.ticker.Ticker.order_book` 과 같은 :class:`OrderBook` 로 돌려주되,
    파생 호가는 5단계(주식 10단계)다. 호가 사다리는 응답의 **output2** 에 있고(output1 은 현재가
    요약), 가격 키는 ``futs_askp``/``futs_bidp``, 잔량 키는 ``askp_rsqn``/``bidp_rsqn`` 다.
    """
    params = {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_ORDER_BOOK_PATH, tr_id=_ORDER_BOOK_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output2 = resp.body.get("output2")
    if not isinstance(output2, Mapping):       # 성공 응답인데 호가 객체 아님 -> fail-closed
        raise _missing_block_error("output2", resp)
    return OrderBook(
        symbol=code,
        market=market,
        bids=_price_levels(output2, "futs_bidp", "bidp_rsqn"),
        asks=_price_levels(output2, "futs_askp", "askp_rsqn"),
        total_bid_quantity=optional_int(output2.get("total_bidp_rsqn"), "total_bidp_rsqn") or 0,
        total_ask_quantity=optional_int(output2.get("total_askp_rsqn"), "total_askp_rsqn") or 0,
        as_of=datetime.now(_KST),
        _raw=output2,
    )


_UNDERLYING_PATH = "/uapi/domestic-futureoption/v1/quotations/display-board-top"
_UNDERLYING_TR = "FHPIF05030000"


def fetch_underlying_quote(transport: Transport, *, code: str, market: str) -> UnderlyingQuote:
    """선물 계약과 그 기초자산(지수)을 나란히 담는 스냅샷. ``code`` 는 선물 최근월물, ``market`` 은 F.
    기초자산/선물 전일대비는 서로 다른 부호 필드(unas_prdy_vrss_sign / prdy_vrss_sign)로 복원한다."""
    params = {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_COND_MRKT_DIV_CODE1": "",
        "FID_COND_SCR_DIV_CODE": "",
        "FID_MTRT_CNT": "",
        "FID_COND_MRKT_CLS_CODE": "",
    }
    resp = transport.request(
        method="GET", path=_UNDERLYING_PATH, tr_id=_UNDERLYING_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output1 = resp.body.get("output1")
    if not isinstance(output1, Mapping):
        raise _missing_block_error("output1", resp)
    unas_sign = str(output1.get("unas_prdy_vrss_sign", "")).strip()
    futs_sign = str(output1.get("prdy_vrss_sign", "")).strip()
    return UnderlyingQuote(
        symbol=code,
        name=str(output1.get("hts_kor_isnm", "")).strip(),
        underlying_price=required_decimal(output1.get("unas_prpr"), "unas_prpr"),
        underlying_change=_apply_change_sign(
            required_decimal(output1.get("unas_prdy_vrss"), "unas_prdy_vrss"), unas_sign
        ),
        underlying_change_percent=_apply_change_sign(
            required_decimal(output1.get("unas_prdy_ctrt"), "unas_prdy_ctrt"), unas_sign
        ),
        underlying_volume=required_int(output1.get("unas_acml_vol"), "unas_acml_vol"),
        futures_price=required_decimal(output1.get("futs_prpr"), "futs_prpr"),
        futures_change=_apply_change_sign(
            required_decimal(output1.get("futs_prdy_vrss"), "futs_prdy_vrss"), futs_sign
        ),
        futures_change_percent=_apply_change_sign(
            required_decimal(output1.get("futs_prdy_ctrt"), "futs_prdy_ctrt"), futs_sign
        ),
        as_of=datetime.now(_KST),
        _raw=output1,
    )


def fetch_bars(
    transport: Transport,
    *,
    code: str,
    market: str,
    interval: Interval = "1d",
    start: str | date | None = None,
    end: str | date | None = None,
    max_bars: int | None = None,
) -> list[Bar]:
    """선물/옵션 계약의 봉을 과거->현재 오름차순으로. ``market`` 은 F/O.

    ``interval="1m"`` 은 당일 1분봉을 최신부터 뒤로 밀며(``start``/``end`` 무시, ``max_bars`` 로 최근
    N개), ``1d``/``1wk``/``1mo`` 는 ``[start, end]`` 구간 기간봉(``start`` 필요). 캔들은 응답의
    ``output2`` 에 있고 종가는 채권/지수와 달리 ``futs_prpr``(현재가)이며, 파생엔 수정주가 개념이 없어
    조정 파라미터는 보내지 않는다. 페이지 상한에 닿으면 부분 결과로 자르지 않고 예외.
    """
    if max_bars is not None and max_bars <= 0:
        raise KISUsageError(f"max_bars 는 양의 정수여야 한다: {max_bars}")
    if interval == "1m":
        return _fetch_minute_bars(transport, code=code, market=market, max_bars=max_bars)
    if start is None:
        raise KISUsageError(f"interval={interval!r}(기간봉)에는 start 가 필요하다.")
    period = _period_code_for(interval)
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _to_yyyymmdd(start, "start")
    if start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    base_params = {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_PERIOD_DIV_CODE": period,
    }
    return collect_period_bars(
        transport, path=_BARS_PATH, tr=_BARS_TR, base_params=base_params,
        start_date=start_date, end_date=end_date, max_bars=max_bars,
        parse_rows=lambda rows: _parse_bars(rows, code=code),
    )


def _fetch_minute_bars(
    transport: Transport, *, code: str, market: str, max_bars: int | None
) -> list[Bar]:
    """당일 1분봉을 과거->현재 오름차순으로. 최신부터 102건씩 받고 FID_INPUT_HOUR_1 을 뒤로 밀며
    모은다. 파생은 세션 경계(야간장 등)가 다양해 개장시각 가정 대신 새 봉이 없으면 종료하고, 페이지
    상한 초과는 fail-closed. 봉 식별은 당일 시각(HHMMSS)."""
    day = _today_kst()
    bar_by_time: dict[str, Bar] = {}   # "HHMMSS" -> Bar, 고정폭이라 문자열 정렬=시간순
    anchor = _MINUTE_ANCHOR_START
    for _page in range(_MAX_MINUTE_PAGES):
        params = {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": code,
            "FID_HOUR_CLS_CODE": "60",         # 60 = 1분
            "FID_PW_DATA_INCU_YN": "N",        # 당일치
            "FID_FAKE_TICK_INCU_YN": "N",      # 허봉 제외
            "FID_INPUT_DATE_1": day,
            "FID_INPUT_HOUR_1": anchor,
        }
        resp = transport.request(
            method="GET", path=_MINUTE_BARS_PATH, tr_id=_MINUTE_BARS_TR, params=params,
            idempotent=True,
        )
        _raise_if_error(resp)
        rows = resp.body.get("output2")
        if not isinstance(rows, list):  # 성공 응답인데 봉 배열 아님 -> fail-closed
            raise _missing_block_error("output2", resp)
        page = {f"{bar.timestamp:%H%M%S}": bar for bar in _parse_minute_bars(rows, code=code)}
        fresh = {time: bar for time, bar in page.items() if time not in bar_by_time}
        if not fresh:  # 빈 페이지거나 진전 없음 -> 종료(무한 루프 방지)
            break
        bar_by_time.update(fresh)
        if max_bars is not None and len(bar_by_time) >= max_bars:
            break
        anchor = _subtract_one_minute(min(page))
    else:
        raise KISError(
            f"선물옵션 분봉 조회가 {_MAX_MINUTE_PAGES}페이지 상한에 도달했으나 진전을 멈추지 않았다 "
            f"-- 부분 결과로 자르지 않는다. max_bars 로 범위를 줄이거나 재시도하라."
        )
    bars = [bar_by_time[key] for key in sorted(bar_by_time)]
    if max_bars is not None and len(bars) > max_bars:
        bars = bars[-max_bars:]
    return bars


def _parse_minute_bars(rows: Sequence[Mapping[str, Any]], *, code: str) -> list[Bar]:
    """분봉 행 -> Bar. 종가 ``futs_prpr``, 분당 거래량 ``cntg_vol``(일봉의 acml_vol 과 다름),
    timestamp 는 일자+시각(``stck_bsop_date``+``stck_cntg_hour``)."""
    bars: list[Bar] = []
    for row in rows:
        date_text = str(row.get("stck_bsop_date", "")).strip()
        time_text = str(row.get("stck_cntg_hour", "")).strip()
        close_text = str(row.get("futs_prpr", "")).strip()
        if not date_text or not time_text or not close_text:   # 빈 봉 skip
            continue
        bars.append(
            Bar(
                symbol=code,
                timestamp=_parse_minute_bar_timestamp(date_text, time_text),
                open=required_decimal(row.get("futs_oprc"), "futs_oprc"),
                high=required_decimal(row.get("futs_hgpr"), "futs_hgpr"),
                low=required_decimal(row.get("futs_lwpr"), "futs_lwpr"),
                close=required_decimal(close_text, "futs_prpr"),
                volume=required_int(row.get("cntg_vol"), "cntg_vol"),
                _raw=row,
            )
        )
    return bars


def _parse_bars(rows: Sequence[Mapping[str, Any]], *, code: str) -> list[Bar]:
    bars: list[Bar] = []
    for row in rows:
        date_text = str(row.get("stck_bsop_date", "")).strip()
        close_text = str(row.get("futs_prpr", "")).strip()
        if not date_text or not close_text:    # 미체결 세션의 빈 바 -- 건너뜀
            continue
        bars.append(
            Bar(
                symbol=code,
                timestamp=_parse_bar_timestamp(date_text),
                open=required_decimal(row.get("futs_oprc"), "futs_oprc"),
                high=required_decimal(row.get("futs_hgpr"), "futs_hgpr"),
                low=required_decimal(row.get("futs_lwpr"), "futs_lwpr"),
                close=required_decimal(close_text, "futs_prpr"),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                _raw=row,
            )
        )
    return bars
