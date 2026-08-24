"""심볼 -> 시장 판별.

종목 핸들(:class:`~kis_trader.domestic.stock.DomesticStock`)이 심볼만으로 어느 시장을 부를지 정한다.
6자리 대문자 영숫자 단축코드는 국내(KRX 보드)로 본다 -- 주식·ETF·ETN(숫자)뿐 아니라 ELW·
신주인수권 등 문자가 섞인 코드도 KRX 상장이다. 그 밖(이름·해외 심볼 등)은 아직 지원하지 않아
명시적으로 거부한다. 국내 다른 보드(NXT/통합)를 쓰려면 ``market=`` 로 지정한다.
"""

from __future__ import annotations

import re
from typing import Literal

from .errors import KISUsageError

#: 국내 KRX 단축코드 형태 -- 6자리 대문자 영숫자. 주식·ETF·ETN 은 숫자(``005930``),
#: ELW·신주인수권 등은 문자가 섞인다(``57LABS``). 이름(``삼성전자``)·해외 심볼(``AAPL``)은 안 맞는다.
_DOMESTIC_CODE = re.compile(r"[0-9A-Z]{6}")

#: 국내 시장 보드. KIS 조건시장분류코드로는 KRX / NXT(넥스트레이드) / UN(통합).
DomesticBoard = Literal["KRX", "NXT", "UN"]

_DOMESTIC_BOARDS = frozenset(("KRX", "NXT", "UN"))


def is_domestic_symbol(symbol: str) -> bool:
    """6자리 대문자 영숫자 단축코드면 국내(KRX)로 본다.

    KRX 단축코드는 주식·ETF·ETN(숫자 ``"005930"``)뿐 아니라 ELW·신주인수권 등 문자가 섞인
    코드(``"57LABS"``)도 있으며 전부 6자리 국내 상장이다. 이름(``"삼성전자"``, 비ASCII)이나
    해외 심볼(``"AAPL"``, 길이 불일치)은 여기서 ``False`` -- 국내 다른 보드는 ``market=`` 로 준다."""
    return _DOMESTIC_CODE.fullmatch(symbol) is not None


def resolve_market(symbol: str, *, market: DomesticBoard | None = None) -> DomesticBoard:
    """심볼(과 선택적 ``market``)로 국내 시장 보드를 정한다.

    ``market`` 을 명시하면 유효성(KRX/NXT/UN)을 여기서 검증해 잘못된 값을 조회 전에 거른다.
    없으면 6자리 대문자 영숫자 단축코드(주식·ETF·ELW 등)를 국내 KRX 로 본다. 해외 심볼은 여기서
    판별하지 않는다 -- 해외는 거래소코드(``exchange=``)로 만들거나 ``kis.overseas.stock(symbol)``
    이 마스터로 자동 해석한다.
    """
    if market is not None:
        if market not in _DOMESTIC_BOARDS:
            raise KISUsageError(f"지원하지 않는 시장 보드: {market!r} (KRX/NXT/UN).")
        return market
    if is_domestic_symbol(symbol):
        return "KRX"
    raise KISUsageError(
        f"국내 시장을 판별할 수 없는 심볼: {symbol!r} -- 국내 단축코드는 6자리 대문자 영숫자다"
        f"(주식·ETF 는 숫자 '005930', ELW 등은 문자 섞인 '57LABS'). 해외는 "
        f"kis.overseas.stock(symbol, exchange=...) 로 만들거나 심볼만 주면 마스터로 자동 해석한다."
    )
