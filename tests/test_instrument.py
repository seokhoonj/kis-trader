"""resolve_market -- 국내 시장 보드 판별과 그 fail-closed 분기.

모든 국내 주문의 라우팅을 이 함수가 가르므로, 잘못된 보드/판별불가 심볼을 조회 전에
거부하는지(주문이 엉뚱한 거래소로 나가지 않게) 단위로 못 박는다.
"""

from __future__ import annotations

import pytest

from kis_trader.errors import KISUsageError
from kis_trader.instrument import is_domestic_symbol, resolve_market


def test_resolve_market_defaults_domestic_symbol_to_krx():
    assert resolve_market("005930") == "KRX"


@pytest.mark.parametrize("market", ["KRX", "NXT", "UN"])
def test_resolve_market_honors_valid_board(market):
    assert resolve_market("005930", market=market) == market


def test_resolve_market_rejects_invalid_board():
    with pytest.raises(KISUsageError):
        resolve_market("005930", market="NASDAQ")  # type: ignore[arg-type]


def test_resolve_market_rejects_unresolvable_symbol():
    with pytest.raises(KISUsageError):
        resolve_market("AAPL")


def test_is_domestic_symbol_distinguishes_six_digit_codes():
    assert is_domestic_symbol("005930")
    assert not is_domestic_symbol("AAPL")
    assert not is_domestic_symbol("00593")   # 5자리 -- 국내 코드 아님


def test_is_domestic_symbol_accepts_alphanumeric_krx_codes():
    # ELW/신주인수권 등 단축코드는 6자리 대문자 영숫자다(예: 삼성전자 콜/풋 ELW "57LABS").
    assert is_domestic_symbol("57LABS")
    assert is_domestic_symbol("58J306")
    assert not is_domestic_symbol("57labs")   # 소문자 -- KRX 코드 아님
    assert not is_domestic_symbol("삼성전자")   # 이름 -- 코드 아님
    assert not is_domestic_symbol("57LAB")     # 5자리


def test_resolve_market_routes_elw_code_to_krx():
    assert resolve_market("57LABS") == "KRX"
