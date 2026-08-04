"""해외주식 시세 조회 (내부) -- 현재가 등을 :class:`Quote` 로.

해외는 거래소코드(EXCD)+심볼로 조회한다. 국내와 달리 통화가 시장마다 다르므로(USD/HKD/JPY...)
``Quote.currency`` 를 응답의 통화(``curr``)로 채운다. 전일대비는 KIS 가 native 통화로는 따로 주지
않아 현재가-전일종가로 계산한다.

KIS URL/TR-id (원장 대조):
- 해외 현재가상세: ``GET /uapi/overseas-price/v1/quotations/price-detail`` ``HHDFS76200200``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .._domestic.market_data import (
    _KST,
    _MAX_BAR_PAGES,
    _missing_block_error,
    _parse_bar_timestamp,
    _raise_if_error,
    _to_yyyymmdd,
    _today_kst,
)
from .._wire import required_decimal, required_int
from ..bar import Bar, Interval
from ..errors import KisError, KisUsageError
from ..quote import Quote
from ..transport import Transport

_QUOTE_PATH = "/uapi/overseas-price/v1/quotations/price-detail"
_QUOTE_TR = "HHDFS76200200"
_PERCENT = Decimal("0.01")

_BARS_PATH = "/uapi/overseas-price/v1/quotations/dailyprice"
_BARS_TR = "HHDFS76240000"
#: 해외 기간봉 간격 -> GUBN(원장: 0:일 1:주 2:월).
_BARS_GUBN = {"1d": "0", "1wk": "1", "1mo": "2"}


def fetch_quote(transport: Transport, *, symbol: str, exchange: str) -> Quote:
    """해외 현재가 스냅샷. ``exchange`` 는 거래소코드(NAS/NYS/AMS/TSE/HKS/...)."""
    params = {"AUTH": "", "EXCD": exchange, "SYMB": symbol}
    resp = transport.request(
        method="GET", path=_QUOTE_PATH, tr_id=_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_quote(output, symbol=symbol, exchange=exchange, as_of=datetime.now(_KST))


def fetch_bars(
    transport: Transport,
    *,
    symbol: str,
    exchange: str,
    interval: Interval = "1d",
    start: str | date | None = None,
    end: str | date | None = None,
    adjusted: bool = True,
    max_bars: int | None = None,
) -> list[Bar]:
    """해외 기간봉(일/주/월)을 과거->현재 오름차순으로. ``interval`` 은 ``1d``/``1wk``/``1mo``,
    ``start`` 가 필요하다(``end`` 기본 오늘). ``adjusted`` 는 수정주가 반영(MODP).

    해외 분봉(``1m``)은 별도 엔드포인트라 아직 미지원. dailyprice 는 기준일(BYMD)에서 뒤로 한
    페이지씩 주므로 BYMD 를 옛날로 밀며 ``start`` 까지 모으고, 페이지 상한 초과는 fail-closed."""
    if max_bars is not None and max_bars <= 0:
        raise KisUsageError(f"max_bars 는 양의 정수여야 한다: {max_bars}")
    if interval == "1m":
        raise NotImplementedError("해외 분봉은 아직 미지원 -- 별 슬라이스로 다룬다.")
    gubn = _BARS_GUBN.get(interval)
    if gubn is None:
        raise KisUsageError(f"지원하지 않는 해외 기간봉 interval: {interval!r} (1d/1wk/1mo).")
    if start is None:
        raise KisUsageError(f"interval={interval!r}(기간봉)에는 start 가 필요하다.")
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _to_yyyymmdd(start, "start")
    if start_date > end_date:
        raise KisUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    modp = "1" if adjusted else "0"    # 수정주가 반영 여부

    bar_by_date: dict[str, Bar] = {}
    base_date = end_date
    for _page in range(_MAX_BAR_PAGES):
        params = {
            "AUTH": "", "EXCD": exchange, "SYMB": symbol,
            "GUBN": gubn, "BYMD": base_date, "MODP": modp,
        }
        resp = transport.request(
            method="GET", path=_BARS_PATH, tr_id=_BARS_TR, params=params, idempotent=True
        )
        _raise_if_error(resp)
        rows = resp.body.get("output2")
        if not isinstance(rows, list):  # 성공 응답인데 바 배열 아님 -> fail-closed
            raise _missing_block_error("output2", resp)
        page = {f"{bar.timestamp:%Y%m%d}": bar for bar in _parse_bars(rows, symbol=symbol)}
        fresh = {day: bar for day, bar in page.items() if day not in bar_by_date}
        if not fresh:  # 빈 페이지거나 진전 없음(더 과거 데이터 없음) -> 종료
            break
        bar_by_date.update(fresh)
        if max_bars is not None and len(bar_by_date) >= max_bars:
            break
        oldest = min(page)             # YYYYMMDD 고정폭 -> 문자열 비교 = 시간순
        if oldest <= start_date:
            break
        base_date = f"{_parse_bar_timestamp(oldest) - timedelta(days=1):%Y%m%d}"
    else:
        raise KisError(
            f"해외 바 조회가 {_MAX_BAR_PAGES}페이지 상한에 도달했으나 start({start_date})에 못 미쳤다 "
            f"-- 부분 결과로 자르지 않는다. 범위를 좁히거나 재시도하라."
        )

    bars = [bar_by_date[key] for key in sorted(bar_by_date) if start_date <= key <= end_date]
    if max_bars is not None and len(bars) > max_bars:
        bars = bars[-max_bars:]
    return bars


def _parse_bars(rows: Sequence[Mapping[str, Any]], *, symbol: str) -> list[Bar]:
    """해외 일봉 행 -> Bar. 일자 ``xymd``, 종가 ``clos``, 시고저 ``open/high/low``, 거래량 ``tvol``."""
    bars: list[Bar] = []
    for row in rows:
        date_text = str(row.get("xymd", "")).strip()
        close_text = str(row.get("clos", "")).strip()
        if not date_text or not close_text:  # 미개장/빈 바 skip
            continue
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(date_text),
                open=required_decimal(row.get("open"), "open"),
                high=required_decimal(row.get("high"), "high"),
                low=required_decimal(row.get("low"), "low"),
                close=required_decimal(close_text, "clos"),
                volume=required_int(row.get("tvol"), "tvol"),
                _raw=row,
            )
        )
    return bars


def _parse_quote(
    output: Mapping[str, Any], *, symbol: str, exchange: str, as_of: datetime
) -> Quote:
    last = required_decimal(output.get("last"), "last")
    previous_close = required_decimal(output.get("base"), "base")
    change = last - previous_close
    # native 등락률은 응답에 없어 계산한다(전일종가 0 이면 나눗셈 불가 -> 0).
    change_percent = (
        (change / previous_close * 100).quantize(_PERCENT)
        if previous_close != 0
        else Decimal(0)
    )
    return Quote(
        symbol=symbol,
        market=exchange,
        currency=str(output.get("curr", "")).strip(),
        last=last,
        open=required_decimal(output.get("open"), "open"),
        high=required_decimal(output.get("high"), "high"),
        low=required_decimal(output.get("low"), "low"),
        previous_close=previous_close,
        change=change,
        change_percent=change_percent,
        volume=required_int(output.get("tvol"), "tvol"),
        week_52_high=required_decimal(output.get("h52p"), "h52p"),
        week_52_low=required_decimal(output.get("l52p"), "l52p"),
        as_of=as_of,
        _raw=output,
    )
