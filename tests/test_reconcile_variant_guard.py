"""reconcile 경로의 지문 변형(variant) 가드 회귀 테스트.

네 reconcile 엔진 함수(국내/해외 x 즉시/예약)는 저장소가 이 id 에 대해 **기대한 변형이 아닌**
지문을 돌려주면 :class:`KISError` 로 fail-closed 한다(내부 상태 불일치). 정상 경로에선
``client.orders.reconcile`` 이 변형별로 라우팅하므로 이 가드가 도달하지 않는다 -- 그래서 여기서는
엔진 함수를 직접 호출하고, 저장소에 어긋난 변형을 심어 가드가 서는지 못박는다. 이 그물이 없으면
훗날 라우팅 불변식을 깨는 리팩터가 이 방어선을 조용히 지워도 깨지는 테스트가 없다.

가드는 저장소 조회 직후·어떤 전송(transport) 호출보다도 앞이므로, 각 테스트는 KISError 를 확인하고
전송이 한 번도 일어나지 않았음을 함께 단언한다.
"""

from __future__ import annotations

import pytest

from kis_trader.domestic._engine import orders as domestic_orders
from kis_trader.domestic._engine import reserved_orders as domestic_reserved
from kis_trader.errors import KISError
from kis_trader.order import ImmediateOrderFingerprint, ReservedOrderFingerprint
from kis_trader.overseas._engine import orders as overseas_orders
from kis_trader.overseas._engine import reserved_orders as overseas_reserved
from kis_trader.store import OrderStore


class RecordingTransport:
    """호출되면 기록만 한다(가드가 전송 전에 서는지 확인용) -- 실제 요청은 나가면 안 된다."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        self.calls.append({"method": method, "path": path, "tr_id": tr_id})
        raise AssertionError("가드가 전송 전에 서야 한다 -- 이 경로는 실행되면 안 된다.")


def _immediate(exchange: str) -> ImmediateOrderFingerprint:
    return ImmediateOrderFingerprint(
        symbol="005930", side="buy", order_type="limit", quantity="1",
        limit_price="70000", stop_price="", time_in_force="day", exchange=exchange,
    )


def _reserved(exchange: str) -> ReservedOrderFingerprint:
    return ReservedOrderFingerprint(
        symbol="005930", side="buy", order_type="limit", quantity="1",
        limit_price="70000", end_date="", exchange=exchange,
    )


def _seed(fingerprint) -> tuple[OrderStore, RecordingTransport]:
    store = OrderStore()
    store.try_claim("cid", fingerprint)                 # in-flight 로 확보
    return store, RecordingTransport()


def test_domestic_immediate_reconcile_rejects_non_immediate_fingerprint():
    store, transport = _seed(_reserved("reserved"))
    with pytest.raises(KISError, match="즉시주문이 아니다"):
        domestic_orders.reconcile(
            transport, store, "cid", cano="1", product_code="01", environment="real")
    assert transport.calls == []                         # 전송 전에 fail-closed


def test_domestic_reserved_reconcile_rejects_non_reserved_fingerprint():
    store, transport = _seed(_immediate("XKRX"))
    with pytest.raises(KISError, match="예약주문이 아니다"):
        domestic_reserved.reconcile_reserved_order(
            transport, store, "cid", cano="1", product_code="01", environment="real")
    assert transport.calls == []


def test_overseas_immediate_reconcile_rejects_non_immediate_fingerprint():
    store, transport = _seed(_reserved("overseas-reserved"))
    with pytest.raises(KISError, match="즉시주문이 아니다"):
        overseas_orders.reconcile(
            transport, store, "cid", cano="1", product_code="01", environment="real")
    assert transport.calls == []


def test_overseas_reserved_reconcile_rejects_non_reserved_fingerprint():
    store, transport = _seed(_immediate("NAS"))
    with pytest.raises(KISError, match="예약주문이 아니다"):
        overseas_reserved.reconcile_overseas_reserved_order(
            transport, store, "cid", cano="1", product_code="01", environment="real")
    assert transport.calls == []
