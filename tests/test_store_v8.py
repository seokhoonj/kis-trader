import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader.errors import KISError
from kis_trader.order import ChangeActionFingerprint, Order
from kis_trader.store import _SCHEMA_VERSION, Claimed, InFlight, OrderStore, PriorChangeInFlight

_KST = timezone(timedelta(hours=9))


def _clock():
    return datetime(2026, 8, 17, 19, 30, tzinfo=_KST)


def _fp():
    return Order(symbol="101S03", side="buy", order_type="limit", quantity=Decimal(1),
                 limit_price=Decimal("400.00"), asset_class="derivative", session="night",
                 derivative_item="01", client_order_id="cid-1").fingerprint


def test_claim_records_claim_time(tmp_path):
    with OrderStore(tmp_path / "s.json", now=_clock) as store:
        assert isinstance(store.try_claim("cid-1", _fp()), Claimed)
        assert store.claim_time_for("cid-1") == _clock().isoformat()


def test_v8_persist_and_reload_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    with OrderStore(p, now=_clock) as store:
        store.try_claim("cid-1", _fp())
    data = json.loads(p.read_text())
    assert data["schema_version"] == _SCHEMA_VERSION   # v8 에서 도입된 in_flight dict 형식이 대상
    assert data["in_flight"] == {"cid-1": _clock().isoformat()}
    with OrderStore(p, now=_clock) as reloaded:
        assert reloaded.is_in_flight("cid-1")
        assert reloaded.claim_time_for("cid-1") == _clock().isoformat()


def test_v7_file_migrates_inflight_list_to_dict(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"schema_version": 7, "in_flight": ["old-cid"],
                             "fingerprints": {}, "reports": {}}))
    with OrderStore(p, now=_clock) as store:
        assert store.is_in_flight("old-cid")
        assert store.claim_time_for("old-cid") == ""   # 구 레코드 = 시각 미상 -> 폴백 앵커


def test_in_flight_change_for_finds_pending_change(tmp_path):
    with OrderStore(tmp_path / "s.json", now=_clock) as store:
        change_fp = ChangeActionFingerprint(
            original_client_order_id="orig-1", side="buy", order_type="limit",
            quantity="1", limit_price="400.00", action="cancel", time_in_force="day",
            region="domestic", asset_class="derivative", exchange="KRX")
        store.try_claim("req-1", change_fp)
        assert store.in_flight_change_for("orig-1") == "req-1"
        assert store.in_flight_change_for("orig-2") is None


def _change_fp(*, original, action="cancel", request_qty="1"):
    return ChangeActionFingerprint(
        original_client_order_id=original, side="buy", order_type="limit",
        quantity=request_qty, limit_price="400.00", action=action, time_in_force="day",
        region="domestic", asset_class="derivative", exchange="KRX")


def test_try_claim_change_rejects_second_change_on_same_original_atomically(tmp_path):
    """같은 원주문을 겨눈 다른 request_id 의 변경은 한 번에 하나만 claim 된다(TOCTOU 경합 차단)."""
    with OrderStore(tmp_path / "s.json", now=_clock) as store:
        first = store.try_claim_change("req-A", _change_fp(original="orig-1"),
                                       original_client_order_id="orig-1")
        second = store.try_claim_change("req-B", _change_fp(original="orig-1"),
                                        original_client_order_id="orig-1")
        assert isinstance(first, Claimed)
        assert isinstance(second, PriorChangeInFlight)
        assert second.other_request_id == "req-A"


def test_try_claim_change_same_request_id_retry_is_idempotent_not_rejected(tmp_path):
    """같은 request_id 재시도는 PriorChangeInFlight 가 아니라 멱등 InFlight 로 처리된다(재전송 금지)."""
    with OrderStore(tmp_path / "s.json", now=_clock) as store:
        fp = _change_fp(original="orig-1")
        assert isinstance(store.try_claim_change("req-A", fp, original_client_order_id="orig-1"), Claimed)
        assert isinstance(store.try_claim_change("req-A", fp, original_client_order_id="orig-1"), InFlight)


def test_try_claim_change_allows_change_on_a_different_original(tmp_path):
    """다른 원주문을 겨눈 변경은 서로를 막지 않는다."""
    with OrderStore(tmp_path / "s.json", now=_clock) as store:
        assert isinstance(store.try_claim_change("req-A", _change_fp(original="orig-1"),
                                                 original_client_order_id="orig-1"), Claimed)
        assert isinstance(store.try_claim_change("req-B", _change_fp(original="orig-2"),
                                                 original_client_order_id="orig-2"), Claimed)


def test_try_claim_is_atomic_under_concurrency(tmp_path):
    # 핵심 이중체결 방지 불변식: 여러 스레드가 동시에 같은 id 를 claim 하면 정확히 하나만 Claimed,
    # 나머지는 InFlight. 락 밖으로 check/claim 이 새면(회귀) 둘 이상 Claimed 가 되어 이 테스트가 깨진다.
    with OrderStore(tmp_path / "s.json", now=_clock) as store:
        fp = _fp()
        barrier = threading.Barrier(16)
        results: list[object] = []
        results_lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            outcome = store.try_claim("same-id", fp)
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5.0)
        claimed = [r for r in results if isinstance(r, Claimed)]
        in_flight = [r for r in results if isinstance(r, InFlight)]
        assert len(claimed) == 1              # 정확히 하나만 전송 권한을 얻는다
        assert len(in_flight) == 15           # 나머지는 재조회 요구(재전송 금지)


def test_second_store_on_same_path_is_rejected(tmp_path):
    # 프로세스 간 이중체결 방지: 같은 경로의 두 번째 열기는 단일 writer flock 으로 거부된다.
    path = tmp_path / "s.json"
    with OrderStore(path, now=_clock), pytest.raises(KISError, match="이미 열려"):
        OrderStore(path, now=_clock)
