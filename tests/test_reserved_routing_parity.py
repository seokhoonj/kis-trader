"""예약 지문의 slot-7 네임스페이스별 reconcile 라우팅 회귀 테스트."""

from unittest.mock import Mock

import pytest

from kis_trader import _order_engine
from kis_trader._order_engine import OrderEngine
from kis_trader.order import ReservedOrderFingerprint


class _Store:
    def __init__(self, fingerprint: ReservedOrderFingerprint) -> None:
        self._fingerprint = fingerprint

    def fingerprint_for(self, client_order_id: str) -> ReservedOrderFingerprint:
        return self._fingerprint


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("reserved", "domestic"),
        ("overseas-reserved", "overseas"),
        ("overseas-reserved-asia", "asia"),
    ],
)
def test_reserved_reconcile_keeps_namespace_routing(monkeypatch, token: str, expected: str) -> None:
    fingerprint = ReservedOrderFingerprint(
        symbol="005930", side="buy", order_type="limit", quantity="1",
        limit_price="70000", end_date="", exchange=token,
    )
    routes = {
        "domestic": Mock(return_value=None),
        "overseas": Mock(return_value=None),
        "asia": Mock(return_value=None),
    }
    monkeypatch.setattr(
        _order_engine.reserved_orders_api, "reconcile_reserved_order", routes["domestic"],
    )
    monkeypatch.setattr(
        _order_engine.overseas_reserved_orders_api,
        "reconcile_overseas_reserved_order",
        routes["overseas"],
    )
    monkeypatch.setattr(
        _order_engine.overseas_reserved_orders_api,
        "reconcile_asia_reserved_order",
        routes["asia"],
    )
    engine = OrderEngine(
        transport=object(), store=_Store(fingerprint), environment="real",
        orderable=True, risk=None, require_account=lambda: ("12345678", "01"),
    )

    assert engine.reconcile("cid") is None
    for route, mock in routes.items():
        assert mock.call_count == (1 if route == expected else 0)
