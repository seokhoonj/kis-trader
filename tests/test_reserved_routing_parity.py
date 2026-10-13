"""예약 지문의 slot-7 네임스페이스별 reconcile 라우팅 회귀 테스트."""

from unittest.mock import Mock

import pytest

from kis_trader import _order_engine
from kis_trader._order_engine import OrderEngine
from kis_trader.errors import KISUsageError
from kis_trader.order import ReservedOrderFingerprint


class _Store:
    def __init__(self, fingerprint: ReservedOrderFingerprint) -> None:
        self._fingerprint = fingerprint

    def fingerprint_for(self, client_order_id: str) -> ReservedOrderFingerprint:
        return self._fingerprint

    def report_for(self, client_order_id: str) -> object:
        return object()

    def binding_for(self, client_order_id: str) -> tuple[object, ReservedOrderFingerprint]:
        # change_order 는 (리포트, 지문)을 한 락으로 원자 스냅샷한다 -- 둘 다 있으면 쌍을 돌려준다.
        return self.report_for(client_order_id), self._fingerprint


def _reserved_fingerprint(token: str) -> ReservedOrderFingerprint:
    return ReservedOrderFingerprint(
        symbol="005930", side="buy", order_type="limit", quantity="1",
        limit_price="70000", end_date="", exchange=token,
    )


def _engine(fingerprint: ReservedOrderFingerprint) -> OrderEngine:
    return OrderEngine(
        transport=object(), store=_Store(fingerprint), environment="real",
        orderable=True, risk=None, require_account=lambda: ("12345678", "01"),
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("reserved", "domestic"),
        ("overseas-reserved", "overseas"),
        ("overseas-reserved-asia", "asia"),
    ],
)
def test_reserved_reconcile_keeps_namespace_routing(monkeypatch, token: str, expected: str) -> None:
    fingerprint = _reserved_fingerprint(token)
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
        "reconcile_us_reserved_order",
        routes["overseas"],
    )
    monkeypatch.setattr(
        _order_engine.overseas_reserved_orders_api,
        "reconcile_asia_reserved_order",
        routes["asia"],
    )
    engine = _engine(fingerprint)

    assert engine.reconcile("cid") is None
    for route, mock in routes.items():
        assert mock.call_count == (1 if route == expected else 0)


def test_change_order_cancel_routes_asia_reserved_order(monkeypatch) -> None:
    cancel = Mock(return_value=object())
    monkeypatch.setattr(
        _order_engine.overseas_reserved_orders_api, "cancel_asia_reserved_order", cancel,
    )
    engine = _engine(_reserved_fingerprint("overseas-reserved-asia"))

    result = engine.change_order(
        "cid", action="cancel", quantity=None, limit_price=None, request_id=None,
    )

    assert result is cancel.return_value
    cancel.assert_called_once()
    assert cancel.call_args.args[2] == "cid"
    assert cancel.call_args.kwargs == {
        "cano": "12345678", "product_code": "01", "environment": "real",
    }


@pytest.mark.parametrize(
    ("action", "quantity", "limit_price", "message"),
    [
        ("modify", None, 71000, "정정 미지원"),
        ("cancel", 1, None, "전량만 가능"),
    ],
)
def test_change_order_rejects_unsupported_asia_reserved_changes(
    monkeypatch, action, quantity, limit_price, message,
) -> None:
    cancel = Mock()
    monkeypatch.setattr(
        _order_engine.overseas_reserved_orders_api, "cancel_asia_reserved_order", cancel,
    )
    engine = _engine(_reserved_fingerprint("overseas-reserved-asia"))

    with pytest.raises(KISUsageError, match=message):
        engine.change_order(
            "cid", action=action, quantity=quantity, limit_price=limit_price, request_id=None,
        )
    cancel.assert_not_called()


@pytest.mark.parametrize("token", ["reserved", "overseas-reserved"])
def test_change_order_rejects_other_reserved_orders_cleanly(token: str) -> None:
    engine = _engine(_reserved_fingerprint(token))

    with pytest.raises(KISUsageError, match="cancel-reserved"):
        engine.change_order(
            "cid", action="cancel", quantity=None, limit_price=None, request_id=None,
        )
