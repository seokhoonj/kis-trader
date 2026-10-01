"""지문(fingerprint) 결정성 -- 안전코어 dedup 의 핵심 불변식.

같은 논리적 주문은 시계·uuid·실행순서와 무관하게 **항상 같은** fingerprint 를 내야 한다. 이게 깨지면
재시작/재시도 때 멱등 replay 가 conflict 로 뒤집혀 dedup 이 무너진다. (``mint_client_order_id`` 는 새
멱등키라 clock+uuid 로 비결정이지만, replay 를 판정하는 것은 그 키가 아니라 이 fingerprint 다.)
"""

from __future__ import annotations

from decimal import Decimal

from kis_trader import Order


def test_identical_orders_have_equal_fingerprint_and_hash() -> None:
    a = Order(symbol="005930", side="buy", order_type="limit", quantity=10, limit_price=70000)
    b = Order(symbol="005930", side="buy", order_type="limit", quantity=10, limit_price=70000)
    assert a.fingerprint == b.fingerprint
    assert hash(a.fingerprint) == hash(b.fingerprint)


def test_fingerprint_canonicalizes_numeric_representation() -> None:
    # 와이어 정본(format_wire_decimal)으로 10 과 1E1 은 같은 지문이어야 한다(fingerprint docstring 계약).
    a = Order(symbol="005930", side="buy", order_type="limit", quantity=Decimal(10),
              limit_price=Decimal(70000))
    b = Order(symbol="005930", side="buy", order_type="limit", quantity=Decimal("1E1"),
              limit_price=Decimal(70000))
    assert a.fingerprint == b.fingerprint


def test_distinct_orders_have_distinct_fingerprint() -> None:
    buy = Order(symbol="005930", side="buy", order_type="limit", quantity=10, limit_price=70000)
    sell = Order(symbol="005930", side="sell", order_type="limit", quantity=10, limit_price=70000)
    assert buy.fingerprint != sell.fingerprint
