"""디렉터리 fsync 의 플랫폼 분기 회귀 -- :func:`kis_trader.store._fsync_dir`.

Windows 는 디렉터리를 ``os.O_RDONLY`` 로 열 수 없어 ``os.open`` 이 PermissionError(EACCES) 를
낸다. 이는 구조적 플랫폼 특성이지 내구성 실패가 아니므로(데이터 파일은 이미 fsync + 원자 교체됨)
건너뛰어야 한다 -- 그러지 않으면 모든 ``_save_locked`` / ``try_claim`` 이 Windows 에서 크래시해
지속형 OrderStore 가 벽돌이 된다. 반대로 비-Windows 에서의 진짜 열기 실패(EIO 등)는 '저장됨'
거짓 주장을 막기 위해 전파돼야 한다.
"""

import errno
from pathlib import Path

import pytest

from kis_trader import store


# Windows 분기는 store._WINDOWS(모듈 bool)로 뒤집는다 -- 글로벌 os.name 을 'nt' 로 패치하면
# Python 3.11 의 pathlib 이 Path() 를 WindowsPath 로 만들어(비-Windows 인스턴스화 불가) 테스트가 깨진다.
def test_dir_fsync_skips_when_windows_refuses_directory_open(monkeypatch):
    def _refuse(*_a, **_k):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(store, "_WINDOWS", True)
    monkeypatch.setattr(store.os, "open", _refuse)
    # 조용히 반환해야 한다(예외 전파 없음) -- 아니면 Windows 저장이 벽돌이 된다.
    assert store._fsync_dir(Path("/any/dir")) is None


def test_dir_fsync_propagates_real_open_failure_off_windows(monkeypatch):
    def _raise_eio(*_a, **_k):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(store, "_WINDOWS", False)
    monkeypatch.setattr(store.os, "open", _raise_eio)
    # 진짜 내구성 실패는 삼키지 않고 올린다(거짓 '저장됨' 방지).
    with pytest.raises(OSError):
        store._fsync_dir(Path("/any/dir"))


def test_dir_fsync_skips_unsupported_errno_off_windows(monkeypatch):
    def _raise_einval(*_a, **_k):
        raise OSError(errno.EINVAL, "not supported")

    monkeypatch.setattr(store, "_WINDOWS", False)
    monkeypatch.setattr(store.os, "open", _raise_einval)
    # 디렉터리 fsync 미지원 errno 는 비-Windows 에서도 무시한다.
    assert store._fsync_dir(Path("/any/dir")) is None


class _StrictWindowsLock:
    LK_NBLCK = 20
    LK_UNLCK = 21

    def __init__(self):
        self.calls = []
        self.held = set()

    def locking(self, descriptor, mode, length):
        assert store.os.lseek(descriptor, 0, store.os.SEEK_CUR) == 0
        assert length == 1
        assert mode in (self.LK_NBLCK, self.LK_UNLCK)
        self.calls.append((descriptor, mode, length))
        if mode == self.LK_NBLCK:
            assert descriptor not in self.held
            self.held.add(descriptor)
        else:
            assert descriptor in self.held
            self.held.remove(descriptor)


@pytest.fixture
def windows_backend(monkeypatch):
    backend = _StrictWindowsLock()
    monkeypatch.setattr(store, "_WINDOWS", True)
    monkeypatch.setattr(store, "fcntl", None)
    monkeypatch.setattr(store, "msvcrt", backend, raising=False)
    original_close = store.os.close
    def close(descriptor):
        backend.held.discard(descriptor)              # CRT 잠금도 실제 descriptor close 시 해제된다
        return original_close(descriptor)
    monkeypatch.setattr(store.os, "close", close)
    return backend


@pytest.mark.parametrize("contents", [b"", b"persistent-lock-content"])
def test_windows_lock_exact_region_registry_and_captured_backend(tmp_path, monkeypatch, windows_backend, contents):
    path = tmp_path / "journal.json"
    lock = path.with_suffix(".json.lock")
    lock.write_bytes(contents)
    instance = store.OrderStore(path)
    descriptor = instance._process_lock_fd
    assert windows_backend.calls == [(descriptor, windows_backend.LK_NBLCK, 1)]
    def forbidden_open(*args, **kwargs):
        pytest.fail("second descriptor must not open")
    with monkeypatch.context() as patch:
        patch.setattr(store.os, "open", forbidden_open)
        with pytest.raises(store.KISError, match="이미 열려"):
            store.OrderStore(path.parent / "." / path.name)
    store.os.lseek(descriptor, 7, store.os.SEEK_SET)
    monkeypatch.setattr(store, "msvcrt", None)
    instance.close()
    instance.close()
    assert windows_backend.calls[-1] == (descriptor, windows_backend.LK_UNLCK, 1)
    assert lock.read_bytes() == contents
    assert not windows_backend.held


@pytest.mark.parametrize("failure", ["open", "seek", "acquire", "load"])
def test_windows_partial_acquire_and_load_release_resources(tmp_path, monkeypatch, windows_backend, failure):
    path = tmp_path / "failure.json"
    before = b"invalid-json"
    if failure == "load":
        path.write_bytes(before)
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise OSError(errno.EIO, "synthetic")
        if failure == "open":
            patch.setattr(store.os, "open", fail)
        elif failure == "seek":
            patch.setattr(store.os, "lseek", fail)
        elif failure == "acquire":
            patch.setattr(windows_backend, "locking", fail)
        with pytest.raises(store.KISError):
            store.OrderStore(path)
    assert not store._locked_paths
    assert not windows_backend.held
    if failure == "load":
        assert path.read_bytes() == before
        path.unlink()
    with store.OrderStore(path):
        pass


@pytest.mark.parametrize("failure", ["seek", "unlock", "close"])
def test_windows_close_cleanup_even_on_oserror(tmp_path, monkeypatch, windows_backend, failure):
    instance = store.OrderStore(tmp_path / "close.json")
    descriptor = instance._process_lock_fd
    original_close = store.os.close
    attempted = []
    def close(fd):
        attempted.append(fd)
        original_close(fd)
        if failure == "close":
            raise OSError(errno.EIO, "close")
    with monkeypatch.context() as patch:
        patch.setattr(store.os, "close", close)
        if failure == "seek":
            patch.setattr(store.os, "lseek", lambda *args: (_ for _ in ()).throw(OSError(errno.EIO, "seek")))
        elif failure == "unlock":
            patch.setattr(windows_backend, "locking", lambda *args: (_ for _ in ()).throw(OSError(errno.EIO, "unlock")))
        instance.close()
        instance.close()
    assert attempted == [descriptor]
    assert not store._locked_paths
    assert instance._process_lock_fd is None
    with pytest.raises(OSError):
        store.os.fstat(descriptor)


def test_windows_no_backend_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "fcntl", None)
    monkeypatch.setattr(store, "msvcrt", None, raising=False)
    with pytest.raises(store.KISError):
        store.OrderStore(tmp_path / "unsupported.json")


def _native_lock_holder(path, connection):
    from kis_trader.order import Order
    with store.OrderStore(path) as instance:
        instance.try_claim("pending", Order.market("005930", side="buy", quantity=1).fingerprint)
        connection.send("locked")
        connection.recv()
    connection.send("closed")


@pytest.mark.skipif(not store._WINDOWS, reason="native Windows byte-lock evidence requires Windows")
@pytest.mark.parametrize("crash", [False, True])
def test_native_windows_process_exclusion_and_recovery(tmp_path, crash):
    import multiprocessing
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    path = tmp_path / "한글-journal.json"
    process = context.Process(target=_native_lock_holder, args=(path, child))
    process.start()
    try:
        assert parent.poll(10)
        assert parent.recv() == "locked"
        with pytest.raises(store.KISError):
            store.OrderStore(path)
        if crash:
            process.terminate()
        else:
            parent.send("close")
            assert parent.poll(10)
            assert parent.recv() == "closed"
        process.join(10)
        assert not process.is_alive()
        with store.OrderStore(path) as reopened:
            assert reopened.is_in_flight("pending")
    finally:
        if process.is_alive():
            process.terminate()
        process.join(10)
        parent.close()
        child.close()


@pytest.mark.parametrize("code,contention", [(errno.EACCES, True), (errno.EAGAIN, True),
    (errno.EDEADLK, True), (errno.EBADF, False), (errno.EIO, False)])
def test_windows_lock_errno_preserves_cause(tmp_path, monkeypatch, windows_backend, code, contention):
    error = OSError(code, "synthetic")
    def fail(*args):
        raise error
    monkeypatch.setattr(windows_backend, "locking", fail)
    with pytest.raises(store.KISError) as caught:
        store.OrderStore(tmp_path / "errno.json")
    assert caught.value.__cause__ is error
    assert ("이미 열려" in str(caught.value)) is contention
    assert not store._locked_paths


def test_windows_registry_canonical_aliases(tmp_path, monkeypatch, windows_backend):
    path = tmp_path / "MixedCase.json"
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        alias = None                                # 링크 생성 권한은 파일 잠금과 별개다
    monkeypatch.setattr(store.os.path, "normcase", lambda value: value.casefold())
    with store.OrderStore(path):
        spellings = [tmp_path / "mixedcase.json"]
        if alias is not None:
            spellings.append(alias / path.name)
        for other in spellings:
            with pytest.raises(store.KISError, match="이미 열려"):
                store.OrderStore(other)
        assert len(windows_backend.held) == 1
    assert not store._locked_paths


def test_windows_registry_thread_race_has_one_owner(tmp_path, windows_backend):
    import threading
    barrier = threading.Barrier(2)
    instances = []
    errors = []
    def open_store():
        barrier.wait(timeout=2)
        try:
            instances.append(store.OrderStore(tmp_path / "race.json"))
        except store.KISError as error:
            errors.append(error)
    threads = [threading.Thread(target=open_store) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert len(instances) == len(errors) == 1
        assert len(windows_backend.held) == 1
    finally:
        for instance in instances:
            instance.close()
    assert not store._locked_paths


def test_windows_open_flags_and_independent_paths(tmp_path, monkeypatch, windows_backend):
    original_open = store.os.open
    binary = 1 << 28
    calls = []
    monkeypatch.setattr(store.os, "O_BINARY", binary, raising=False)
    def open_lock(path, flags, mode):
        calls.append(flags)
        assert flags & binary
        assert not flags & (store.os.O_TRUNC | store.os.O_APPEND)
        return original_open(path, flags & ~binary, mode)
    monkeypatch.setattr(store.os, "open", open_lock)
    with store.OrderStore(tmp_path / "one.json"), store.OrderStore(tmp_path / "two.json"):
        assert len(windows_backend.held) == 2
        assert len(calls) == 2
    assert not store._locked_paths


def test_windows_backend_supports_cli_client_journal(tmp_path, monkeypatch, windows_backend):
    from argparse import Namespace

    from kis_trader import KISConfig
    from kis_trader.cli import context
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for name in list(store.os.environ):
        if name.startswith("KIS_"):
            monkeypatch.delenv(name)
    KISConfig(profile="general", app_key="synthetic-key", app_secret="synthetic-secret",
              account="synthetic-01", environment="paper").save()
    path = tmp_path / "cli.json"
    monkeypatch.setattr(context, "order_store_path", lambda **kwargs: path)
    with context.build_client(Namespace(profile="general", environment="paper", account=None)) as client:
        assert client.environment == "paper"
        assert windows_backend.held
    with store.OrderStore(path):
        pass
