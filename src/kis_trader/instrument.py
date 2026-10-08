"""심볼 -> 국내 시세 거래소 판별.

종목 핸들(:class:`~kis_trader.domestic.stock.DomesticStock`)이 심볼만으로 어느 시장을 부를지 정한다.
6자리 대문자 영숫자 단축코드는 국내(KRX 보드)로 본다 -- 주식·ETF·ETN(숫자)뿐 아니라 ELW·
신주인수권 등 문자가 섞인 코드도 KRX 상장이다. 그 밖(이름·해외 심볼 등)은 아직 지원하지 않아
명시적으로 거부한다. 국내 다른 거래소(NXT/통합)를 쓰려면 ``exchange=`` 로 지정한다.
"""

from __future__ import annotations

import re
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
