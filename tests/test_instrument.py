"""resolve_domestic_quote_exchange -- 국내 시세 거래소 판별과 fail-closed 분기.

국내 시세 핸들의 거래소를 이 함수가 정하므로, 잘못된 거래소/판별불가 심볼을 조회 전에
거부하는지 단위로 못 박는다.
"""

from __future__ import annotations

import pytest

from kis_trader.errors import KISUsageError
from kis_trader.instrument import is_domestic_symbol, resolve_domestic_quote_exchange


def test_resolve_domestic_quote_exchange_defaults_domestic_symbol_to_krx():
    assert resolve_domestic_quote_exchange("005930") == "KRX"


@pytest.mark.parametrize("exchange", ["KRX", "NXT", "UN"])
def test_resolve_domestic_quote_exchange_honors_valid_exchange(exchange):
    assert resolve_domestic_quote_exchange("005930", exchange=exchange) == exchange


def test_resolve_domestic_quote_exchange_rejects_invalid_exchange():
    with pytest.raises(KISUsageError):
        resolve_domestic_quote_exchange("005930", exchange="NASDAQ")  # type: ignore[arg-type]


def test_resolve_domestic_quote_exchange_rejects_unresolvable_symbol():
    with pytest.raises(KISUsageError):
        resolve_domestic_quote_exchange("AAPL")


def test_is_domestic_symbol_distinguishes_six_digit_codes():
    assert is_domestic_symbol("005930")
    assert not is_domestic_symbol("AAPL")
    assert not is_domestic_symbol("00593")   # 5자리 -- 국내 코드 아님


def test_is_domestic_symbol_accepts_alphanumeric_krx_codes():
    # ELW/신주인수권 등 단축코드는 6자리 대문자 영숫자다(예: 삼성전자 콜/풋 ELW "57LABS").
    assert is_domestic_symbol("57LABS")
    assert is_domestic_symbol("58J306")
    assert not is_domestic_symbol("57labs")   # 소문자 -- KRX 코드 아님
    assert not is_domestic_symbol("58j306")   # 숫자+소문자 혼합 -- 아님
    assert not is_domestic_symbol("삼성전자")   # 이름 -- 코드 아님
    assert not is_domestic_symbol("57LAB")     # 5자리
    assert not is_domestic_symbol("57LABS0")   # 7자리 -- 아님


def test_resolve_domestic_quote_exchange_routes_elw_code_to_krx():
    assert resolve_domestic_quote_exchange("57LABS") == "KRX"
