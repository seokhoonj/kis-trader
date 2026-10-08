"""멱등 replay 회귀 -- :func:`kis_trader._order_core.place`.

이미 완료된 ``client_order_id`` 를 다시 place 하면, 사전 리스크(라이브 현재가 조회, fail-closed)나
와이어 변환 같은 **실패 가능 게이트**를 타기 전에 저장소에서 완료 리포트를 그대로 돌려줘야 한다.
그러지 않으면 일시적 조회 실패(스로틀/장마감)가 replay 를 막아 호출자를 새 id 재제출(이중발주)로
밀어낼 수 있다. in-flight id 는 같은 이유로 reconcile 을 요구한다.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

_KST = timezone(timedelta(hours=9))

from kis_trader._order_core import place
from kis_trader.errors import KISUsageError
from kis_trader.order import Order
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.store import OrderStore


def _clock():
    return datetime(2026, 8, 31, 10, 0, tzinfo=_KST)


class _ExplodingTransport:
    def request(self, **_kw):
        raise AssertionError("완료 replay 는 와이어에 닿으면 안 된다")


def _never_builds(*_a, **_k):
    raise AssertionError("완료 replay 는 와이어 요청을 만들면 안 된다")


def _boom_risk(_order):
    raise KISUsageError("일시적 현재가 조회 실패(스로틀/장마감)")


def _order(cid: str) -> Order:
    return Order.limit("005930", side="buy", quantity=10, limit_price=70000,
                       region="domestic", asset_class="stock", exchange="KRX",
                       client_order_id=cid)


def test_completed_replay_returns_report_despite_failing_risk(tmp_path):
    order = _order("CID-DONE")
    report = ExecutionReport(
        client_order_id="CID-DONE", order_id="0001", symbol="005930", side="buy",
        status=OrderStatus.FILLED, filled_quantity=Decimal(10), average_price=Decimal(70000),
        recorded_at=_clock(),
    )
    with OrderStore(tmp_path / "s.json", now=_clock) as store:
        store.record(report, order.fingerprint)
        got = place(
            _ExplodingTransport(), store, order,
            cano="12345678", product_code="01", environment="paper",
            build_request=_never_builds, pre_trade_risk=_boom_risk,
        )
    # 저장된 완료 리포트를 그대로 replay -- 게이트 실패와 무관.
    assert got == report


def test_in_flight_replay_demands_reconcile_before_risk(tmp_path):
    order = _order("CID-FLY")
    with OrderStore(tmp_path / "s.json", now=_clock) as store:
        store.try_claim(order.client_order_id, order.fingerprint)  # in-flight 로 확보
        with pytest.raises(KISUsageError, match="reconcile"):
            place(
                _ExplodingTransport(), store, order,
                cano="12345678", product_code="01", environment="paper",
                build_request=_never_builds, pre_trade_risk=_boom_risk,
            )
