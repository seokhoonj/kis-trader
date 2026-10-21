"""배타적 주문 디렉터리 설치의 플랫폼 분기와 원본 보존을 검증한다."""
import ctypes
import errno
import os
import sys
from types import SimpleNamespace

import pytest

from kis_trader import _order_store_layout as layout
from kis_trader.errors import KISUsageError


def _darwin_library(monkeypatch, code=0):
    calls = []

    def rename(source, destination, flags):
        calls.append((source, destination, flags))
        if code:
            ctypes.set_errno(code)
            return -1
        os.rename(source, destination)
        return 0

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(layout, "os", SimpleNamespace(name="posix", fsencode=os.fsencode, rename=os.rename))
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: SimpleNamespace(renamex_np=rename))
    return calls


def test_darwin_rename_uses_exclusive_flag_and_preserves_payload(tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    (source / "journal.json").write_bytes(b"synthetic-journal")
    calls = _darwin_library(monkeypatch)
    layout._rename_no_replace(source, destination)
    assert calls == [(os.fsencode(source), os.fsencode(destination), 4)]
    assert not source.exists()
    assert (destination / "journal.json").read_bytes() == b"synthetic-journal"


@pytest.mark.parametrize("code", [errno.ENOSYS, errno.EINVAL, errno.ENOTSUP])
def test_darwin_unsupported_filesystem_refuses_without_fallback(tmp_path, monkeypatch, code):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    calls = _darwin_library(monkeypatch, code)
    with pytest.raises(KISUsageError, match="파일시스템.*지원하지 않습니다"):
        layout._rename_no_replace(source, destination)
    assert len(calls) == 1 and calls[0][2] == 4
    assert source.is_dir() and not destination.exists()


@pytest.mark.parametrize("code", [errno.EEXIST, errno.EACCES])
def test_darwin_rename_preserves_native_errno_and_both_sentinels(tmp_path, monkeypatch, code):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "source-sentinel").write_bytes(b"source")
    (destination / "destination-sentinel").write_bytes(b"destination")
    calls = _darwin_library(monkeypatch, code)
    with pytest.raises(OSError) as error:
        layout._rename_no_replace(source, destination)
    assert error.value.errno == code
    assert len(calls) == 1
    assert (source / "source-sentinel").read_bytes() == b"source"
    assert (destination / "destination-sentinel").read_bytes() == b"destination"


@pytest.mark.skipif(sys.platform != "darwin", reason="actual Darwin renamex_np requires native macOS")
@pytest.mark.parametrize("target", ["absent", "empty", "nonempty", "symlink", "dangling"])
def test_native_darwin_exclusive_rename_keeps_live_lock_and_never_overwrites(tmp_path, target):
    import fcntl

    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    lock = source / "journal.json.lock"
    lock.write_bytes(b"synthetic-lock-payload")
    if target in {"empty", "nonempty"}:
        destination.mkdir()
        if target == "nonempty":
            (destination / "sentinel").write_bytes(b"destination")
    elif target in {"symlink", "dangling"}:
        referent = tmp_path / "referent"
        if target == "symlink":
            referent.mkdir()
            (referent / "sentinel").write_bytes(b"referent")
        destination.symlink_to(referent, target_is_directory=True)
    with lock.open("r+b") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = (os.fstat(owner.fileno()).st_dev, os.fstat(owner.fileno()).st_ino)
        if target == "absent":
            layout._rename_no_replace(source, destination)
            assert not source.exists()
            moved_lock = destination / lock.name
            assert (moved_lock.stat().st_dev, moved_lock.stat().st_ino) == identity
            with moved_lock.open("r+b") as contender, pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            with pytest.raises(OSError) as error:
                layout._rename_no_replace(source, destination)
            assert error.value.errno == errno.EEXIST
            assert source.is_dir()
            if target == "nonempty":
                assert (destination / "sentinel").read_bytes() == b"destination"
            if target in {"symlink", "dangling"}:
                assert destination.is_symlink()
                assert os.readlink(destination) == str(tmp_path / "referent")
            if target == "symlink":
                assert (tmp_path / "referent" / "sentinel").read_bytes() == b"referent"
        owner.seek(0)
        assert owner.read() == b"synthetic-lock-payload"
