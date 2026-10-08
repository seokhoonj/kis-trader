"""공유 주문 커널 강화 테스트 -- pending-action 게이트(BC-2)와 가격형상 검증의 빌더 이동(BC-1).

BC-1: 정정=>0보다 큰 limit_price, 취소=>limit_price 없음 검증을 공유 코어(submit_change)에서
와이어 빌더로 옮긴다. 지정가 전용 자산(주식/해외)은 공유 헬퍼 reject_bad_change_price_shape 로
같은 규칙을 강제하며, 관측 동작은 바뀌지 않는다(기존 change 테스트 무변경 통과가 parity 게이트).

BC-2: 원주문에 결과 미확인(in-flight) 변경이 이미 있으면 새(다른 request_id) 변경을 거부한다 --
같은 원주문을 겨눈 두 번째 변경이 조용히 나가지 않게 한다.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader import _order_core
from kis_trader.domestic._engine import orders as orders_engine
from kis_trader.errors import KISUsageError
from kis_trader.order import ChangeActionFingerprint, Order
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.store import OrderStore

_KST = timezone(timedelta(hours=9))


def _now() -> datetime:
    return datetime(2026, 8, 17, 10, 0, tzinfo=_KST)


class _FakeTransport:
    """게이트/검증이 전송 전에 걸리는지 확인하는 가짜 전송 -- request 가 호출되면 실패로 본다."""

    def request(self, **kwargs: object) -> object:
        raise AssertionError("게이트가 전송 전에 걸려야 한다 -- transport.request 가 호출됐다.")


def _seed_completed(store: OrderStore, cid: str, order: Order) -> ExecutionReport:
    fp = order.fingerprint
    store.try_claim(cid, fp)
    report = ExecutionReport(client_order_id=cid, order_id="0000001", symbol=order.symbol,
                             side=order.side, status=OrderStatus.NEW, filled_quantity=Decimal(0),
                             average_price=None, recorded_at=_now(), organization_number="00950")
    store.record(report, fp)
    return report


def test_second_change_on_same_order_is_rejected(tmp_path):
    # 국내 주식 지문으로도 성립(자산 무관)
    store = OrderStore(tmp_path / "s.json", now=_now)
    order = Order.limit("005930", side="buy", quantity=Decimal(1), limit_price=Decimal(70000),
                        client_order_id="orig")
    _seed_completed(store, "orig", order)
    # 첫 취소를 in-flight 로 남긴다(change fingerprint 를 직접 claim):
    store.try_claim("req-1", ChangeActionFingerprint(
        original_client_order_id="orig", side="buy", order_type="limit", quantity="1",
        limit_price="", action="cancel", time_in_force="day",
        region="domestic", asset_class="stock", exchange="KRX"))
    with pytest.raises(KISUsageError, match="reconcile"):
        _order_core.submit_change(
            _FakeTransport(), store, original_client_order_id="orig", request_id="req-2",
            action="cancel", quantity=Decimal(1), limit_price=None,
            cano="12345678", product_code="01", environment="real",
            build_request=orders_engine._make_domestic_change_request)


def test_same_request_id_retry_is_not_treated_as_second_change(tmp_path):
    """같은 request_id 재시도는 pending 게이트가 아니라 기존 in-flight 경로로 거부돼야 한다
    (BC-2 는 다른 request_id 의 두 번째 변경만 겨냥) -- parity 보존."""
    store = OrderStore(tmp_path / "s.json", now=_now)
    order = Order.limit("005930", side="buy", quantity=Decimal(1), limit_price=Decimal(70000),
                        client_order_id="orig")
    _seed_completed(store, "orig", order)
    store.try_claim("req-1", ChangeActionFingerprint(
        original_client_order_id="orig", side="buy", order_type="limit", quantity="1",
        limit_price="", action="cancel", time_in_force="day",
        region="domestic", asset_class="stock", exchange="KRX"))
    with pytest.raises(KISUsageError, match="재전송하지"):
        _order_core.submit_change(
            _FakeTransport(), store, original_client_order_id="orig", request_id="req-1",
            action="cancel", quantity=Decimal(1), limit_price=None,
            cano="12345678", product_code="01", environment="real",
            build_request=orders_engine._make_domestic_change_request)


def test_domestic_modify_still_requires_positive_limit(tmp_path):
    """정정인데 가격이 없으면 빌더가 거부한다(코어에서 옮겨도 관측 동작 동일)."""
    store = OrderStore(tmp_path / "s.json", now=_now)
    order = Order.limit("005930", side="buy", quantity=Decimal(1), limit_price=Decimal(70000),
                        client_order_id="orig")
    _seed_completed(store, "orig", order)
    with pytest.raises(KISUsageError):
        _order_core.submit_change(
            _FakeTransport(), store, original_client_order_id="orig", request_id="r",
            action="modify", quantity=Decimal(1), limit_price=None,   # 정정인데 가격 없음
            cano="12345678", product_code="01", environment="real",
            build_request=orders_engine._make_domestic_change_request)


def test_domestic_cancel_still_rejects_a_price(tmp_path):
    """취소인데 가격을 주면 빌더가 거부한다(코어에서 옮겨도 관측 동작 동일)."""
    store = OrderStore(tmp_path / "s.json", now=_now)
    order = Order.limit("005930", side="buy", quantity=Decimal(1), limit_price=Decimal(70000),
                        client_order_id="orig")
    _seed_completed(store, "orig", order)
    with pytest.raises(KISUsageError):
        _order_core.submit_change(
            _FakeTransport(), store, original_client_order_id="orig", request_id="r",
            action="cancel", quantity=Decimal(1), limit_price=Decimal(70000),  # 취소인데 가격 있음
            cano="12345678", product_code="01", environment="real",
            build_request=orders_engine._make_domestic_change_request)


def test_overseas_engine_does_not_import_domestic_order_core():
    # P2-9 구조 불변식: 지역 중립 주문 코어(place/submit_change/_RECEIPT_DATE_KEY)는 _order_core 로
    # 분리됐다. overseas 엔진은 domestic._engine.orders(국내 전용 모듈)를 안쪽으로 import 하면 안 된다
    # -- 공유분은 _order_core 에서 가져온다(형제 패키지 간 안쪽 의존 금지).
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "kis_trader" / "overseas" / "_engine"
    offenders = [
        p.name for p in root.glob("*.py")
        if "domestic._engine.orders" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"overseas 엔진이 domestic._engine.orders 를 참조한다: {offenders}"


def test_order_core_place_and_submit_change_are_public_to_engine():
    # 코어가 제자리(_order_core)에 있고 엔진이 그걸 쓰는지 고정.
    from kis_trader import _order_core
    assert callable(_order_core.place) and callable(_order_core.submit_change)
    assert _order_core._RECEIPT_DATE_KEY == "_receipt_date"
