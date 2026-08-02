"""국내주식 시세(quotations) 파사드 -- :class:`Quotations`.

현재가 스냅샷(:meth:`quote`)과 기간별 OHLCV 바(:meth:`bars`)를 제공한다. 파사드는 구체
HTTP 세션이 아니라 :class:`~kis_openapi.transport.Transport` 프로토콜에 의존하므로, 저장된
KIS 응답을 주입해 자격증명·네트워크 없이 파싱 경로를 결정적으로 테스트할 수 있다.

바 조회는 KIS의 호출당 100개 상한을 **날짜 창을 뒤로 밀며** 넘긴다(요청 [start, end] 를 다
덮을 때까지). 상한에 부딪히면 조용히 자르지 않고 예외로 알린다(부분 결과를 전부로 오인하지
않게).

KIS URL/TR-id (국내주식 quotations):
- 현재가: ``GET /uapi/domestic-stock/v1/quotations/inquire-price`` (``FHKST01010100``).
- 기간별 OHLCV: ``GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice``
  (``FHKST03010100``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone

from ...errors import KisError, KisUsageError
from ...transport import RawResponse, Transport
from .bar import Bar, Interval, parse_bars, period_code_for
from .quote import Market, Quote, parse_quote

#: 시장 보드 -> KIS 조건시장분류코드(FID_COND_MRKT_DIV_CODE).
_MARKET_DIV = {"KRX": "J", "NXT": "NX", "UN": "UN"}

_KST = timezone(timedelta(hours=9))

_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
_QUOTE_TR = "FHKST01010100"
_BARS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
_BARS_TR = "FHKST03010100"

#: 날짜창 페이지네이션 안전 상한(무한 루프 방지). 일봉 기준 ~20000개까지 -- 실사용 범위를
#: 크게 웃돈다. 여기 닿으면 부분 결과로 자르지 않고 예외로 fail-closed 한다.
_MAX_BAR_PAGES = 200


class Quotations:
    """국내주식 시세 조회 표면. 하나의 :class:`Transport` 를 공유한다(읽기 전용)."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def quote(self, symbol: str, *, market: Market = "KRX") -> Quote:
        """한 종목의 현재가 스냅샷을 조회한다(호가 제외 -- 호가는 order_book 슬라이스)."""
        params = {
            "FID_COND_MRKT_DIV_CODE": _resolve_market(market),
            "FID_INPUT_ISCD": symbol,
        }
        resp = self._transport.request(
            method="GET", path=_QUOTE_PATH, tr_id=_QUOTE_TR, params=params, idempotent=True
        )
        _raise_if_error(resp)
        output = resp.body.get("output")
        if not isinstance(output, Mapping):
            raise KisError(
                "현재가 응답에 output 객체가 없다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        return parse_quote(output, symbol=symbol, market=market, as_of=datetime.now(_KST))

    def bars(
        self,
        symbol: str,
        *,
        start: str | date,
        end: str | date | None = None,
        interval: Interval = "1d",
        adjusted: bool = True,
        market: Market = "KRX",
        max_bars: int | None = None,
    ) -> list[Bar]:
        """[start, end] 구간의 OHLCV 바를 오름차순(과거->현재)으로 조회한다.

        ``interval`` 은 일/주/월(``1d``/``1wk``/``1mo``)만 지원(분봉은 다음 슬라이스).
        ``end`` 생략 시 오늘(KST). ``adjusted=True`` 면 수정주가, ``False`` 면 원주가.
        ``max_bars`` 를 주면 가장 최근 그 개수만 남긴다(명시적 상한 -- 조용한 절단 아님).
        KIS 100개/호출 상한은 날짜창을 뒤로 밀며 자동으로 넘는다.
        """
        period = period_code_for(interval)  # 분봉/미지 토큰이면 I/O 전에 여기서 중단
        market_div = _resolve_market(market)
        end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
        start_date = _to_yyyymmdd(start, "start")
        if start_date > end_date:
            raise KisUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
        if max_bars is not None and max_bars <= 0:
            raise KisUsageError(f"max_bars 는 양의 정수여야 한다: {max_bars}")
        adjusted_code = "0" if adjusted else "1"  # KIS 극성: 0=수정주가, 1=원주가

        bar_by_date: dict[str, Bar] = {}  # 날짜 키(YYYYMMDD)로 중복 페이지 병합
        window_end = end_date
        for _page in range(_MAX_BAR_PAGES):
            params = {
                "FID_COND_MRKT_DIV_CODE": market_div,
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start_date,
                "FID_INPUT_DATE_2": window_end,
                "FID_PERIOD_DIV_CODE": period,
                "FID_ORG_ADJ_PRC": adjusted_code,
            }
            resp = self._transport.request(
                method="GET", path=_BARS_PATH, tr_id=_BARS_TR, params=params, idempotent=True
            )
            _raise_if_error(resp)
            rows = resp.body.get("output2", [])
            if not isinstance(rows, list):  # 성공 응답인데 바 배열이 아님 -> fail-closed
                raise KisError(
                    "기간별시세 응답의 output2 가 리스트가 아니다.",
                    rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
                )
            # 날짜 키를 페이지당 1회만 계산해 이후 병합/경계판정/정렬에 재사용.
            page_by_date = {f"{bar.timestamp:%Y%m%d}": bar for bar in parse_bars(rows, symbol=symbol)}
            if not page_by_date:  # 이 창에 바 없음 -> 뒤로 다 훑음
                break
            bar_by_date.update(page_by_date)
            # 페이지는 최근->과거로 훑으므로, 최근 max_bars 개면 충분하면 더 안 훑는다(불필요한 호출/유량 절약).
            if max_bars is not None and len(bar_by_date) >= max_bars:
                break
            oldest_date = min(page_by_date)  # YYYYMMDD 고정폭 -> 문자열 비교 = 시간순
            if oldest_date <= start_date:  # 요청 시작일까지 도달
                break
            oldest = page_by_date[oldest_date].timestamp
            window_end = f"{oldest - timedelta(days=1):%Y%m%d}"  # 다음 창은 가장 오래된 바 하루 전까지
        else:
            raise KisError(
                f"바 조회가 {_MAX_BAR_PAGES}페이지 상한에 도달했으나 start({start_date})에 "
                f"못 미쳤다 -- 부분 결과로 자르지 않는다. 범위를 좁히거나 재시도하라."
            )

        bars = [
            bar_by_date[date_key]
            for date_key in sorted(bar_by_date)
            if start_date <= date_key <= end_date
        ]
        if max_bars is not None and len(bars) > max_bars:
            bars = bars[-max_bars:]  # 가장 최근 max_bars 개
        return bars


def _resolve_market(market: str) -> str:
    try:
        return _MARKET_DIV[market]
    except KeyError:
        raise KisUsageError(
            f"지원하지 않는 market: {market!r} (KRX/NXT/UN 중 하나여야 한다)."
        ) from None


def _to_yyyymmdd(value: str | date, name: str) -> str:
    """date/datetime 또는 YYYYMMDD/YYYY-MM-DD 문자열을 KIS 날짜 문자열(YYYYMMDD)로."""
    if isinstance(value, date):  # datetime 도 date 의 하위형이라 함께 처리됨
        return f"{value:%Y%m%d}"
    digits = str(value).strip().replace("-", "")
    if len(digits) == 8 and digits.isdigit():
        return digits
    raise KisUsageError(
        f"{name} 는 date 또는 YYYYMMDD/YYYY-MM-DD 문자열이어야 한다: {value!r}"
    )


def _today_kst() -> str:
    return f"{datetime.now(_KST):%Y%m%d}"


def _raise_if_error(resp: RawResponse) -> None:
    """KIS 응답이 실패(``rt_cd`` != 0)면 :class:`KisError` 로 올린다."""
    if not resp.ok:
        raise KisError(
            f"시세 조회 실패: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
