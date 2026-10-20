"""라이브 해외주문 세션에서 드러난 세 수정의 회귀 테스트.

- #1 정정·취소가 '거래소 미접수'로 거부되면 재시도 가능한 하위 타입
  (:class:`OrderNotAcceptedYetError`)으로 매핑한다(``OrderRejectedError`` 하위 -> 하위호환).
- #2 ``config.order_store_path`` 와 CLI ``build_client`` 가 영속 :class:`OrderStore` 를 배선해
  프로세스가 바뀌어도 취소/재조회/dedup 이 가능하게 한다(라이브러리 기본은 인메모리 유지).
- #3+5 해외는 부분 취소·정정 메커니즘이 없어(라이브 확인), 잔량 전체가 아닌 변경은 와이어 전에
  :class:`KISUsageError` 로 거부한다(국내는 이 게이트에 안 걸린다).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import types
from argparse import Namespace

import pytest

from kis_trader import KISClient
from kis_trader.config import order_store_path
from kis_trader.errors import KISUsageError, OrderNotAcceptedYetError, OrderRejectedError
from kis_trader.transport import RawResponse

_ORDER_CASH = "/uapi/domestic-stock/v1/trading/order-cash"
_ORDER_CHANGE = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
_OVERSEAS_ORDER = "/uapi/overseas-stock/v1/trading/order"
_OVERSEAS_CHANGE = "/uapi/overseas-stock/v1/trading/order-rvsecncl"


class FakeTransport:
    """경로별 순차 응답을 돌려주는 가짜 전송. ``request_count`` 로 와이어 접촉 횟수를 센다."""

    def __init__(self, *, by_path=None):
        self.by_path = by_path or {}
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    @property
    def request_count(self) -> int:
        return len(self.calls)

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body,
                               "params": params, "idempotent": idempotent})
        outcome = self.by_path.get(path)
        if isinstance(outcome, list):
            outcome = outcome.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert outcome is not None, f"FakeTransport 에 {path} 응답을 줘야 한다"
        return outcome


def _ack(odno="0000117057"):
    return RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                       body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": odno,
                                        "ORD_TMD": "121052"}})


def _client(transport, **kw):
    kw.setdefault("environment", "real")
    return KISClient(app_key="k", app_secret="s", account="12345678-01", transport=transport, **kw)


def _seeded_domestic(reject=None):
    """국내 매수를 접수시켜 ``cid-1`` 원주문을 심고, 정정취소 응답을 지정한 클라이언트."""
    change_resp = reject if reject is not None else _ack()
    fake = FakeTransport(by_path={_ORDER_CASH: _ack(), _ORDER_CHANGE: change_resp})
    kis = _client(fake)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="cid-1")
    fake.calls.clear()   # seed 이후의 와이어 접촉만 센다
    return kis, fake


def _seeded_overseas():
    """해외 매수(잔량 3)를 접수시켜 ``cid-1`` 원주문을 심은 클라이언트."""
    fake = FakeTransport(by_path={_OVERSEAS_ORDER: _ack("0000123456"),
                                  _OVERSEAS_CHANGE: _ack("0000123456")})
    kis = _client(fake)
    kis.overseas.stock("AAPL", exchange="NAS").buy(
        quantity=3, limit_price="150.00", client_order_id="cid-1")
    fake.calls.clear()
    return kis, fake


# --- #1 미접수 거부 -> 재시도 가능한 하위 타입 --------------------------------

@pytest.mark.parametrize("route", ["cancel", "modify"])
def test_change_not_yet_accepted_maps_to_retryable_error(route):
    # 정정·취소 두 경로 모두 '거래소 미접수' 거부를 재시도 가능한 하위 타입으로 매핑한다.
    reject = RawResponse(rt_cd="1", msg_cd="APBK0919",
                         msg1="거래소 미접수로 정정취소주문이 불가합니다", body={})
    kis, _ = _seeded_domestic(reject=reject)
    change = (lambda: kis.orders.cancel("cid-1", request_id="c-1")) if route == "cancel" \
        else (lambda: kis.orders.modify("cid-1", limit_price=71000, request_id="c-1"))
    with pytest.raises(OrderNotAcceptedYetError) as ei:
        change()
    assert isinstance(ei.value, OrderRejectedError)   # 하위호환: 기존 except 가 그대로 잡는다


def test_place_not_yet_accepted_text_stays_orderrejected():
    # 매핑은 정정·취소 경로에만 한정된다 -- 발주(place) 거부는 msg1 에 '미접수'가 있어도 평범한
    # OrderRejectedError 로 남아야 한다(재시도 가능 하위 타입으로 승격하지 않는다).
    reject = RawResponse(rt_cd="1", msg_cd="APBK0919", msg1="거래소 미접수 상태입니다", body={})
    fake = FakeTransport(by_path={_ORDER_CASH: reject})
    kis = _client(fake)
    with pytest.raises(OrderRejectedError) as ei:
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="cid-9")
    assert not isinstance(ei.value, OrderNotAcceptedYetError)


def test_cancel_other_rejection_stays_orderrejected():
    reject = RawResponse(rt_cd="1", msg_cd="APBK1234", msg1="주문가능금액 부족", body={})
    kis, _ = _seeded_domestic(reject=reject)
    with pytest.raises(OrderRejectedError) as ei:
        kis.orders.cancel("cid-1", request_id="c-1")
    assert not isinstance(ei.value, OrderNotAcceptedYetError)


# --- #3+5 해외 부분 취소·정정 거부(국내 미차단) ------------------------------

def test_overseas_partial_cancel_rejected():
    kis, fake = _seeded_overseas()
    with pytest.raises(KISUsageError, match="부분"):
        kis.orders.cancel("cid-1", quantity=1, request_id="x-1")
    assert fake.request_count == 0        # 와이어에 닿기 전에 거부


def test_overseas_partial_modify_rejected():
    kis, fake = _seeded_overseas()
    with pytest.raises(KISUsageError, match="부분"):
        kis.orders.modify("cid-1", limit_price="151.00", quantity=1, request_id="x-2")
    assert fake.request_count == 0


def test_overseas_full_cancel_still_works():
    kis, fake = _seeded_overseas()
    kis.orders.cancel("cid-1", request_id="x-1")   # quantity 생략 -> 전량, 전송된다
    assert fake.request_count == 1


def test_domestic_partial_cancel_not_blocked_by_this_gate():
    # 국내는 이 게이트에 안 걸린다(부분취소는 국내 슬라이스 소관, 여기선 통과해 와이어로 나간다).
    kis, fake = _seeded_domestic()
    kis.orders.cancel("cid-1", quantity=5, request_id="c-1")
    assert fake.request_count == 1


# --- #2 order_store_path + CLI 영속 store -------------------------------------

def test_order_store_path_is_per_env_and_account(tmp_path):
    p1 = order_store_path(account="12345678-01", environment="real", override=tmp_path)
    p2 = order_store_path(account="12345678-01", environment="paper", override=tmp_path)
    p3 = order_store_path(account="87654321-03", environment="real", override=tmp_path)
    assert p1 != p2 and p1 != p3
    assert "12345678" not in str(p1)      # 계정번호 노출 금지(해시)
    assert str(p1).endswith(".json")


def test_cli_build_client_uses_persistent_store(monkeypatch, tmp_path):
    from kis_trader import KISConfig
    from kis_trader.cli.context import build_client
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for var in [name for name in __import__("os").environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)
    KISConfig(profile="general", app_key="k", app_secret="s",
              account="12345678-01", environment="paper").save()
    kis = build_client(Namespace(profile="general", account=None))
    assert kis._store._path is not None   # 영속(디스크) 저장소가 주입됐다


def test_library_default_store_is_in_memory():
    # 라이브러리 기본(store 미지정)은 인메모리 -- CLI 영속화 수정이 기대는 parity 보장.
    kis = _client(FakeTransport())
    assert kis._store._path is None


def test_order_store_path_default_is_in_state_dir(monkeypatch, tmp_path):
    # 재생성 불가한 영속 상태라 캐시가 아니라 XDG state 트리 아래 둔다.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    p = order_store_path(account="12345678-01", environment="real")
    assert str(p).startswith(str(tmp_path / "state"))
    assert str(tmp_path / "cache") not in str(p)
    assert p.parent.name == "orders"


@pytest.mark.parametrize("legacy_kind", ["empty", "journal", "lock"])
def test_order_store_path_refuses_actual_legacy_directory(monkeypatch, tmp_path, legacy_kind):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    old = tmp_path / "cache" / "kis-trader" / "orders"
    old.mkdir(parents=True)
    if legacy_kind != "empty":
        (old / ("other.json" if legacy_kind == "journal" else "other.json.lock")).write_bytes(b"synthetic")
    before = {entry.name: entry.read_bytes() for entry in old.iterdir()}
    with pytest.raises(KISUsageError, match="migrate-orders.*--offline"):
        order_store_path(account="synthetic-01", environment="paper")
    assert old.is_dir() and not old.is_symlink()
    assert {entry.name: entry.read_bytes() for entry in old.iterdir()} == before
    assert not (tmp_path / "state" / "kis-trader" / "orders").exists()


def test_order_store_fresh_setup_uses_shared_directory_alias(monkeypatch, tmp_path):
    from kis_trader.store import OrderStore
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = order_store_path(account="synthetic-01", environment="paper")
    alias = tmp_path / "cache" / "kis-trader" / "orders"
    assert alias.is_symlink() and alias.samefile(path.parent)
    with OrderStore(path=path):
        assert path.with_suffix(".json.lock").samefile(alias / path.with_suffix(".json.lock").name)
    assert order_store_path(account="other-01", environment="real").parent == path.parent


@pytest.mark.parametrize("layout", ["state-only", "both-empty", "both-data", "broken", "wrong", "state-link", "cache-file"])
def test_order_store_unknown_layout_refuses_without_changing_bytes(monkeypatch, tmp_path, layout):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    state = tmp_path / "state" / "kis-trader" / "orders"
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    cache.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    if layout == "state-link":
        state.symlink_to(tmp_path / "unknown", target_is_directory=True)
    elif layout in {"state-only", "both-empty", "both-data"}:
        state.mkdir()
        if layout != "both-empty":
            (state / "synthetic.json").write_bytes(b"state-bytes")
    if layout.startswith("both"):
        cache.mkdir()
        if layout == "both-data":
            (cache / "synthetic.json").write_bytes(b"cache-bytes")
    elif layout in {"broken", "wrong"}:
        target = tmp_path / "unknown"
        if layout == "wrong":
            target.mkdir()
        cache.symlink_to(target, target_is_directory=True)
    elif layout == "cache-file":
        cache.write_bytes(b"cache-file")
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(KISUsageError):
        order_store_path(account="synthetic-01", environment="paper")
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert all(after[k] == value for k, value in before.items())
    assert not any(p.suffix == ".json" and str(p.relative_to(tmp_path)) not in before for p in tmp_path.rglob("*"))


def _historical_store_module():
    """고정된 과거 버전의 실제 락·교체 코드를 현재 타입과 함께 실행한다."""
    from pathlib import Path

    if not (Path(__file__).resolve().parent.parent / ".git").exists():
        pytest.skip("historical-source proof requires checkout git history; sdist omits it")
    source = subprocess.check_output(
        ["git", "show", "3becf7a^:src/kis_trader/store.py"], text=True,
    )
    module = types.ModuleType("kis_trader._historical_layout_test")
    module.__package__ = "kis_trader"
    sys.modules[module.__name__] = module
    exec(compile(source, "historical-store.py", "exec"), module.__dict__)  # noqa: S102 -- fixed repository history
    return module


def test_actual_historical_writer_paused_before_replace_is_not_moved(monkeypatch, tmp_path):
    from kis_trader import Order, store
    if store.fcntl is None:
        pytest.skip("historical POSIX writer requires fcntl")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    old = tmp_path / "cache" / "kis-trader" / "orders" / "other.json"
    historical = _historical_store_module()
    paused, release = threading.Event(), threading.Event()
    failures = []
    original_replace = os.replace

    def replace(source, destination):
        if destination == old:
            paused.set()
            assert release.wait(5)
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace)

    def writer():
        try:
            with historical.OrderStore(path=old) as journal:
                journal.try_claim("synthetic", Order.market("005930", side="buy", quantity=1).fingerprint)
        except BaseException as error:  # noqa: BLE001 -- publish worker failures to joining test
            failures.append(error)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert paused.wait(5)
        lock = old.with_suffix(".json.lock")
        identity = lock.stat().st_ino
        with pytest.raises(KISUsageError, match="migrate-orders"):
            order_store_path(account="synthetic-01", environment="paper")
        assert lock.stat().st_ino == identity
        assert not (tmp_path / "state" / "kis-trader" / "orders").exists()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not failures
    assert old.is_file()
    with historical.OrderStore(path=old) as journal:
        assert isinstance(journal.try_claim("synthetic", Order.market("005930", side="buy", quantity=1).fingerprint), historical.InFlight)


def test_old_start_wins_fresh_alias_race_preserves_old_authority(monkeypatch, tmp_path):
    from pathlib import Path

    from kis_trader import Order, store
    if store.fcntl is None:
        pytest.skip("historical POSIX writer requires fcntl")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    original = Path.symlink_to
    historical = _historical_store_module()

    def race(path, target, target_is_directory=False):
        if path == cache:
            with historical.OrderStore(path=cache / "old.json") as journal:
                journal.try_claim("synthetic", Order.market("005930", side="buy", quantity=1).fingerprint)
        return original(path, target, target_is_directory=target_is_directory)

    monkeypatch.setattr(Path, "symlink_to", race)
    with pytest.raises(KISUsageError, match="migrate-orders"):
        order_store_path(account="synthetic-01", environment="paper")
    assert (cache / "old.json").is_file() and not cache.is_symlink()
    assert list((tmp_path / "state" / "kis-trader" / "orders").iterdir()) == []


def test_alias_shares_actual_historical_lock_and_replace(monkeypatch, tmp_path):
    from kis_trader import Order, store
    from kis_trader.errors import KISError, UnsupportedSchemaVersionError
    if store.fcntl is None:
        pytest.skip("historical POSIX writer requires fcntl")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = order_store_path(account="synthetic-01", environment="paper")
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    old = cache / path.name
    historical = _historical_store_module()
    fingerprint = Order.market("005930", side="buy", quantity=1).fingerprint
    with historical.OrderStore(path=old) as journal:
        journal.try_claim("synthetic", fingerprint)
        before = old.read_bytes()
        with pytest.raises(KISError):
            store.OrderStore(path=path)
        journal.try_claim("second", fingerprint)
        assert cache.is_symlink() and old.samefile(path)
    current_bytes = path.read_bytes()
    assert before != current_bytes
    with store.OrderStore(path=path), pytest.raises(KISError):
        historical.OrderStore(path=old)
    assert path.read_bytes() == current_bytes
    with historical.OrderStore(path=old) as journal:
        assert isinstance(journal.try_claim("synthetic", fingerprint), historical.InFlight)
    with store.OrderStore(path=path) as journal:
        journal.try_claim("new", fingerprint)
    with pytest.raises(UnsupportedSchemaVersionError):
        historical.OrderStore(path=old)
    assert old.samefile(path)


def test_order_store_rejects_state_inside_cache_cleanup_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "cache" / "state"))
    with pytest.raises(KISUsageError, match="캐시 정리 트리"):
        order_store_path(account="synthetic-01", environment="paper")
    assert not (tmp_path / "cache").exists()


def test_order_store_cache_cleanup_preserves_state_but_requires_offline_repair(monkeypatch, tmp_path):
    from kis_trader import Order, store
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = order_store_path(account="synthetic-01", environment="paper")
    with store.OrderStore(path=path) as journal:
        journal.try_claim("synthetic", Order.market("005930", side="buy", quantity=1).fingerprint)
    before = path.read_bytes()
    (tmp_path / "cache" / "kis-trader" / "orders").unlink()
    assert path.read_bytes() == before
    with pytest.raises(KISUsageError, match="migrate-orders"):
        order_store_path(account="synthetic-01", environment="paper")
    assert path.read_bytes() == before


def test_native_no_fcntl_fresh_layout_does_not_require_symlink(monkeypatch, tmp_path):
    from pathlib import Path

    from kis_trader import _order_store_layout as layout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # Platform-selection unit check only: the actual process lock is still POSIX here.
    monkeypatch.setattr(layout, "_requires_legacy_alias", lambda: False)
    monkeypatch.setattr(Path, "symlink_to", lambda *args, **kwargs: pytest.fail("native branch must not need directory links"))
    path = order_store_path(account="synthetic-01", environment="paper")
    assert path.parent.is_dir()
    assert order_store_path(account="synthetic-01", environment="paper") == path
    assert not (tmp_path / "cache").exists()


def test_fresh_alias_race_refuses_competing_writer_without_git(monkeypatch, tmp_path):
    from pathlib import Path

    from kis_trader import Order, store
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    original = Path.symlink_to

    def race(path, target, target_is_directory=False):
        if path == cache:
            with store.OrderStore(path=cache / "competing.json") as journal:
                journal.try_claim("synthetic", Order.market("005930", side="buy", quantity=1).fingerprint)
        return original(path, target, target_is_directory=target_is_directory)

    monkeypatch.setattr(Path, "symlink_to", race)
    with pytest.raises(KISUsageError, match="migrate-orders"):
        order_store_path(account="synthetic-01", environment="paper")
    assert (cache / "competing.json").is_file() and not cache.is_symlink()
    assert list((tmp_path / "state" / "kis-trader" / "orders").iterdir()) == []


def _offline_layout(tmp_path):
    from kis_trader import Order
    from kis_trader.store import OrderStore
    state = tmp_path / "state" / "kis-trader" / "orders"
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    for name in ("paper-synthetic.json", "real-other.json"):
        with OrderStore(path=cache / name, retention_days=0) as journal:
            journal.try_claim("synthetic", Order.market("005930", side="buy", quantity=1).fingerprint)
    return state, cache, {p.name: p.read_bytes() for p in cache.iterdir()}


@pytest.mark.parametrize("failure_stage", ["prepared", "copied", "installed", "backed_up", "linked", "committed"])
def test_offline_migration_resumes_each_durable_stage_without_rewriting_journals(tmp_path, monkeypatch, failure_stage):
    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    identities = {p.name: p.stat().st_ino for p in cache.iterdir()}
    original = layout._write_transaction

    def interrupt(marker, transaction):
        original(marker, transaction)
        if transaction["stage"] == failure_stage:
            raise OSError("synthetic interruption")

    with monkeypatch.context() as patch:
        patch.setattr(layout, "_write_transaction", interrupt)
        with pytest.raises(OSError, match="synthetic"):
            layout.migrate_order_store_directory(state, cache, offline=True)
    marker = state.parent / ".orders-migration.json"
    assert marker.is_file()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    with pytest.raises(KISUsageError, match="완료되지 않은"):
        order_store_path(account="synthetic-01", environment="paper")
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert not marker.exists() and cache.is_symlink() and cache.samefile(state)
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before
    backups = list(cache.parent.glob(".orders-backup-*"))
    assert len(backups) == 1
    assert {p.name: p.read_bytes() for p in backups[0].iterdir()} == before
    assert {p.name: p.stat().st_ino for p in backups[0].iterdir()} == identities


@pytest.mark.parametrize("operation", ["install", "backup"])
def test_offline_migration_recovers_operation_before_stage_record(tmp_path, monkeypatch, operation):
    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    original = layout._rename_no_replace

    def interrupt(source, destination):
        original(source, destination)
        if (operation == "install" and destination == state) or (operation == "backup" and source == cache):
            raise OSError("synthetic rename interruption")

    with monkeypatch.context() as patch:
        patch.setattr(layout, "_rename_no_replace", interrupt)
        with pytest.raises(OSError, match="synthetic"):
            layout.migrate_order_store_directory(state, cache, offline=True)
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert cache.samefile(state)
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before


@pytest.mark.parametrize("held", ["source", "state-only", "stage"])
def test_offline_migration_refuses_any_held_journal_lock_and_releases_own_locks(tmp_path, monkeypatch, held):
    from kis_trader import _order_store_layout as layout
    from kis_trader.errors import KISError
    from kis_trader.store import OrderStore
    state, cache, before = _offline_layout(tmp_path)
    target = cache
    if held == "state-only":
        state.parent.mkdir(parents=True)
        cache.rename(state)
        target = state
    elif held == "stage":
        original = layout._write_transaction

        def interrupt(marker, transaction):
            original(marker, transaction)
            if transaction["stage"] == "copied":
                raise OSError("synthetic interruption")

        with monkeypatch.context() as patch:
            patch.setattr(layout, "_write_transaction", interrupt)
            with pytest.raises(OSError):
                layout.migrate_order_store_directory(state, cache, offline=True)
        target = next(state.parent.glob(".orders-stage-*"))
    with OrderStore(path=target / "real-other.json", retention_days=0), pytest.raises(KISError):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert {p.name: p.read_bytes() for p in target.iterdir()} == before
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before


@pytest.mark.parametrize("equal", [True, False])
def test_offline_migration_never_merges_independent_journals(tmp_path, equal):
    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    state.mkdir(parents=True)
    (state / "paper-synthetic.json").write_bytes(before["paper-synthetic.json"] if equal else b"different")
    destination = (state / "paper-synthetic.json").read_bytes()
    with pytest.raises(KISUsageError, match="자동 병합"):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before
    assert (state / "paper-synthetic.json").read_bytes() == destination


def test_offline_migration_partial_copy_refuses_resume_and_preserves_all_copies(tmp_path, monkeypatch):
    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)

    def interrupt(original, output):
        output.write(original.read(10))
        raise OSError("synthetic copy failure")

    with monkeypatch.context() as patch:
        patch.setattr(layout.shutil, "copyfileobj", interrupt)
        with pytest.raises(OSError):
            layout.migrate_order_store_directory(state, cache, offline=True)
    stage = next(state.parent.glob(".orders-stage-*"))
    partial = {p.name: p.read_bytes() for p in stage.iterdir()}
    with pytest.raises(KISUsageError, match="수동 점검"):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before
    assert {p.name: p.read_bytes() for p in stage.iterdir()} == partial


def test_offline_migration_raced_cache_directory_is_not_overwritten(tmp_path, monkeypatch):
    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    original = layout._advance

    def race(marker, transaction, stage):
        original(marker, transaction, stage)
        if stage == "backed_up":
            cache.mkdir()
            (cache / "raced.json.lock").write_bytes(b"raced")

    monkeypatch.setattr(layout, "_advance", race)
    with pytest.raises(KISUsageError, match="독립"):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert (cache / "raced.json.lock").read_bytes() == b"raced"
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before


def test_offline_migration_copies_across_real_distinct_filesystems(tmp_path):
    import tempfile
    from pathlib import Path

    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    other_root = Path("/dev/shm")
    if not other_root.is_dir() or other_root.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("a second writable filesystem is unavailable")
    with tempfile.TemporaryDirectory(prefix="kis-orders-synthetic-", dir=other_root) as scratch:
        state = Path(scratch) / "state" / "kis-trader" / "orders"
        layout.migrate_order_store_directory(state, cache, offline=True)
        assert {p.name: p.read_bytes() for p in state.iterdir()} == before
        assert cache.samefile(state)


@pytest.mark.parametrize("mutation", ["stage-list", "marker-symlink", "source-replaced", "new-source-entry"])
def test_offline_recovery_rejects_unknown_transaction_topology(tmp_path, monkeypatch, mutation):
    import json
    import shutil

    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    original = layout._write_transaction

    def interrupt(marker, transaction):
        original(marker, transaction)
        if transaction["stage"] == "prepared":
            raise OSError("synthetic interruption")

    with monkeypatch.context() as patch:
        patch.setattr(layout, "_write_transaction", interrupt)
        with pytest.raises(OSError):
            layout.migrate_order_store_directory(state, cache, offline=True)
    marker = state.parent / ".orders-migration.json"
    if mutation == "stage-list":
        raw = json.loads(marker.read_text())
        raw["stage"] = []
        marker.write_text(json.dumps(raw))
    elif mutation == "marker-symlink":
        marker.rename(marker.with_suffix(".saved"))
        marker.symlink_to(marker.with_suffix(".saved"))
    elif mutation == "source-replaced":
        target = cache / "paper-synthetic.json"
        replacement = cache / "replacement"
        replacement.write_bytes(target.read_bytes())
        shutil.copymode(target, replacement)
        replacement.replace(target)
    else:
        (cache / "new.json.lock").write_bytes(b"new")
    with pytest.raises(KISUsageError):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert all((cache / name).read_bytes() == contents for name, contents in before.items())
    assert not state.exists()


def test_offline_stage_and_files_are_durable_before_source_rename(tmp_path, monkeypatch):
    from kis_trader import _order_store_layout as layout
    from kis_trader.errors import KISError
    from kis_trader.store import OrderStore
    state, cache, before = _offline_layout(tmp_path)
    synced_files = set()
    synced_dirs = set()
    original_fsync = os.fsync
    original_dir = layout.store._fsync_dir
    original_rename = layout._rename_no_replace
    original_advance = layout._advance

    def fsync(descriptor):
        details = os.fstat(descriptor)
        synced_files.add((details.st_dev, details.st_ino))
        return original_fsync(descriptor)

    def directory(path):
        original_dir(path)
        synced_dirs.add(path)

    def rename(source, destination):
        if source == cache:
            assert state.parent in synced_dirs
            assert any(p.name.startswith(".orders-stage-") for p in synced_dirs)
            assert all((p.stat().st_dev, p.stat().st_ino) in synced_files for p in state.iterdir())
            assert {p.name: p.read_bytes() for p in state.iterdir()} == before
        return original_rename(source, destination)

    def advance(marker, transaction, stage):
        original_advance(marker, transaction, stage)
        if stage == "linked":
            with pytest.raises(KISError):
                OrderStore(path=cache / "paper-synthetic.json")

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(layout.store, "_fsync_dir", directory)
    monkeypatch.setattr(layout, "_rename_no_replace", rename)
    monkeypatch.setattr(layout, "_advance", advance)
    layout.migrate_order_store_directory(state, cache, offline=True)


def test_offline_constructor_failure_leaves_bytes_and_releases_all_previous_locks(tmp_path):
    from kis_trader import _order_store_layout as layout
    from kis_trader.errors import KISError
    from kis_trader.store import OrderStore
    state, cache, before = _offline_layout(tmp_path)
    bad = cache / "zz-corrupt.json"
    bad.write_bytes(b"not valid JSON")
    with pytest.raises(KISError):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert bad.read_bytes() == b"not valid JSON"
    assert all((cache / name).read_bytes() == contents for name, contents in before.items())
    with OrderStore(path=cache / "paper-synthetic.json"), OrderStore(path=state.parent / ".orders-layout"):
        pass
    assert not state.exists() and not (state.parent / ".orders-migration.json").exists()


def test_offline_no_replace_preserves_raced_canonical_directory(tmp_path, monkeypatch):
    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    original = layout._rename_no_replace

    def race(source, destination):
        if destination == state:
            state.mkdir()
            (state / "raced.json.lock").write_bytes(b"independent")
        return original(source, destination)

    monkeypatch.setattr(layout, "_rename_no_replace", race)
    with pytest.raises(OSError):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert (state / "raced.json.lock").read_bytes() == b"independent"
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before


def test_offline_migration_preserves_actual_historical_schema_and_pending_claim(tmp_path):
    from kis_trader import Order
    from kis_trader import _order_store_layout as layout
    from kis_trader.store import OrderStore
    historical = _historical_store_module()
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    state = tmp_path / "state" / "kis-trader" / "orders"
    fingerprint = Order.market("005930", side="buy", quantity=1).fingerprint
    with historical.OrderStore(path=cache / "synthetic.json") as journal:
        journal.try_claim("synthetic", fingerprint)
    before = {p.name: p.read_bytes() for p in cache.iterdir()}
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before
    with historical.OrderStore(path=cache / "synthetic.json") as journal:
        assert isinstance(journal.try_claim("synthetic", fingerprint), historical.InFlight)
    with OrderStore(path=state / "synthetic.json"):
        pass
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before


def test_offline_probe_failure_does_not_remove_an_existing_symlink(tmp_path, monkeypatch):
    from pathlib import Path

    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    original = Path.symlink_to
    raced = []

    def collision(path, target, target_is_directory=False):
        if path.name.startswith(".orders-link-probe-"):
            original(path, tmp_path / "unrelated", target_is_directory=True)
            raced.append(path)
        return original(path, target, target_is_directory=target_is_directory)

    monkeypatch.setattr(Path, "symlink_to", collision)
    with pytest.raises(KISUsageError):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert raced and raced[0].is_symlink()
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before
    assert not (state.parent / ".orders-migration.json").exists()


def test_offline_does_not_remove_an_unrecorded_empty_state_directory(tmp_path, monkeypatch):
    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    original = layout._advance
    identity = []

    def race(marker, transaction, stage):
        original(marker, transaction, stage)
        if stage == "copied":
            state.mkdir()
            identity.append(state.stat().st_ino)

    monkeypatch.setattr(layout, "_advance", race)
    with pytest.raises(KISUsageError):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert state.stat().st_ino == identity[0] and list(state.iterdir()) == []
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before


def test_offline_accepts_recorded_empty_state_placeholder_and_lock_only_source(tmp_path):
    from kis_trader import _order_store_layout as layout
    state = tmp_path / "state" / "kis-trader" / "orders"
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    state.mkdir(parents=True)
    cache.mkdir(parents=True)
    lock = cache / "synthetic.json.lock"
    lock.write_bytes(b"")
    identity = lock.stat().st_ino
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert [p.name for p in state.iterdir()] == ["synthetic.json.lock"]
    backup = next(cache.parent.glob(".orders-backup-*"))
    assert (backup / lock.name).stat().st_ino == identity
    assert cache.samefile(state)


@pytest.mark.parametrize("failure", ["unsupported", "EXDEV"])
def test_offline_no_replace_failure_preserves_source_and_stage(tmp_path, monkeypatch, failure):
    import errno

    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)

    def reject(source, destination):
        if failure == "unsupported":
            raise KISUsageError("synthetic unsupported no-replace primitive")
        raise OSError(errno.EXDEV, "synthetic cross-device failure")

    monkeypatch.setattr(layout, "_rename_no_replace", reject)
    with pytest.raises((KISUsageError, OSError)):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert not state.exists()
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before
    stage = next(state.parent.glob(".orders-stage-*"))
    assert {p.name: p.read_bytes() for p in stage.iterdir()} == before


def test_offline_actual_historical_writer_lock_blocks_move(tmp_path):
    from kis_trader import Order
    from kis_trader import _order_store_layout as layout
    from kis_trader.errors import KISError
    historical = _historical_store_module()
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    state = tmp_path / "state" / "kis-trader" / "orders"
    with historical.OrderStore(path=cache / "synthetic.json") as journal:
        journal.try_claim("synthetic", Order.market("005930", side="buy", quantity=1).fingerprint)
        before = {p.name: p.read_bytes() for p in cache.iterdir()}
        lock_inode = (cache / "synthetic.json.lock").stat().st_ino
        with pytest.raises(KISError):
            layout.migrate_order_store_directory(state, cache, offline=True)
        assert (cache / "synthetic.json.lock").stat().st_ino == lock_inode
        assert {p.name: p.read_bytes() for p in cache.iterdir()} == before
        assert not state.exists()
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before


@pytest.mark.parametrize("failure_at", ["stage", "installed", "alias"])
def test_offline_directory_fsync_failure_retains_recoverable_copies(tmp_path, monkeypatch, failure_at):
    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    original = layout.store._fsync_dir
    failed = []

    def fsync(directory):
        should_fail = (
            failure_at == "stage" and directory.name.startswith(".orders-stage-")
            or failure_at == "installed" and directory == state.parent and state.exists()
            or failure_at == "alias" and directory == cache.parent and cache.is_symlink()
        )
        if should_fail and not failed:
            failed.append(True)
            raise OSError("synthetic durability failure")
        original(directory)

    with monkeypatch.context() as patch:
        patch.setattr(layout.store, "_fsync_dir", fsync)
        with pytest.raises(OSError, match="synthetic"):
            layout.migrate_order_store_directory(state, cache, offline=True)
    assert failed
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before
    backup = next(cache.parent.glob(".orders-backup-*"))
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == before


def test_offline_recovery_does_not_adopt_an_unrecorded_staging_directory(tmp_path, monkeypatch):
    import json
    import shutil

    from kis_trader import _order_store_layout as layout
    state, cache, before = _offline_layout(tmp_path)
    original = layout._write_transaction

    def interrupt(marker, transaction):
        original(marker, transaction)
        if transaction["stage"] == "prepared":
            raise OSError("synthetic interruption")

    with monkeypatch.context() as patch:
        patch.setattr(layout, "_write_transaction", interrupt)
        with pytest.raises(OSError):
            layout.migrate_order_store_directory(state, cache, offline=True)
    marker = state.parent / ".orders-migration.json"
    transaction = json.loads(marker.read_text())
    stage = state.parent / (".orders-stage-" + transaction["operation"])
    stage.mkdir()
    for entry in cache.iterdir():
        shutil.copy2(entry, stage / entry.name)
    with pytest.raises(KISUsageError):
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before
    assert {p.name: p.read_bytes() for p in stage.iterdir()} == before


@pytest.mark.parametrize("operation", ["lookup", "offline"])
@pytest.mark.parametrize("populated", [False, True])
def test_order_layout_rejects_cache_nested_inside_state_without_mutation(tmp_path, monkeypatch, operation, populated):
    from kis_trader import _order_store_layout as layout
    state_home = tmp_path / "state"
    state = state_home / "kis-trader" / "orders"
    cache_home = state / "cache"
    cache = cache_home / "kis-trader" / "orders"
    if populated:
        state.mkdir(parents=True)
        (state / "synthetic.json").write_bytes(b"preserve synthetic bytes")
    before = {str(p.relative_to(tmp_path)): p.read_bytes() if p.is_file() else None
              for p in tmp_path.rglob("*")}
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    with pytest.raises(KISUsageError):
        if operation == "lookup":
            order_store_path(account="synthetic-01", environment="paper")
        else:
            layout.migrate_order_store_directory(state, cache, offline=True)
    after = {str(p.relative_to(tmp_path)): p.read_bytes() if p.is_file() else None
             for p in tmp_path.rglob("*")}
    assert after == before
