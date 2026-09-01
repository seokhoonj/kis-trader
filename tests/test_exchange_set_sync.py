"""US 거래소 집합 동기화 -- order.py 의 구성시점 검증 집합이 _overseas 의 와이어시점 원장과 같은지 고정.

``order.py`` 는 ``_overseas`` 를 import 하지 못해(순환) 미국 거래소 집합(오버나이트/algo 가능)을 직접 든다.
그 주석은 "``_ORDER_EXCHANGE`` 의 US 그룹과 동일해야 한다"고 계약을 선언하지만, 이 테스트가 없으면
아무것도 그 계약을 집행하지 않는다 -- 한쪽에만 거래소를 추가하면 조용히 갈라진다(가용성 버그).
"""

from __future__ import annotations

from kis_trader.order import _ALGO_EXCHANGES, _OVERNIGHT_EXCHANGES
from kis_trader.overseas._engine.orders import _ALGO_MARKET, _ORDER_EXCHANGE


def _overseas_us_exchanges() -> frozenset[str]:
    """와이어 원장(``_ORDER_EXCHANGE``)에서 market == "US" 인 거래소코드 집합."""
    return frozenset(
        exchange for exchange, (_order_exchange, market) in _ORDER_EXCHANGE.items()
        if market == _ALGO_MARKET
    )


def test_overnight_exchange_set_matches_overseas_us_group():
    assert _OVERNIGHT_EXCHANGES == _overseas_us_exchanges()


def test_algo_exchange_set_matches_overseas_us_group():
    assert _ALGO_EXCHANGES == _overseas_us_exchanges()
