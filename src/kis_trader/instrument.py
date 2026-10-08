"""심볼 -> 국내 시세 거래소 판별.

종목 핸들(:class:`~kis_trader.domestic.stock.DomesticStock`)이 심볼만으로 어느 시장을 부를지 정한다.
6자리 대문자 영숫자 단축코드는 국내(KRX 보드)로 본다 -- 주식·ETF·ETN(숫자)뿐 아니라 ELW·
신주인수권 등 문자가 섞인 코드도 KRX 상장이다. 그 밖(이름·해외 심볼 등)은 아직 지원하지 않아
명시적으로 거부한다. 국내 다른 거래소(NXT/통합)를 쓰려면 ``exchange=`` 로 지정한다.

이 모듈은 또한 종목 마스터의 공개 DATA 타입을 정의한다: 해외 마스터 레코드/인덱스
(:class:`InstrumentRecord`/:class:`MasterIndex`)와 국내 상장목록/검색 인덱스
(:class:`DomesticListing`/:class:`DomesticListingIndex`), 그리고 그 Literal(:data:`SecurityType`/
:data:`DomesticMarket`/:data:`DomesticMarketFilter`). 파싱/다운로드/캐시는 내부 마스터 모듈이 담당하고 그
함수들이 이 타입을 만들어 돌려준다(공개 DATA 는 공개 모듈에 산다 -- Bar/OrderBook 과 같은 관례).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, get_args

from .errors import KISUsageError

#: 국내 KRX 단축코드 형태 -- 6자리 대문자 영숫자. 주식·ETF·ETN 은 숫자(``005930``),
#: ELW·신주인수권 등은 문자가 섞인다(``57LABS``). 이름(``삼성전자``)·해외 심볼(``AAPL``)은 안 맞는다.
_DOMESTIC_CODE_PATTERN = re.compile(r"[0-9A-Z]{6}")

AssetClass = Literal["stock", "derivative", "bond"]
Region = Literal["domestic", "overseas"]
#: 국내 주문 거래소 -- KRX(정규거래소)/NXT(대체거래소, ATS)/SOR(스마트 라우팅=통합; 시세의 UN 에 대응)
DomesticOrderExchange = Literal["KRX", "NXT", "SOR"]
#: 국내 시세 거래소 -- KRX(정규거래소)/NXT(대체거래소, ATS)/UN(통합호가=통합; 주문의 SOR 에 대응)
DomesticQuoteExchange = Literal["KRX", "NXT", "UN"]
#: 국내 지수선물 보드 시장구분 -- ""(KOSPI200)/MKI(미니코스피)/WKM(위클리월)/WKI(위클리목)/KQI(코스닥150).
FuturesBoardMarket = Literal["", "MKI", "WKM", "WKI", "KQI"]

#: 멤버십 검증용 frozenset -- 공개 Literal 에서 파생해 드리프트를 원천 차단한다(단일 출처).
_DOMESTIC_ORDER_EXCHANGES = frozenset(get_args(DomesticOrderExchange))
_DOMESTIC_QUOTE_EXCHANGES = frozenset(get_args(DomesticQuoteExchange))


def is_domestic_symbol(symbol: str) -> bool:
    """6자리 대문자 영숫자 단축코드면 국내(KRX)로 본다.

    KRX 단축코드는 주식·ETF·ETN(숫자 ``"005930"``)뿐 아니라 ELW·신주인수권 등 문자가 섞인
    코드(``"57LABS"``)도 있으며 전부 6자리 국내 상장이다. 이름(``"삼성전자"``, 비ASCII)이나
    해외 심볼(``"AAPL"``, 길이 불일치)은 여기서 ``False`` -- 국내 다른 거래소는 ``exchange=`` 로 준다.
    (형태가 같은 6자 대문자 해외 티커를 ``kis.domestic`` 에 잘못 주면 국내로 오인될 수 있으나 --
    ``kis.domestic`` 은 국내를 선언한 자리다 -- 서버가 조회에서 거부한다.)"""
    return _DOMESTIC_CODE_PATTERN.fullmatch(symbol) is not None


def resolve_domestic_quote_exchange(
    symbol: str, *, exchange: DomesticQuoteExchange | None = None,
) -> DomesticQuoteExchange:
    """심볼(과 선택적 ``exchange``)로 국내 시세 거래소를 정한다.

    ``exchange`` 를 명시하면 유효성(KRX/NXT/UN)을 여기서 검증해 잘못된 값을 조회 전에 거른다.
    없으면 6자리 대문자 영숫자 단축코드(주식·ETF·ELW 등)를 국내 KRX 로 본다. 해외 심볼은 여기서
    판별하지 않는다 -- 해외는 거래소코드(``exchange=``)로 만들거나 ``kis.overseas.stock(symbol)``
    이 마스터로 자동 해석한다.
    """
    if exchange is not None:
        if exchange not in _DOMESTIC_QUOTE_EXCHANGES:
            raise KISUsageError(f"지원하지 않는 국내 시세 거래소: {exchange!r} (KRX/NXT/UN).")
        return exchange
    if is_domestic_symbol(symbol):
        return "KRX"
    raise KISUsageError(
        f"국내 시장을 판별할 수 없는 심볼: {symbol!r} -- 국내 단축코드는 6자리 대문자 영숫자다"
        f"(주식·ETF 는 숫자 '005930', ELW 등은 문자 섞인 '57LABS'). 해외는 "
        f"kis.overseas.stock(symbol, exchange=...) 로 만들거나 심볼만 주면 마스터로 자동 해석한다."
    )


# ---------------------------------------------------------------------------
# 종목 마스터 DATA -- 심볼->거래소 해석(해외)과 이름검색(국내). 파싱/다운로드/캐시는
# `_internal/_masters` 가 담당하고 그 함수들이 아래 타입을 만들어 돌려준다(공개 DATA 는
# 공개 모듈에 산다 -- Bar/OrderBook 과 같은 관례).
# ---------------------------------------------------------------------------

#: 종목유형 코드(마스터 stis 컬럼) -> 이름.
SecurityType = Literal["index", "stock", "etf", "warrant"]

#: 국내 시장(2값 폐집합). 해외 exchange/currency 와 달리 KIS 국내는 KOSPI/KOSDAQ 뿐이라 Literal 로 닫는다.
DomesticMarket = Literal["KOSPI", "KOSDAQ"]
#: 국내 시장 필터 -- 두 시장 + ``"all"``(전체). :data:`DomesticMarket` 의 all 포함 버전으로,
#: 종목검색·투자자순매수·예상체결 등 시장으로 범위를 좁히는 조회가 공유한다(SideFilter 와 같은 bare/Filter 쌍).
DomesticMarketFilter = Literal["all", "KOSPI", "KOSDAQ"]


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    """해외 마스터 한 종목(불변). 심볼->거래소 해석과 통화/유형/이름 메타를 담는다."""

    symbol: str
    exchange: str                     # KIS 거래소코드(EXCD): NAS/NYS/AMS/...
    currency: str                     # 결제 통화(USD/HKD/JPY/...)
    security_type: SecurityType | str  # stock/etf/index/warrant (미지원 코드면 원값)
    korean_name: str
    english_name: str
    realtime_symbol: str              # 실시간 시세용 심볼(rsym)


class MasterIndex:
    """심볼 -> 거래소 해석 인덱스. 여러 시장 마스터를 합쳐 심볼로 :class:`InstrumentRecord` 를 찾는다.

    같은 심볼이 여러 거래소에 있으면 ``exchange`` 를 명시해야 한다(오조회 방지). 국내 6자리 코드는
    이 인덱스를 안 거친다(이 모듈의 :func:`is_domestic_symbol` 가 KRX 로 판별).
    """

    __slots__ = ("_by_symbol",)

    def __init__(self, records: Iterable[InstrumentRecord]) -> None:
        by_symbol: dict[str, list[InstrumentRecord]] = {}
        for record in records:
            by_symbol.setdefault(record.symbol, []).append(record)
        self._by_symbol = by_symbol

    def resolve(self, symbol: str, *, exchange: str | None = None) -> InstrumentRecord:
        """심볼(과 선택적 ``exchange``)로 마스터 레코드 하나를 찾는다.

        없으면/모호하면(여러 거래소) :class:`~kis_trader.errors.KISUsageError`. ``exchange`` 를 주면
        그 거래소로 좁힌다."""
        matches = self._by_symbol.get(symbol, [])
        if exchange is not None:
            matches = [record for record in matches if record.exchange == exchange]
        if not matches:
            hint = f" (거래소 {exchange!r})" if exchange is not None else ""
            raise KISUsageError(f"해외 마스터에서 심볼을 찾지 못했다: {symbol!r}{hint}.")
        exchanges = {record.exchange for record in matches}
        if len(exchanges) > 1:
            raise KISUsageError(
                f"심볼 {symbol!r} 이 여러 거래소에 있다: {sorted(exchanges)} "
                f"-- exchange= 로 지정하라."
            )
        return matches[0]


@dataclass(frozen=True, slots=True)
class DomesticListing:
    """국내 상장 종목 한 건(불변) -- 이름검색 결과. ``symbol`` 6자리 단축코드, ``name`` 한글종목명,
    ``market`` ``"KOSPI"``/``"KOSDAQ"``. 우선주(삼성전자우)·ETF 는 ``name`` 으로 구분한다."""

    symbol: str
    name: str
    market: DomesticMarket


class DomesticListingIndex:
    """국내 상장 종목 이름검색 인덱스(불변). KOSPI+KOSDAQ 마스터를 합쳐 이름/코드로 후보를 찾는다.

    이름은 모호할 수 있어("삼성전자"⊃"삼성전자우") **후보를 하나로 좁히지 않고 모두** 돌려준다 --
    호출자가 코드를 골라 :meth:`~kis_trader.domestic.namespace.DomesticNamespace.stock` 로 넘긴다
    (안전커널의 오확정 방지와 같은 결).
    """

    __slots__ = ("_listings",)

    def __init__(self, listings: Iterable[DomesticListing]) -> None:
        self._listings = tuple(listings)

    def search_stocks(self, query: str, *, market: DomesticMarketFilter = "all") -> list[DomesticListing]:
        """이름(부분/정확) 또는 6자리 코드로 후보를 찾아 돌려준다.

        매치 순서는 정확일치 -> 접두 -> 부분(정확이 먼저 오되 다건은 모두 포함한다). ``market`` 은
        ``"all"``/``"KOSPI"``/``"KOSDAQ"``. 빈 검색어/잘못된 market 은 :class:`~kis_trader.errors.
        KISUsageError`. 매치 없으면 빈 리스트."""
        text = query.strip()
        if not text:
            raise KISUsageError("검색어(query)가 비어 있다.")
        if market not in ("all", "KOSPI", "KOSDAQ"):
            raise KISUsageError(f"market 은 all/KOSPI/KOSDAQ 중 하나: {market!r}")
        is_code_query = text.isdigit()  # 숫자면 코드 역검색, 아니면 이름 검색
        exact_matches: list[DomesticListing] = []
        prefix_matches: list[DomesticListing] = []
        substring_matches: list[DomesticListing] = []
        for listing in self._listings:
            if market != "all" and listing.market != market:
                continue
            field = listing.symbol if is_code_query else listing.name
            if field == text:
                exact_matches.append(listing)
            elif field.startswith(text):
                prefix_matches.append(listing)
            elif not is_code_query and text in field:
                substring_matches.append(listing)
        return exact_matches + prefix_matches + substring_matches
