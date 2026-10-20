"""OrderStore.binding_for -- (리포트, 지문)의 원자 스냅샷(머니패스 TOCTOU 가드).

정정/취소는 잔량(지문 수량 - 리포트 체결량)을 (리포트, 지문) 쌍에서 계산해 와이어에 싣는다.
둘을 각각 읽으면 그 사이 동시 리바인드(정정)가 끼어들어 세대가 어긋난 쌍(옛 지문 + 새 리포트)을
볼 수 있다. binding_for 는 한 락으로 둘을 함께 읽어 쌍이 항상 같은 세대임을 보장한다.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader.errors import KISError, KISUsageError
from kis_trader.order import Order
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.store import Binding, OrderStore

_KST = timezone(timedelta(hours=9))
_CID = "20240101-bind01"


def _report(order_id: str) -> ExecutionReport:
    return ExecutionReport(
        client_order_id=_CID, order_id=order_id, symbol="005930", side="buy",
        status=OrderStatus.PENDING_REPLACE, filled_quantity=Decimal(0), average_price=None,
        recorded_at=datetime(2024, 6, 3, 10, 0, tzinfo=_KST),
    )


def _fingerprint(quantity: int):
    # client_order_id 를 _CID 로 고정해 record 가 _CID 아래에 바인딩하게 한다.
    return Order(symbol="005930", side="buy", order_type="limit", quantity=quantity,
                 limit_price=70000, client_order_id=_CID).fingerprint


# 두 세대: 주문번호 A<->수량 10, 주문번호 B<->수량 20 이 짝. 어긋난 쌍은 수량≠기대값으로 드러난다.
_GEN_A = Binding(_report("A"), _fingerprint(10))
_GEN_B = Binding(_report("B"), _fingerprint(20))
_EXPECTED_QTY = {"A": "10", "B": "20"}


def test_binding_for_none_when_unknown_or_in_flight_only():
    store = OrderStore()
    assert store.binding_for(_CID) is None            # 미기록
    store.try_claim(_CID, _fingerprint(10))           # in-flight (지문만, 리포트 없음)
    assert store.binding_for(_CID) is None            # 리포트 없으면 None (확정 안 됨)


def test_binding_for_returns_recorded_pair():
    store = OrderStore()
    store.record(_GEN_A.report, _GEN_A.fingerprint)
    binding = store.binding_for(_CID)
    assert binding is not None
    assert binding.report.order_id == "A"
    assert binding.fingerprint.quantity == "10"


def test_binding_for_reads_pair_atomically_under_concurrent_rebind():
    # 한 스레드가 cid 를 두 세대 사이로 반복 리바인드하는 동안, 다른 스레드가 binding_for 로
    # 읽는 쌍은 항상 같은 세대여야 한다(옛 지문 + 새 리포트 같은 찢긴 쌍이 없어야). binding_for 가
    # 한 락 스냅샷이 아니면(회귀) 찢긴 쌍이 관측돼 이 테스트가 깨진다.
    store = OrderStore()
    store.record(_GEN_A.report, _GEN_A.fingerprint)
    stop = threading.Event()
    torn: list[tuple[str, str]] = []
    start = threading.Barrier(2)

    def writer() -> None:
        start.wait()
        gen = _GEN_B
        for _ in range(20000):
            store.record(gen.report, gen.fingerprint)
            gen = _GEN_A if gen is _GEN_B else _GEN_B
        stop.set()

    def reader() -> None:
        start.wait()
        while not stop.is_set():
            binding = store.binding_for(_CID)
            if binding is None or binding.report.order_id is None:
                continue
            if binding.fingerprint.quantity != _EXPECTED_QTY[binding.report.order_id]:
                torn.append((binding.report.order_id, binding.fingerprint.quantity))

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    assert not torn, f"찢긴 (리포트, 지문) 쌍 관측: {torn[:5]}"


_SCOPE = {"cano": "synthetic", "product_code": "01", "environment": "paper"}


def test_scope_persist_equal_and_redacted_mismatch(tmp_path):
    path = tmp_path / "scope.json"
    with OrderStore(path) as store:
        store.bind_scope(**_SCOPE)
        before = path.read_bytes()
        store.bind_scope(**_SCOPE)
        assert path.read_bytes() == before
        for key, value in (("cano", "other-account"), ("product_code", "02"), ("environment", "real")):
            for adopt in (False, True):
                with pytest.raises(KISUsageError) as error:
                    store.bind_scope(**{**_SCOPE, key: value}, adopt_legacy=adopt)
                assert "synthetic" not in str(error.value) and "other-account" not in str(error.value)
                assert path.read_bytes() == before
    data = json.loads(path.read_text())
    assert data["schema_version"] == 12 and data["scope"] == _SCOPE
    with OrderStore(path) as reopened:
        reopened.bind_scope(**_SCOPE)
        with pytest.raises(KISUsageError):
            reopened.bind_scope(**{**_SCOPE, "environment": "real"})


@pytest.mark.parametrize("version", range(1, 12))
def test_scope_unknown_legacy_refusal_and_explicit_adoption_preserves_records(tmp_path, version):
    path = tmp_path / "legacy.json"
    with OrderStore(path, retention_days=0) as seed:
        seed.record(_GEN_A.report, _GEN_A.fingerprint)
        seed.try_claim("pending", _GEN_B.fingerprint)
    data = json.loads(path.read_text())
    data["schema_version"] = version
    if version < 11:
        data.pop("scope", None)
    else:
        data["scope"] = None
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with OrderStore(path) as store:
        assert path.read_bytes() == before
        with pytest.raises(KISUsageError):
            store.bind_scope(**_SCOPE)
        assert path.read_bytes() == before
        store.bind_scope(**_SCOPE, adopt_legacy=True)
        after = json.loads(path.read_text())
        for key in ("reports", "fingerprints", "in_flight"):
            assert after[key] == data[key]             # 오래된 완료 기록도 scope 저장 중에는 정리하지 않는다
    with OrderStore(path) as reopened:
        reopened.bind_scope(**_SCOPE)
        assert reopened.report_for(_CID) == _GEN_A.report
        assert reopened.is_in_flight("pending")


@pytest.mark.parametrize("scope", ["missing", {}, [], {**_SCOPE, "extra": "bad"},
    {**_SCOPE, "cano": ""}, {**_SCOPE, "product_code": 1}, {**_SCOPE, "environment": "invalid"}])
def test_scope_current_corruption_releases_constructor_lock(tmp_path, scope):
    path = tmp_path / "bad.json"
    data = {"schema_version": 11, "in_flight": {}, "reports": {}, "fingerprints": {}}
    if scope != "missing":
        data["scope"] = scope
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(KISError):
        OrderStore(path)
    assert path.read_bytes() == before
    data["scope"] = None
    path.write_text(json.dumps(data))
    with OrderStore(path) as store:
        store.bind_scope(**_SCOPE)


def test_scope_concurrent_first_binding_has_one_winner():
    store = OrderStore()
    barrier = threading.Barrier(2)
    results = []
    def bind(cano):
        barrier.wait(timeout=2)
        try:
            store.bind_scope(**{**_SCOPE, "cano": cano})
            results.append((cano, True))
        except KISUsageError:
            results.append((cano, False))
    threads = [threading.Thread(target=bind, args=(cano,)) for cano in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert sorted(success for _, success in results) == [False, True]
    winner = next(cano for cano, success in results if success)
    store.bind_scope(**{**_SCOPE, "cano": winner})


@pytest.mark.parametrize("after_replace", [False, True])
def test_scope_persistence_failure_closes_instance_until_reopen(tmp_path, monkeypatch, after_replace):
    import kis_trader.store as store_module
    path = tmp_path / "failure.json"
    store = OrderStore(path)
    if after_replace:
        monkeypatch.setattr(store_module, "_fsync_dir", lambda directory: (_ for _ in ()).throw(OSError("fsync")))
    else:
        monkeypatch.setattr(store_module.tempfile, "mkstemp", lambda **kwargs: (_ for _ in ()).throw(OSError("write")))
    with pytest.raises(OSError):
        store.bind_scope(**_SCOPE)
    with pytest.raises(KISError):
        store.try_claim("later", _GEN_A.fingerprint)
    with pytest.raises(KISError):
        store.bind_scope(**_SCOPE)
    monkeypatch.undo()
    with OrderStore(path) as reopened:
        reopened.bind_scope(**_SCOPE)


@pytest.mark.parametrize("version", [10, 11])
@pytest.mark.parametrize("missing", ["in_flight", "fingerprints", "reports"])
def test_scope_schema_does_not_loosen_v10_required_maps(tmp_path, version, missing):
    path = tmp_path / "maps.json"
    data = {"schema_version": version, "scope": None, "in_flight": {}, "fingerprints": {}, "reports": {}}
    del data[missing]
    path.write_text(json.dumps(data))
    with pytest.raises(KISError):
        OrderStore(path)


@pytest.mark.parametrize("values", [{"cano": ""}, {"product_code": ""}, {"environment": "invalid"}])
def test_scope_invalid_input_keeps_store_usable(tmp_path, values):
    path = tmp_path / "input.json"
    with OrderStore(path) as store:
        with pytest.raises(KISUsageError):
            store.bind_scope(**{**_SCOPE, **values})
        assert not path.exists()
        store.bind_scope(**_SCOPE)
        store.try_claim("still-open", _GEN_A.fingerprint)


def test_scope_equal_concurrent_bind_writes_once(tmp_path, monkeypatch):
    store = OrderStore(tmp_path / "same.json")
    original = store._save_locked
    writes = []
    def save(**kwargs):
        writes.append(True)
        return original(**kwargs)
    monkeypatch.setattr(store, "_save_locked", save)
    barrier = threading.Barrier(2)
    errors = []
    def bind():
        try:
            barrier.wait(timeout=2)
            store.bind_scope(**_SCOPE)
        except (KISError, threading.BrokenBarrierError) as error:
            errors.append(error)
    threads = [threading.Thread(target=bind) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert errors == []
        assert writes == [True]
    finally:
        store.close()


def test_scope_adopts_legacy_pending_without_fingerprint(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema_version": 1, "in_flight": ["old-pending"]}))
    with OrderStore(path) as store:
        with pytest.raises(KISUsageError):
            store.bind_scope(**_SCOPE)
        store.bind_scope(**_SCOPE, adopt_legacy=True)
    with OrderStore(path) as reopened:
        assert reopened.is_in_flight("old-pending")
        assert reopened.fingerprint_for("old-pending") is None


def test_scope_remains_bound_after_retention_removes_all_records(tmp_path):
    path = tmp_path / "pruned.json"
    with OrderStore(path) as store:
        store.bind_scope(**_SCOPE)
        store.record(_GEN_A.report, _GEN_A.fingerprint)
        assert store.report_for(_CID) is None
        with pytest.raises(KISUsageError):
            store.bind_scope(**{**_SCOPE, "cano": "other"}, adopt_legacy=True)
    with OrderStore(path) as reopened:
        reopened.bind_scope(**_SCOPE)
        with pytest.raises(KISUsageError):
            reopened.bind_scope(**{**_SCOPE, "cano": "other"})


def test_scope_standalone_unbound_claims_remain_compatible(tmp_path):
    path = tmp_path / "unbound.json"
    with OrderStore(path) as store:
        store.try_claim("unbound", _GEN_A.fingerprint)
        assert json.loads(path.read_text())["scope"] is None
        with pytest.raises(KISUsageError):
            store.bind_scope(**_SCOPE)
        assert store.is_in_flight("unbound")
        store.bind_scope(**_SCOPE, adopt_legacy=True)


@pytest.mark.parametrize("adoption", ["false", "true", 1, 0, None, [], {}])
@pytest.mark.parametrize("nonempty", [False, True])
def test_scope_adoption_requires_actual_boolean(tmp_path, adoption, nonempty):
    path = tmp_path / "attestation.json"
    with OrderStore(path) as store:
        if nonempty:
            store.try_claim("pending", _GEN_A.fingerprint)
        before = path.read_bytes() if path.exists() else None
        with pytest.raises(KISUsageError):
            store.bind_scope(**_SCOPE, adopt_legacy=adoption)
        assert (path.read_bytes() if path.exists() else None) == before
        assert store.is_in_flight("pending") is nonempty
        if nonempty:
            with pytest.raises(KISUsageError):
                store.bind_scope(**_SCOPE, adopt_legacy=False)
        store.bind_scope(**_SCOPE, adopt_legacy=True)
        store.bind_scope(**_SCOPE, adopt_legacy=False)
