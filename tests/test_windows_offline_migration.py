"""Windows 오프라인 이전: 잠긴 자식 디렉터리를 이동하지 않고 실제 상태를 보존한다."""
from __future__ import annotations

import subprocess
import sys

import pytest

from kis_trader import Order, OrderStore
from kis_trader import _order_store_layout as layout
from kis_trader.errors import KISError, KISUsageError


def _seed(tmp_path, payload=b"synthetic-byte-zero"):
    state = tmp_path / "state" / "kis-trader" / "orders"
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    cache.mkdir(parents=True)
    with OrderStore(cache / "synthetic.json", retention_days=0) as journal:
        journal.bind_scope(cano="synthetic", product_code="01", environment="paper")
        journal.try_claim("pending", Order.market("005930", side="buy", quantity=1).fingerprint)
    (cache / "synthetic.json.lock").write_bytes(payload)
    return state, cache


def _contents(directory):
    return {entry.name: entry.read_bytes() for entry in directory.iterdir()}


@pytest.fixture
def windows_protocol(monkeypatch):
    """실제 호스트 락을 쓰고 Windows의 열린 자식 rename 제약만 인과적으로 모사한다."""
    original_acquire = OrderStore._acquire_single_writer_lock
    original_init = layout._JournalLocks.__init__
    original_rename = layout._rename_no_replace
    stacks = []
    def acquire(journal):
        if sys.platform == "win32":
            return original_acquire(journal)
        layout.store._WINDOWS = False
        try:
            return original_acquire(journal)
        finally:
            layout.store._WINDOWS = True
    def initialize(stack):
        original_init(stack)
        stacks.append(stack)
    def rename(source, destination):
        identities = {(entry.stat().st_dev, entry.stat().st_ino) for entry in source.iterdir()}
        if any(identity in identities for stack in stacks for identity in stack._descriptors):
            raise PermissionError("synthetic Windows refuses directory with held child")
        return original_rename(source, destination)
    monkeypatch.setattr(OrderStore, "_acquire_single_writer_lock", acquire)
    monkeypatch.setattr(layout._JournalLocks, "__init__", initialize)
    monkeypatch.setattr(layout, "_rename_no_replace", rename)
    monkeypatch.setattr(layout, "_requires_legacy_alias", lambda: False)
    monkeypatch.setattr(layout.store, "_WINDOWS", True)
    return stacks


@pytest.mark.parametrize("payload", [b"", b"synthetic-byte-zero"])
def test_windows_protocol_moves_only_closed_child_directories_and_preserves_bytes(tmp_path, windows_protocol, payload):
    state, cache = _seed(tmp_path, payload)
    before = _contents(cache)
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert _contents(state) == before
    assert not cache.exists()
    backups = list(cache.parent.glob(".orders-backup-*"))
    assert len(backups) == 1 and _contents(backups[0]) == before
    assert not (state.parent / ".orders-migration.json").exists()
    with OrderStore(state / "synthetic.json", retention_days=0) as reopened:
        assert reopened.is_in_flight("pending")
        reopened.bind_scope(cano="synthetic", product_code="01", environment="paper")


def test_selective_source_release_keeps_destination_and_layout_excluded(tmp_path, windows_protocol):
    state, cache = _seed(tmp_path)
    state.mkdir(parents=True)
    with layout._JournalLocks() as stack:
        control = state.parent / ".orders-layout"
        stack.enter_context(OrderStore(control, retention_days=0))
        stack.acquire(cache)
        stack.acquire(state)
        # Use a real destination lock file even when the destination directory is empty.
        with OrderStore(state / "synthetic.json", retention_days=0):
            pass
        stack.acquire(state)
        source_identity = (cache.joinpath("synthetic.json.lock").stat().st_dev, cache.joinpath("synthetic.json.lock").stat().st_ino)
        stack.release_source(cache)
        assert source_identity not in stack._descriptors
        with OrderStore(cache / "synthetic.json", retention_days=0):
            pass
        for blocked in (state / "synthetic.json", control):
            with pytest.raises(KISError):
                OrderStore(blocked, retention_days=0)


@pytest.mark.parametrize("crash", ["copied", "installed", "backed_up", "state_rename"])
def test_windows_protocol_resumes_known_stages_and_install_before_advance(tmp_path, windows_protocol, monkeypatch, crash):
    state, cache = _seed(tmp_path)
    before = _contents(cache)
    original_advance = layout._advance
    original_rename = layout._rename_no_replace
    def advance(marker, transaction, stage):
        original_advance(marker, transaction, stage)
        if stage == crash:
            raise RuntimeError("synthetic interruption")
    def rename(source, destination):
        original_rename(source, destination)
        if crash == "state_rename" and destination == state:
            raise RuntimeError("synthetic interruption")
    with monkeypatch.context() as temporary:
        temporary.setattr(layout, "_advance", advance)
        temporary.setattr(layout, "_rename_no_replace", rename)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            layout.migrate_order_store_directory(state, cache, offline=True)
    assert (state.parent / ".orders-migration.json").exists()
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert _contents(state) == before
    assert not (state.parent / ".orders-migration.json").exists()


def test_windows_cutover_changed_source_is_preserved_and_never_committed(tmp_path, windows_protocol, monkeypatch):
    state, cache = _seed(tmp_path)
    original = layout._JournalLocks.release_source
    def release(stack, directory):
        original(stack, directory)
        with (directory / "synthetic.json").open("ab") as output:
            output.write(b" ")
    monkeypatch.setattr(layout._JournalLocks, "release_source", release)
    with pytest.raises(KISUsageError):
        layout.migrate_order_store_directory(state, cache, offline=True)
    marker = state.parent / ".orders-migration.json"
    assert marker.exists()
    backups = list(cache.parent.glob(".orders-backup-*"))
    source = cache if cache.exists() else backups[0]
    assert source.joinpath("synthetic.json").read_bytes().endswith(b" ")
    assert source.joinpath("synthetic.json").read_bytes() != state.joinpath("synthetic.json").read_bytes()


@pytest.mark.parametrize("kind", ["unknown_entry", "independent_state", "bad_marker"])
def test_windows_unknown_layout_refuses_before_creating_control_files(tmp_path, windows_protocol, kind):
    state, cache = _seed(tmp_path)
    if kind == "unknown_entry":
        (cache / "unknown.bin").write_bytes(b"synthetic")
    elif kind == "independent_state":
        state.mkdir(parents=True)
        (state / "other.json").write_bytes(b"synthetic")
    else:
        state.parent.mkdir(parents=True)
        (state.parent / ".orders-migration.json").write_bytes(b"{}")
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(KISUsageError):
        layout.migrate_order_store_directory(state, cache, offline=True)
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.skipif(sys.platform != "win32", reason="requires actual native Windows directory and byte-lock behavior")
@pytest.mark.parametrize("payload", [b"", b"synthetic-byte-zero"])
def test_native_windows_offline_migration_preserves_pending_and_lock_bytes(tmp_path, payload):
    state, cache = _seed(tmp_path, payload)
    before = _contents(cache)
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert _contents(state) == before
    backup, = cache.parent.glob(".orders-backup-*")
    assert _contents(backup) == before
    with OrderStore(state / "synthetic.json", retention_days=0) as reopened:
        assert reopened.is_in_flight("pending")


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows subprocess crash and lock cleanup")
@pytest.mark.parametrize("stage", ["copied", "installed", "backed_up", "state_rename", "source_release", "backup_rename"])
def test_native_windows_offline_migration_crash_resume(tmp_path, stage):
    state, cache = _seed(tmp_path)
    before = _contents(cache)
    script = '''import os,sys
from pathlib import Path
from kis_trader import _order_store_layout as layout
original=layout._advance
def advance(marker, transaction, stage):
 original(marker,transaction,stage)
 if stage==sys.argv[3]:os._exit(91)
layout._advance=advance
original_rename=layout._rename_no_replace
def rename(source,destination):
 original_rename(source,destination)
 if sys.argv[3]=="state_rename" and destination==Path(sys.argv[1]):os._exit(91)
 if sys.argv[3]=="backup_rename" and source==Path(sys.argv[2]):os._exit(91)
layout._rename_no_replace=rename
original_release=layout._JournalLocks.release_source
def release(stack,directory):
 original_release(stack,directory)
 if sys.argv[3]=="source_release":os._exit(91)
layout._JournalLocks.release_source=release
layout.migrate_order_store_directory(Path(sys.argv[1]),Path(sys.argv[2]),offline=True)
'''
    result = subprocess.run([sys.executable, "-c", script, str(state), str(cache), stage], check=False, capture_output=True, timeout=20)
    assert result.returncode == 91, result.stderr.decode(errors="replace")
    assert (state.parent / ".orders-migration.json").exists()
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert _contents(state) == before
    assert not (state.parent / ".orders-migration.json").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows foreign-process directory exclusion")
def test_native_windows_source_contender_blocks_cutover(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    state, cache = _seed(tmp_path)
    before = _contents(cache)
    original_release = layout._JournalLocks.release_source
    contenders = []
    script = '''import sys
from kis_trader import OrderStore
with OrderStore(sys.argv[1],retention_days=0):
 print("READY",flush=True)
 sys.stdin.readline()
'''

    def release(stack, directory):
        original_release(stack, directory)
        contender = subprocess.Popen(
            [sys.executable, "-c", script, str(directory / "synthetic.json")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        contenders.append(contender)
        assert contender.stdout is not None
        with ThreadPoolExecutor(max_workers=1) as executor:
            ready = executor.submit(contender.stdout.readline)
            try:
                assert ready.result(timeout=15).strip() == "READY"
            except BaseException:
                contender.kill()
                raise

    monkeypatch.setattr(layout._JournalLocks, "release_source", release)
    try:
        with pytest.raises((OSError, KISError)):
            layout.migrate_order_store_directory(state, cache, offline=True)
        assert (state.parent / ".orders-migration.json").exists()
        # No commit or merge: both original and installed copies remain intact.
        assert _contents(state) == before
        assert (cache / "synthetic.json").read_bytes() == before["synthetic.json"]
    finally:
        for contender in contenders:
            contender.communicate(input="STOP\n", timeout=15)
    assert _contents(cache) == before
    monkeypatch.setattr(layout._JournalLocks, "release_source", original_release)
    layout.migrate_order_store_directory(state, cache, offline=True)
    assert _contents(state) == before


@pytest.mark.parametrize("root", ["state", "cache", "stage"])
def test_reparse_directory_refuses_before_inventory_or_mutation(tmp_path, monkeypatch, root):
    from pathlib import Path
    from types import SimpleNamespace

    state, cache = _seed(tmp_path)
    state.mkdir(parents=True)
    selected = {"state": state, "cache": cache, "stage": tmp_path / "stage"}[root]
    selected.mkdir(exist_ok=True)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    original_lstat = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == selected:
            details = original_lstat(path, *args, **kwargs)
            return SimpleNamespace(st_mode=details.st_mode, st_file_attributes=0x400)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(KISUsageError):
        if root == "stage":
            layout._journal_entries(selected)
        else:
            layout.migrate_order_store_directory(state, cache, offline=True)
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.skipif(sys.platform != "win32", reason="requires actual native Windows junction semantics")
@pytest.mark.parametrize("root", ["state", "cache"])
def test_native_windows_junction_roots_refuse_without_touching_referent(tmp_path, root):
    state = tmp_path / "state" / "kis-trader" / "orders"
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    referent = tmp_path / "external"
    referent.mkdir()
    (referent / "synthetic.json").write_bytes(b"synthetic-preserved")
    selected = state if root == "state" else cache
    selected.parent.mkdir(parents=True)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(selected), str(referent)],
        capture_output=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    before = _contents(referent)
    identities = {p.name: (p.stat().st_dev, p.stat().st_ino) for p in referent.iterdir()}
    for operation in (layout.ensure_order_store_directory,
                      lambda state, cache: layout.migrate_order_store_directory(state, cache, offline=True)):
        with pytest.raises(KISUsageError):
            operation(state, cache)
        assert _contents(referent) == before
        assert {p.name: (p.stat().st_dev, p.stat().st_ino) for p in referent.iterdir()} == identities
        assert not (state.parent / ".orders-layout.lock").exists()
        assert not (state.parent / ".orders-migration.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink fixture models Windows reparse attributes without requiring symlink privileges")
@pytest.mark.parametrize("operation", ["ensure", "migrate"])
def test_existing_direct_cache_symlink_reparse_preserves_shared_state(tmp_path, monkeypatch, operation):
    from pathlib import Path
    from types import SimpleNamespace

    state = tmp_path / "state" / "kis-trader" / "orders"
    cache = tmp_path / "cache" / "kis-trader" / "orders"
    state.mkdir(parents=True)
    cache.parent.mkdir(parents=True)
    cache.symlink_to(state, target_is_directory=True)
    original_lstat = Path.lstat

    def lstat(path, *args, **kwargs):
        details = original_lstat(path, *args, **kwargs)
        if path == cache:
            return SimpleNamespace(st_mode=details.st_mode, st_file_attributes=0x400)
        return details

    monkeypatch.setattr(Path, "lstat", lstat)
    if operation == "ensure":
        assert layout.ensure_order_store_directory(state, cache) == state
    else:
        layout.migrate_order_store_directory(state, cache, offline=True)
    assert cache.is_symlink() and cache.samefile(state)
    assert list(state.iterdir()) == []
