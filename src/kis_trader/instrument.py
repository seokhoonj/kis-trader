"""심볼 -> 시장 판별.

종목 핸들(:class:`~kis_trader.domestic.stock.DomesticStock`)이 심볼만으로 어느 시장을 부를지 정한다.
6자리 숫자 심볼은 국내(KRX 보드)로 본다. 그 밖(영문 등 해외 심볼)은 아직 지원하지 않아
명시적으로 거부한다. 국내 다른 보드(NXT/통합)를 쓰려면 ``market=`` 로 지정한다.
"""

from __future__ import annotations

from typing import Literal

from .errors import KISUsageError

#: 국내 시장 보드. KIS 조건시장분류코드로는 KRX / NXT(넥스트레이드) / UN(통합).
DomesticBoard = Literal["KRX", "NXT", "UN"]

_DOMESTIC_BOARDS = frozenset(("KRX", "NXT", "UN"))


def is_domestic_symbol(symbol: str) -> bool:
    """6자리 숫자 심볼이면 국내(KRX)로 본다."""
    return symbol.isdigit() and len(symbol) == 6


def resolve_market(symbol: str, *, market: DomesticBoard | None = None) -> DomesticBoard:
    """심볼(과 선택적 ``market``)로 국내 시장 보드를 정한다.

    ``market`` 을 명시하면 유효성(KRX/NXT/UN)을 여기서 검증해 잘못된 값을 조회 전에 거른다.
    없으면 6자리 숫자 심볼을 국내 KRX 로 본다. 해외 심볼은 여기서 판별하지 않는다 -- 해외는
    거래소코드(``exchange=``)로 만들거나 ``kis.overseas.stock(symbol)`` 이 마스터로
    자동 해석한다.
    """
    if market is not None:
        if market not in _DOMESTIC_BOARDS:
            raise KISUsageError(f"지원하지 않는 시장 보드: {market!r} (KRX/NXT/UN).")
        return market
    if is_domestic_symbol(symbol):
        return "KRX"
    raise KISUsageError(
        f"국내 시장을 판별할 수 없는 심볼: {symbol!r} -- 국내는 6자리 숫자 코드다. "
        f"해외는 kis.overseas.stock(symbol, exchange=...) 로 만들거나 심볼만 주면 마스터로 자동 해석한다."
    )
