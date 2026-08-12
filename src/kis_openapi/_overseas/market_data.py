"""해외주식 시세 조회 (내부) -- 현재가 등을 :class:`Quote` 로.

해외는 거래소코드(EXCD)+심볼로 조회한다. 국내와 달리 통화가 시장마다 다르므로(USD/HKD/JPY...)
``Quote.currency`` 를 응답의 통화(``curr``)로 채운다. 전일대비는 KIS 가 native 통화로는 따로 주지
않아 현재가-전일종가로 계산한다.

KIS URL/TR-id (KIS 명세 대조):
- 해외 현재가상세: ``GET /uapi/overseas-price/v1/quotations/price-detail`` ``HHDFS76200200``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .._bars import (
    _MAX_BAR_PAGES,
    _MAX_MINUTE_PAGES,
    _parse_bar_timestamp,
    _parse_minute_bar_timestamp,
)
from .._datetime import (
    _KST,
    _to_yyyymmdd,
    _today_kst,
)
from .._depth import _price_levels
from .._response import (
    _missing_block_error,
    _raise_if_error,
    _require_mapping_rows,
)
from .._wire import (
    _apply_change_sign,
    optional_decimal,
    optional_int,
    required_decimal,
    required_int,
)
from ..bar import Bar, Interval
from ..errors import KISError, KISUsageError
from ..order_book import OrderBook
from ..overseas_items import (
    OverseasCurrentPrice,
    OverseasIndustry,
    OverseasIndustryStock,
    OverseasStockSearch,
    OverseasStockSearchItem,
)
from ..overseas_product import OverseasProductInfo
from ..quote import Quote
from ..trade import Trade
from ..transport import Environment, Transport

_QUOTE_PATH = "/uapi/overseas-price/v1/quotations/price-detail"
_QUOTE_TR = "HHDFS76200200"
_CURRENT_PRICE_PATH = "/uapi/overseas-price/v1/quotations/price"
_CURRENT_PRICE_TR = "HHDFS00000300"
_PERCENT = Decimal("0.01")

_PRODUCT_INFO_PATH = "/uapi/overseas-price/v1/quotations/search-info"
_PRODUCT_INFO_TR = "CTPF1702R"
_PRODUCT_TYPE_BY_EXCHANGE = {
    "NAS": "512",
    "NYS": "513",
    "AMS": "529",
    "TSE": "515",
    "HKS": "501",
    "HNX": "507",
    "HSX": "508",
    "SHS": "551",
    "SZS": "552",
}

_BARS_PATH = "/uapi/overseas-price/v1/quotations/dailyprice"
_BARS_TR = "HHDFS76240000"

_MULTI_QUOTE_PATH = "/uapi/overseas-price/v1/quotations/multprice"
_MULTI_QUOTE_TR = "HHDFS76220000"
_MAX_MULTI_QUOTE = 10           # KIS 명세: 슬롯 10개(EXCD_01 ~ _10, NREC 최대 10)
_SEARCH_PATH = "/uapi/overseas-price/v1/quotations/inquire-search"
_SEARCH_TR = "HHDFS76410000"
_MAX_SEARCH_PAGES = 100


def _range_params(name: str, value: tuple[object, object] | None) -> dict[str, str]:
    if value is None:
        return {f"CO_YN_{name}": "", f"CO_ST_{name}": "", f"CO_EN_{name}": ""}
    if len(value) != 2:
        raise KISUsageError(f"{name.lower()} 범위는 (시작, 끝) 두 값이어야 한다.")
    start, end = (str(item) for item in value)
    return {f"CO_YN_{name}": "1", f"CO_ST_{name}": start, f"CO_EN_{name}": end}


def search_stocks(
    transport: Transport, *, exchange: str,
    price: tuple[object, object] | None = None,
    change_percent: tuple[object, object] | None = None,
    market_cap: tuple[object, object] | None = None,
    shares: tuple[object, object] | None = None,
    volume: tuple[object, object] | None = None,
    amount: tuple[object, object] | None = None,
    eps: tuple[object, object] | None = None,
    per: tuple[object, object] | None = None,
) -> OverseasStockSearch:
    """해외 거래소 종목을 가격·등락률·규모·거래·밸류에이션 범위로 검색한다."""
    if not exchange.strip():
        raise KISUsageError("exchange 가 필요하다.")
    params = {"AUTH": "", "EXCD": exchange.strip(), "KEYB": ""}
    for name, value in (("PRICECUR", price), ("RATE", change_percent), ("VALX", market_cap),
                        ("SHAR", shares), ("VOLUME", volume), ("AMT", amount),
                        ("EPS", eps), ("PER", per)):
        params.update(_range_params(name, value))
    items: list[OverseasStockSearchItem] = []
    summary: Mapping[str, Any] | None = None
    tr_cont = ""
    for _page in range(_MAX_SEARCH_PAGES):
        resp = transport.request(
            method="GET", path=_SEARCH_PATH, tr_id=_SEARCH_TR, params=params,
            idempotent=True, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        output1, rows = resp.body.get("output1"), resp.body.get("output2")
        if not isinstance(output1, Mapping):
            raise _missing_block_error("output1", resp)
        if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
            raise _missing_block_error("output2", resp)
        if summary is None:
            summary = output1
        for row in rows:
            sign = str(row.get("sign", "")).strip()
            items.append(OverseasStockSearchItem(
                realtime_symbol=str(row.get("rsym", "")).strip(),
                exchange=str(row.get("excd", "")).strip(), symbol=str(row.get("symb", "")).strip(),
                name=str(row.get("name", "")).strip(), english_name=str(row.get("ename", "")).strip(),
                price=required_decimal(row.get("last"), "last"),
                change=_apply_change_sign(required_decimal(row.get("diff"), "diff"), sign),
                change_percent=_apply_change_sign(required_decimal(row.get("rate"), "rate"), sign),
                open=required_decimal(row.get("popen"), "popen"),
                high=required_decimal(row.get("phigh"), "phigh"),
                low=required_decimal(row.get("plow"), "plow"),
                volume=required_int(row.get("tvol"), "tvol"),
                amount=required_decimal(row.get("avol"), "avol"),
                shares=required_decimal(row.get("shar"), "shar"),
                market_cap=required_decimal(row.get("valx"), "valx"),
                eps=optional_decimal(row.get("eps"), "eps"), per=optional_decimal(row.get("per"), "per"),
                rank=required_int(row.get("rank"), "rank"),
                is_tradable=str(row.get("e_ordyn", "")).strip() == "O", _raw=row,
            ))
        if resp.tr_cont not in {"F", "M"}:
            break
        tr_cont = "N"
    else:
        raise KISError(f"해외 종목검색이 {_MAX_SEARCH_PAGES}페이지 상한을 넘겼다(다음조회 미종료).")
    assert summary is not None
    return OverseasStockSearch(
        exchange=exchange.strip(), decimal_places=required_int(summary.get("zdiv"), "zdiv"),
        status=str(summary.get("stat", "")).strip(),
        total_count=required_int(summary.get("trec"), "trec"), items=tuple(items),
    )


def fetch_multi_quotes(
    transport: Transport, *, requests: Sequence[tuple[str, str]]
) -> list[Quote]:
    """여러 해외 종목의 현재가를 한 번에. ``requests`` 는 (거래소코드, 종목코드) 쌍(거래소 혼합 가능).
    슬롯 10개 상한. 응답 ``output2`` 각 행을 단일 현재가와 같은 방식(전일종가 base 로 등락 계산)으로
    :class:`Quote` 에 실어 준다."""
    if not requests:
        return []
    if len(requests) > _MAX_MULTI_QUOTE:
        raise KISUsageError(
            f"해외 멀티시세는 한 번에 {_MAX_MULTI_QUOTE}종목까지: {len(requests)}개 요청"
        )
    params: dict[str, str] = {"AUTH": "", "NREC": str(len(requests))}
    for i in range(_MAX_MULTI_QUOTE):
        slot = f"{i + 1:02d}"
        if i < len(requests):
            exchange, symbol = requests[i]
            params[f"EXCD_{slot}"] = exchange
            params[f"SYMB_{slot}"] = symbol
        else:                                   # 남는 슬롯도 키는 있어야 함(모두 Required) -> 공백
            params[f"EXCD_{slot}"] = ""
            params[f"SYMB_{slot}"] = ""
    resp = transport.request(
        method="GET", path=_MULTI_QUOTE_PATH, tr_id=_MULTI_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output2", resp)
    as_of = datetime.now(_KST)
    quotes: list[Quote] = []
    for row in rows:
        symbol = str(row.get("symb", "")).strip()
        exchange = str(row.get("excd", "")).strip()
        if not symbol:
            continue
        quotes.append(_parse_quote(row, symbol=symbol, exchange=exchange, as_of=as_of))
    return quotes
#: 해외 기간봉 간격 -> GUBN(KIS 명세: 0:일 1:주 2:월).
_BARS_GUBN = {"1d": "0", "1wk": "1", "1mo": "2"}

#: 해외 분봉. 한 번에 최대 120건, KEYB(마지막 봉 1분 전 시각)로 다음 조회. 국내와 달리 거래소별
#: 현지시각 기준이라 봉 식별/KEYB 모두 현지 일자+시각(xymd+xhms)을 쓴다.
_MINUTE_BARS_PATH = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
_MINUTE_BARS_TR = "HHDFS76950200"
_MINUTE_NREC = "120"              # 한 페이지 최대 레코드(KIS 명세 상한)

_TRADES_PATH = "/uapi/overseas-price/v1/quotations/inquire-ccnl"
_TRADES_TR = "HHDFS76200300"

_ORDER_BOOK_PATH = "/uapi/overseas-price/v1/quotations/inquire-asking-price"
_ORDER_BOOK_TR = "HHDFS76200100"


def fetch_product_info(
    transport: Transport, *, exchange: str, symbol: str
) -> OverseasProductInfo:
    """해외 종목의 상품기본정보. ``exchange`` 는 거래소코드(NAS/NYS/AMS/TSE/HKS/...)."""
    product_type = _PRODUCT_TYPE_BY_EXCHANGE.get(exchange)
    if product_type is None:
        valid = "/".join(_PRODUCT_TYPE_BY_EXCHANGE)
        raise KISUsageError(f"지원하지 않는 해외 거래소코드: {exchange!r} ({valid})")
    resp = transport.request(
        method="GET",
        path=_PRODUCT_INFO_PATH,
        tr_id=_PRODUCT_INFO_TR,
        params={"PRDT_TYPE_CD": product_type, "PDNO": symbol},
        idempotent=True,
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output", resp)
    return OverseasProductInfo(
        symbol=symbol,
        isin=str(output.get("std_pdno", "")).strip(),
        name=str(output.get("prdt_name", "")).strip(),
        english_name=str(output.get("prdt_eng_name", "")).strip(),
        exchange_code=str(output.get("ovrs_excg_cd", "")).strip(),
        exchange_name=str(output.get("ovrs_excg_name", "")).strip(),
        country=str(output.get("natn_name", "")).strip(),
        currency=str(output.get("tr_crcy_cd", "")).strip(),
        currency_name=str(output.get("crcy_name", "")).strip(),
        par_value=optional_decimal(output.get("ovrs_papr"), "ovrs_papr"),
        listed_shares=optional_int(output.get("lstg_stck_num"), "lstg_stck_num"),
        buy_unit=optional_int(output.get("buy_unit_qty"), "buy_unit_qty"),
        sell_unit=optional_int(output.get("sll_unit_qty"), "sll_unit_qty"),
        sedol=str(output.get("sedol_no", "")).strip(),
        bloomberg_ticker=str(output.get("blbg_tckr_text", "")).strip(),
        is_listed=str(output.get("lstg_yn", "")).strip().upper() == "Y",
        is_delisted=str(output.get("lstg_abol_item_yn", "")).strip().upper() == "Y",
        taxable=str(output.get("tax_levy_yn", "")).strip().upper() == "Y",
        _raw=output,
    )


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


def fetch_current_price(
    transport: Transport, *, symbol: str, exchange: str
) -> OverseasCurrentPrice:
    """해외주식의 간결한 현재체결가와 누적 거래량·거래대금을 조회한다."""
    resp = transport.request(
        method="GET",
        path=_CURRENT_PRICE_PATH,
        tr_id=_CURRENT_PRICE_TR,
        params={"AUTH": "", "EXCD": exchange, "SYMB": symbol},
        idempotent=True,
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output", resp)
    sign = str(output.get("sign", "")).strip()
    return OverseasCurrentPrice(
        symbol=symbol,
        exchange=exchange,
        last=required_decimal(output.get("last"), "last"),
        previous_close=required_decimal(output.get("base"), "base"),
        change=_apply_change_sign(required_decimal(output.get("diff"), "diff"), sign),
        change_percent=_apply_change_sign(required_decimal(output.get("rate"), "rate"), sign),
        previous_volume=required_int(output.get("pvol"), "pvol"),
        volume=required_int(output.get("tvol"), "tvol"),
        traded_amount=required_decimal(output.get("tamt"), "tamt"),
        decimal_places=required_int(output.get("zdiv"), "zdiv"),
        buyable_status=str(output.get("ordy", "")).strip(),
        _raw=output,
    )


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
    """해외 봉을 과거->현재 오름차순으로. ``interval="1m"`` 은 별 엔드포인트로 최신 분봉을 뒤로 밀며
    (``start``/``end``/``adjusted`` 무시, ``max_bars`` 로 최근 N개), ``1d``/``1wk``/``1mo`` 는
    [start, end] 기간봉(``start`` 필요, ``end`` 기본 오늘, ``adjusted`` = 수정주가 MODP).

    dailyprice 는 기준일(BYMD)에서 뒤로 한 페이지씩 주므로 BYMD 를 옛날로 밀며 ``start`` 까지 모으고,
    페이지 상한 초과는 fail-closed."""
    if max_bars is not None and max_bars <= 0:
        raise KISUsageError(f"max_bars 는 양의 정수여야 한다: {max_bars}")
    if interval == "1m":
        return _fetch_minute_bars(
            transport, symbol=symbol, exchange=exchange, max_bars=max_bars
        )
    gubn = _BARS_GUBN.get(interval)
    if gubn is None:
        raise KISUsageError(f"지원하지 않는 해외 기간봉 interval: {interval!r} (1d/1wk/1mo).")
    if start is None:
        raise KISUsageError(f"interval={interval!r}(기간봉)에는 start 가 필요하다.")
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _to_yyyymmdd(start, "start")
    if start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
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
        raise KISError(
            f"해외 바 조회가 {_MAX_BAR_PAGES}페이지 상한에 도달했으나 start({start_date})에 못 미쳤다 "
            f"-- 부분 결과로 자르지 않는다. 범위를 좁히거나 재시도하라."
        )

    bars = [bar_by_date[key] for key in sorted(bar_by_date) if start_date <= key <= end_date]
    if max_bars is not None and len(bars) > max_bars:
        bars = bars[-max_bars:]
    return bars


def _fetch_minute_bars(
    transport: Transport, *, symbol: str, exchange: str, max_bars: int | None
) -> list[Bar]:
    """해외 1분봉을 과거->현재 오름차순으로. 최신부터 120건씩 받고, KEYB(직전 페이지 최오래 봉의 현지
    시각 1분 전)로 뒤로 밀며 모은다. 새 봉이 없으면 종료(다음가능 플래그 인코딩과 무관하게 자가종료),
    페이지 상한 초과는 fail-closed. 봉 식별은 현지 일자+시각(xymd+xhms)."""
    bar_by_key: dict[str, Bar] = {}    # "YYYYMMDDHHMMSS"(현지) -> Bar, 고정폭이라 문자열 정렬=시간순
    keyb = ""                          # 첫 조회는 공백
    for _page in range(_MAX_MINUTE_PAGES):
        params = {
            "AUTH": "", "EXCD": exchange, "SYMB": symbol,
            "NMIN": "1", "PINC": "1", "NEXT": "",
            "NREC": _MINUTE_NREC, "FILL": "", "KEYB": keyb,
        }
        resp = transport.request(
            method="GET", path=_MINUTE_BARS_PATH, tr_id=_MINUTE_BARS_TR, params=params,
            idempotent=True,
        )
        _raise_if_error(resp)
        rows = resp.body.get("output2")
        if not isinstance(rows, list):  # 성공 응답인데 봉 배열 아님 -> fail-closed
            raise _missing_block_error("output2", resp)
        page = _parse_minute_bars(rows, symbol=symbol)
        keyed = {f"{day}{moment}": bar for day, moment, bar in page}
        fresh = {key: bar for key, bar in keyed.items() if key not in bar_by_key}
        if not fresh:  # 빈 페이지거나 진전 없음 -> 종료(무한 루프 방지)
            break
        bar_by_key.update(fresh)
        if max_bars is not None and len(bar_by_key) >= max_bars:
            break
        oldest_day, oldest_moment, _ = min(page, key=lambda item: item[0] + item[1])
        try:
            edge = datetime.strptime(oldest_day + oldest_moment, "%Y%m%d%H%M%S")  # noqa: DTZ007
        except ValueError as err:
            raise KISError(f"해외 분봉 KEYB 시각 파싱 실패: {oldest_day!r} {oldest_moment!r}") from err
        keyb = f"{edge - timedelta(minutes=1):%Y%m%d%H%M%S}"
    else:
        raise KISError(
            f"해외 분봉 조회가 {_MAX_MINUTE_PAGES}페이지 상한에 도달했으나 진전을 멈추지 않았다 "
            f"-- 부분 결과로 자르지 않는다. max_bars 로 범위를 줄이거나 재시도하라."
        )
    bars = [bar_by_key[key] for key in sorted(bar_by_key)]
    if max_bars is not None and len(bars) > max_bars:
        bars = bars[-max_bars:]
    return bars


def _parse_minute_bars(
    rows: Sequence[Mapping[str, Any]], *, symbol: str
) -> list[tuple[str, str, Bar]]:
    """해외 분봉 행 -> (현지일자, 현지시각, Bar). 종가 ``last``, 거래량 ``evol``, timestamp 는 현지
    일자+시각(``xymd``+``xhms``; 해외 일봉이 현지일자 xymd 를 쓰는 것과 같은 관례)."""
    out: list[tuple[str, str, Bar]] = []
    for row in rows:
        day = str(row.get("xymd", "")).strip()
        moment = str(row.get("xhms", "")).strip()
        close_text = str(row.get("last", "")).strip()
        if not day or not moment or not close_text:  # 빈 봉 skip
            continue
        out.append((
            day, moment,
            Bar(
                symbol=symbol,
                timestamp=_parse_minute_bar_timestamp(day, moment),
                open=required_decimal(row.get("open"), "open"),
                high=required_decimal(row.get("high"), "high"),
                low=required_decimal(row.get("low"), "low"),
                close=required_decimal(close_text, "last"),
                volume=required_int(row.get("evol"), "evol"),
                _raw=row,
            ),
        ))
    return out


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


def fetch_order_book(transport: Transport, *, symbol: str, exchange: str) -> OrderBook:
    """해외 호가창 스냅샷. **미국은 10단계, 그 외 국가는 1단계**만 제공(KIS 명세). output1=총잔량 헤더,
    output2=단계별 매수/매도 호가. 도메스틱과 같은 :class:`~kis_openapi.order_book.OrderBook` 로."""
    params = {"AUTH": "", "EXCD": exchange, "SYMB": symbol}
    resp = transport.request(
        method="GET", path=_ORDER_BOOK_PATH, tr_id=_ORDER_BOOK_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output1 = resp.body.get("output1")         # 총잔량/시세 헤더
    output2 = resp.body.get("output2")         # 단계별 호가(pbidN/paskN/vbidN/vaskN)
    if not isinstance(output1, Mapping):
        raise _missing_block_error("output1", resp)
    if not isinstance(output2, Mapping):
        raise _missing_block_error("output2", resp)
    return OrderBook(
        symbol=symbol,
        market=exchange,
        bids=_price_levels(output2, "pbid", "vbid"),
        asks=_price_levels(output2, "pask", "vask"),
        total_bid_quantity=optional_int(output1.get("bvol"), "bvol") or 0,
        total_ask_quantity=optional_int(output1.get("avol"), "avol") or 0,
        as_of=datetime.now(_KST),
        _raw={**dict(output1), **dict(output2)},
    )


def fetch_trades(transport: Transport, *, symbol: str, exchange: str) -> list[Trade]:
    """해외 최근 체결(time & sales; 당일). 벤더 순서(최신순)를 유지한다. 시각은 한국기준시간(khms)."""
    params = {"AUTH": "", "EXCD": exchange, "SYMB": symbol, "TDAY": "1", "KEYB": ""}
    resp = transport.request(
        method="GET", path=_TRADES_PATH, tr_id=_TRADES_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output1")
    if not isinstance(rows, list):     # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output1", resp)
    return _parse_trades(rows, symbol=symbol, today=_today_kst())


def _parse_trades(
    rows: Sequence[Mapping[str, Any]], *, symbol: str, today: str
) -> list[Trade]:
    """해외 체결 행 -> Trade. 시각 khms(한국기준시간 HHMMSS)에 조회일을 붙인다. 체결량은 evol."""
    trades: list[Trade] = []
    for row in rows:
        time_text = str(row.get("khms", "")).strip()
        price_text = str(row.get("last", "")).strip()
        if not time_text or not price_text:  # 빈 체결 skip
            continue
        sign = str(row.get("sign", "")).strip()
        trades.append(
            Trade(
                symbol=symbol,
                timestamp=_parse_minute_bar_timestamp(today, time_text),
                price=required_decimal(price_text, "last"),
                quantity=required_int(row.get("evol"), "evol"),
                change=_apply_change_sign(required_decimal(row.get("diff"), "diff"), sign),
                change_percent=_apply_change_sign(required_decimal(row.get("rate"), "rate"), sign),
                _raw=row,
            )
        )
    return trades


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


def fetch_industries(
    transport: Transport, *, exchange: str, environment: Environment
) -> list[OverseasIndustry]:
    """해외 거래소의 업종(섹터) 코드 목록을 조회한다.

    ``exchange`` 는 해외 거래소코드(NAS/NYS/AMS/HKS/...)다.

    KIS ``GET /uapi/overseas-price/v1/quotations/industry-price``
    (``HHDFS76370100``)를 사용하며 모의투자는 지원하지 않는다.

    이 조회는 연속조회 없이 한 번만 호출한다.

    성공 응답의 ``output2`` 객체 배열이 없거나 항목이 객체가 아니면 부분 결과 대신 실패한다.
    """
    if environment == "demo":
        raise KISUsageError("해외 업종 코드 목록 조회는 모의투자 미지원이다(실전만).")

    resp = transport.request(
        method="GET",
        path="/uapi/overseas-price/v1/quotations/industry-price",
        tr_id="HHDFS76370100",
        params={"AUTH": "", "EXCD": exchange},
        idempotent=True,
    )
    _raise_if_error(resp)
    page = resp.body.get("output2")
    if not isinstance(page, list):
        raise _missing_block_error("output2", resp)
    if not all(isinstance(row, Mapping) for row in page):
        raise KISError("해외 업종 코드 응답의 output2 항목이 객체가 아니다.", raw=resp.body)
    return [
        OverseasIndustry(
            code=str(row.get("icod", "")).strip(),
            name=str(row.get("name", "")).strip(),
            _raw=row,
        )
        for row in page
    ]


_INDUSTRY_STOCKS_PATH = "/uapi/overseas-price/v1/quotations/industry-theme"
_INDUSTRY_STOCKS_TR = "HHDFS76370000"
_INDUSTRY_VOLUME_FILTER = {
    0: "0",
    100: "1",
    1_000: "2",
    10_000: "3",
    100_000: "4",
    1_000_000: "5",
    10_000_000: "6",
}


def fetch_industry_stocks(
    transport: Transport,
    *,
    exchange: str,
    industry_code: str,
    min_volume: int,
    environment: Environment,
) -> list[OverseasIndustryStock]:
    """해외 거래소의 한 업종에 속한 종목 시세 목록."""
    if environment == "demo":
        raise KISUsageError("해외 업종별 시세 조회는 모의투자 미지원이다(실전만).")
    volume_code = _INDUSTRY_VOLUME_FILTER.get(min_volume)
    if volume_code is None:
        raise KISUsageError(
            f"min_volume 은 {sorted(_INDUSTRY_VOLUME_FILTER)} 중 하나: {min_volume!r}"
        )
    resp = transport.request(
        method="GET",
        path=_INDUSTRY_STOCKS_PATH,
        tr_id=_INDUSTRY_STOCKS_TR,
        params={
            "KEYB": "",
            "AUTH": "",
            "EXCD": exchange,
            "ICOD": industry_code,
            "VOL_RANG": volume_code,
        },
        idempotent=True,
    )
    _raise_if_error(resp)
    header = resp.body.get("output1")
    rows = resp.body.get("output2")
    if not isinstance(header, Mapping):
        raise _missing_block_error("output1", resp)
    if not isinstance(rows, list):
        raise _missing_block_error("output2", resp)
    stocks: list[OverseasIndustryStock] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output2[]", resp)
        symbol = str(row.get("symb", "")).strip()
        if not symbol:
            continue
        sign = str(row.get("sign", "")).strip()
        stocks.append(
            OverseasIndustryStock(
                exchange=str(row.get("excd", exchange)).strip(),
                symbol=symbol,
                name=str(row.get("name", "")).strip(),
                english_name=str(row.get("ename", "")).strip(),
                last=required_decimal(row.get("last"), "last"),
                change=_apply_change_sign(
                    required_decimal(row.get("diff"), "diff"), sign
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("rate"), "rate"), sign
                ),
                volume=required_int(row.get("tvol"), "tvol"),
                ask_price=required_decimal(row.get("pask"), "pask"),
                ask_quantity=required_int(row.get("vask"), "vask"),
                bid_price=required_decimal(row.get("pbid"), "pbid"),
                bid_quantity=required_int(row.get("vbid"), "vbid"),
                rank=required_int(row.get("seqn"), "seqn"),
                is_tradable=str(row.get("e_ordyn", "")).strip() == "Y",
                _raw=row,
            )
        )
    return stocks
