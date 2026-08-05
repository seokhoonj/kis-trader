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
from ..derivative_items import DerivativesQuote
from ..errors import KisUsageError
from ..order_book import OrderBook
from ..transport import Transport
from .market_data import (
    _KST,
    _apply_change_sign,
    _missing_block_error,
    _parse_bar_timestamp,
    _period_code_for,
    _price_levels,
    _raise_if_error,
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
    """선물/옵션 계약의 기간봉(일/주/월)을 과거->현재 오름차순으로. ``market`` 은 F/O.

    ``interval`` 은 ``1d``/``1wk``/``1mo`` (분봉은 미지원). ``[start, end]`` 구간 기간봉이라 ``start``
    가 필요하다. 캔들은 응답의 ``output2`` 에 있고 종가는 채권/지수와 달리 ``futs_prpr``(현재가)이며,
    파생엔 수정주가 개념이 없어 조정 파라미터는 보내지 않는다. 페이지 상한에 닿으면 부분 결과로 자르지
    않고 예외.
    """
    if max_bars is not None and max_bars <= 0:
        raise KisUsageError(f"max_bars 는 양의 정수여야 한다: {max_bars}")
    if interval == "1m":
        raise KisUsageError("선물옵션 분봉은 아직 미지원이다 (1d/1wk/1mo).")
    if start is None:
        raise KisUsageError(f"interval={interval!r}(기간봉)에는 start 가 필요하다.")
    period = _period_code_for(interval)
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _to_yyyymmdd(start, "start")
    if start_date > end_date:
        raise KisUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
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
