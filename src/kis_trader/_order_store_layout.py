"""주문 저장 디렉터리의 물리적 state 배치와 구 경로 별칭을 검증한다."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import ModuleType
from typing import BinaryIO, cast

from . import store
from .errors import KISUsageError

_OFFLINE = "모든 주문 저장소 사용 프로세스와 자동 재시작을 중지한 뒤 kis config migrate-orders --offline 을 실행하세요."


def _present(path: Path) -> bool:
    """깨진 심볼릭 링크도 존재하는 디렉터리 항목으로 취급한다."""
    return os.path.lexists(path)


def _requires_legacy_alias() -> bool:
    """fcntl 기반 구 바이너리가 사용할 수 있는 플랫폼만 캐시 별칭을 요구한다."""
    return importlib.util.find_spec("fcntl") is not None


def _validate_roots(state: Path, cache: Path) -> None:
    if state.is_symlink() or (_present(state) and not state.is_dir()):
        raise KISUsageError("주문 state 디렉터리가 실제 디렉터리가 아닙니다. " + _OFFLINE)
    try:
        physical_state = state.resolve()
        cache_tree = cache.parent.parent.resolve()
        overlaps_cache = physical_state.is_relative_to(cache_tree) or cache_tree.is_relative_to(physical_state)
    except (OSError, RuntimeError):
        raise KISUsageError("주문 디렉터리의 실제 경로를 확인할 수 없습니다. " + _OFFLINE) from None
    if overlaps_cache:
        raise KISUsageError("주문 state 디렉터리와 캐시 정리 트리는 서로 포함할 수 없습니다.")


def _shared_alias(state: Path, cache: Path) -> bool:
    if not cache.is_symlink():
        return False
    # 직접 지정한 물리적 state 디렉터리만 허용한다. 다른 링크 체인은 추측하지 않는다.
    return state.is_dir() and Path(os.readlink(cache)) == state and cache.samefile(state)


def ensure_order_store_directory(state: Path, cache: Path) -> Path:
    """새 배치는 초기화하고 기존 실제 캐시 디렉터리는 온라인 이동 없이 거부한다.

    POSIX 구 버전과 공존하려면 캐시 orders 디렉터리 별칭이 유지되어야 한다. 캐시 청소로
    별칭이 없어졌다면 구 버전 시작을 중지하고 오프라인 복구를 완료해야 한다. 스키마를
    읽지 못하는 구 버전은 같은 저장소에서 안전하게 거부될 수 있다.
    """
    _validate_roots(state, cache)
    state.parent.mkdir(parents=True, exist_ok=True)
    # JSON은 만들지 않는다. 실제 OrderStore 수명 락을 재사용하되 읽기/스키마 검증만 한다.
    with store.OrderStore(path=state.parent / ".orders-layout", retention_days=0):
        _validate_roots(state, cache)
        if _present(state.parent / ".orders-migration.json"):
            raise KISUsageError("완료되지 않은 주문 저장소 이전이 있습니다. " + _OFFLINE)
        if _present(cache):
            if _shared_alias(state, cache):
                return state
            raise KISUsageError("기존 주문 캐시 디렉터리 또는 알 수 없는 별칭이 있습니다. " + _OFFLINE)
        if _present(state):
            if not _requires_legacy_alias():
                return state
            raise KISUsageError("주문 state 디렉터리에 대응하는 캐시 별칭이 없습니다. " + _OFFLINE)
        state.mkdir()
        store._fsync_dir(state.parent)
        if _requires_legacy_alias():
            cache.parent.mkdir(parents=True, exist_ok=True)
            try:
                cache.symlink_to(state, target_is_directory=True)
            except FileExistsError:
                raise KISUsageError("초기화 중 기존 주문 캐시 디렉터리가 생성되었습니다. " + _OFFLINE) from None
            except OSError:
                raise KISUsageError("주문 저장소의 디렉터리 별칭을 만들 수 없습니다. " + _OFFLINE) from None
            store._fsync_dir(cache.parent)
            if not _shared_alias(state, cache):
                raise KISUsageError("주문 저장소의 디렉터리 별칭 검증에 실패했습니다. " + _OFFLINE)
        return state


def _identity(path: Path) -> tuple[int, int]:
    details = path.stat(follow_symlinks=False)
    return details.st_dev, details.st_ino


def _journal_entries(directory: Path) -> list[Path]:
    """잠긴 바이트를 열지 않고 이름·정규 파일 여부만 검사한다."""
    import stat

    entries = sorted(directory.iterdir())
    for entry in entries:
        if not stat.S_ISREG(entry.lstat().st_mode) or not entry.name.endswith((".json", ".json.lock")):
            raise KISUsageError("주문 디렉터리에 알 수 없는 항목이 있습니다. 수동 점검이 필요합니다.")
    return entries


def _open_windows_migration_lock(lock_path: Path, backend: ModuleType) -> int:
    """이전 중에도 락을 풀지 않고 디렉터리 rename을 허용하는 전용 핸들을 연다.

    일반 OrderStore의 CRT 공유 정책은 바꾸지 않는다. 링크를 따라가지 않고 핸들의 정규 파일
    속성을 확인한 뒤 CRT에 소유권을 넘긴다. 넘긴 뒤에는 descriptor만 닫는다.
    https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
    https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/open-osfhandle
    """
    import ctypes
    import stat

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", ctypes.c_uint32), ("ReparseTag", ctypes.c_uint32)]

    # ctypes의 Win32 심볼은 POSIX 타입 정의에는 없으나 이 경로는 Windows 백엔드에서만 호출된다.
    windows_library = ctypes.WinDLL  # type: ignore[attr-defined]
    windows_error = ctypes.WinError  # type: ignore[attr-defined]
    last_error = ctypes.get_last_error  # type: ignore[attr-defined]
    api = windows_library("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                               ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    api.CreateFileW.restype = ctypes.c_void_p
    api.GetFileInformationByHandleEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                ctypes.c_void_p, ctypes.c_uint32]
    api.GetFileInformationByHandleEx.restype = ctypes.c_int
    api.GetFileType.argtypes = [ctypes.c_void_p]
    api.GetFileType.restype = ctypes.c_uint32
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int
    # READ|WRITE, SHARE_READ|WRITE|DELETE, OPEN_ALWAYS, NORMAL|OPEN_REPARSE_POINT.
    handle = api.CreateFileW(str(lock_path), 0xC0000000, 0x7, None, 4, 0x00200080, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise windows_error(last_error())
    descriptor: int | None = None
    transferred = False
    accepted = False
    try:
        attributes = FileAttributeTagInfo()
        if not api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
            raise windows_error(last_error())
        if attributes.FileAttributes & (0x400 | 0x10) or api.GetFileType(handle) != 1:
            raise KISUsageError("주문 잠금 파일은 링크가 아닌 정규 디스크 파일이어야 합니다.")
        descriptor = backend.open_osfhandle(int(handle), os.O_RDWR | getattr(os, "O_BINARY", 0))
        if descriptor < 0:
            raise OSError("주문 잠금 핸들을 descriptor로 변환할 수 없습니다.")
        transferred = True
        details = os.fstat(descriptor)
        current = lock_path.lstat()
        if (not stat.S_ISREG(details.st_mode) or not stat.S_ISREG(current.st_mode)
                or (details.st_dev, details.st_ino) != (current.st_dev, current.st_ino)):
            raise KISUsageError("이전 중 주문 잠금 파일이 교체되었습니다. 모든 사본을 보존하세요.")
        os.set_inheritable(descriptor, False)
        accepted = True
        return descriptor
    finally:
        if not transferred:
            api.CloseHandle(handle)
        elif not accepted:
            assert descriptor is not None
            os.close(descriptor)


class _MigrationJournal(store.OrderStore):
    """이전 대상의 Windows 핸들만 rename 공유를 허용하고 기존 수명 락은 그대로 쓴다."""

    def _open_windows_lock_descriptor(self, lock_path: Path, backend: ModuleType) -> int:
        return _open_windows_migration_lock(lock_path, backend)


class _JournalLocks(ExitStack):
    """수명 락과 같은 파일 핸들로 잠긴 락 바이트를 읽으며 rename 뒤에도 inode로 연결한다."""

    def __init__(self) -> None:
        super().__init__()
        self._descriptors: dict[tuple[int, int], int] = {}

    def acquire(self, directory: Path) -> None:
        names = {entry.name.removesuffix(".lock") if entry.name.endswith(".json.lock") else entry.name
                 for entry in _journal_entries(directory)}
        for name in sorted(names):
            journal = self.enter_context(_MigrationJournal(path=directory / name, retention_days=0))
            descriptor = journal._process_lock_fd
            assert descriptor is not None
            details = os.fstat(descriptor)
            self._descriptors[(details.st_dev, details.st_ino)] = descriptor

    @contextmanager
    def open_read(self, path: Path) -> Iterator[BinaryIO]:
        details = path.stat()
        descriptor = self._descriptors.get((details.st_dev, details.st_ino))
        if descriptor is None:
            with path.open("rb") as readable:
                yield readable
            return
        # Windows 바이트 락은 다른 핸들의 읽기도 막는다. 락 소유 핸들을 유지하고 위치를 복원한다.
        position = os.lseek(descriptor, 0, os.SEEK_CUR)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            with os.fdopen(descriptor, "rb", closefd=False) as readable:
                yield readable
        finally:
            os.lseek(descriptor, position, os.SEEK_SET)

    def read_bytes(self, path: Path) -> bytes:
        with self.open_read(path) as readable:
            return readable.read()


def _inventory(path: Path, locks: _JournalLocks) -> dict[str, dict[str, str | int]]:
    """정규 저널·락의 전체 바이트와 모드를 검증하며 잠금 파일을 다시 열지 않는다."""
    import hashlib
    import stat

    entries: dict[str, dict[str, str | int]] = {}
    for entry in _journal_entries(path):
        details = entry.lstat()
        payload = locks.read_bytes(entry)
        entries[entry.name] = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload), "mode": stat.S_IMODE(details.st_mode),
        }
    return entries


def _lock_inventory(stack: _JournalLocks, directory: Path) -> None:
    """바이트를 읽기 전에 JSON 없는 락도 실제 OrderStore 백엔드로 확보한다."""
    stack.acquire(directory)


def _write_transaction(marker: Path, transaction: dict[str, object]) -> None:
    """단계 기록은 파일과 부모 디렉터리를 fsync한 뒤에만 완료로 간주한다."""
    descriptor, temporary = tempfile.mkstemp(dir=marker.parent, prefix=".orders-transaction-")
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(json.dumps(transaction, sort_keys=True).encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, marker)
        store._fsync_dir(marker.parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _advance(marker: Path, transaction: dict[str, object], stage: str) -> None:
    transaction["stage"] = stage
    _write_transaction(marker, transaction)


def _rename_no_replace(source: Path, destination: Path) -> None:
    """사용 가능한 배타적 rename만 쓰고 불지원 플랫폼에서 덮어쓰기로 폴백하지 않는다.

    Linux RENAME_NOREPLACE: https://man7.org/linux/man-pages/man2/rename.2.html
    Windows 대상 존재 거부: https://docs.python.org/3/library/os.html#os.rename
    """
    if os.name == "nt":
        os.rename(source, destination)  # Windows rename은 기존 대상을 덮어쓰지 않는다.
        return
    import ctypes
    import errno

    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise KISUsageError("이 플랫폼은 배타적 디렉터리 설치를 지원하지 않습니다. 원본은 보존됩니다.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        if code in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
            raise KISUsageError("이 파일시스템은 배타적 디렉터리 설치를 지원하지 않습니다. 원본은 보존됩니다.")
        raise OSError(code, "주문 디렉터리의 배타적 설치에 실패했습니다.")


def _assert_copy_metadata(directory: Path, entries: object) -> None:
    """락 획득이 기록에 없던 파일을 만들기 전에 파일 목록·크기·모드를 대조한다."""
    import stat

    if directory.is_symlink() or not directory.is_dir():
        raise KISUsageError("이전 기록과 현재 주문 디렉터리가 다릅니다. 수동 점검하세요.")
    expected = cast(dict[str, dict[str, str | int]], entries)
    paths = _journal_entries(directory)
    if {path.name for path in paths} != set(expected):
        raise KISUsageError("이전 기록과 현재 주문 파일 목록이 다릅니다. 모든 사본을 보존하고 수동 점검하세요.")
    for path in paths:
        details = path.lstat()
        metadata = expected[path.name]
        if details.st_size != metadata["size"] or stat.S_IMODE(details.st_mode) != metadata["mode"]:
            raise KISUsageError("이전 기록과 현재 주문 파일 정보가 다릅니다. 모든 사본을 보존하고 수동 점검하세요.")


def _assert_source_metadata(directory: Path, transaction: dict[str, object]) -> None:
    """원본의 파일 이름·inode가 바뀌었으면 잠금 파일 생성도 하기 전에 거부한다."""
    _assert_copy_metadata(directory, transaction["entries"])
    if list(_identity(directory)) != transaction["source_identity"]:
        raise KISUsageError("이전 원본 디렉터리의 정체성이 변경되었습니다. 수동 점검하세요.")
    identities = {path.name: list(_identity(path)) for path in _journal_entries(directory)}
    if identities != transaction["file_identities"]:
        raise KISUsageError("이전 원본 파일이 교체되었습니다. 모든 사본을 보존하고 수동 점검하세요.")


def _assert_copy(directory: Path, entries: object, locks: _JournalLocks) -> None:
    if directory.is_symlink() or not directory.is_dir() or _inventory(directory, locks) != entries:
        raise KISUsageError("이전 기록과 현재 주문 파일이 다릅니다. 모든 사본을 보존하고 수동 점검하세요.")


def _assert_source(directory: Path, transaction: dict[str, object], locks: _JournalLocks) -> None:
    _assert_copy(directory, transaction["entries"], locks)
    if list(_identity(directory)) != transaction["source_identity"]:
        raise KISUsageError("이전 원본 디렉터리의 정체성이 변경되었습니다. 수동 점검하세요.")
    identities = {name: list(_identity(directory / name)) for name in _inventory(directory, locks)}
    if identities != transaction["file_identities"]:
        raise KISUsageError("이전 원본 파일이 교체되었습니다. 모든 사본을 보존하고 수동 점검하세요.")


def _load_transaction(marker: Path, state: Path, cache: Path) -> dict[str, object]:
    """기록에서 임의의 경로를 읽어 이동하지 않고 선택한 두 루트와 작업 ID로 한정한다."""
    if marker.is_symlink() or not marker.is_file():
        raise KISUsageError("주문 이전 단계 기록은 실제 정규 파일이어야 합니다.")
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise KISUsageError("주문 이전 단계 기록을 읽을 수 없습니다. 수동 점검이 필요합니다.") from None
    keys = {"operation", "state", "cache", "stage", "source_identity", "file_identities", "entries", "stage_identity", "placeholder_identity"}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise KISUsageError("주문 이전 단계 기록의 형식이 올바르지 않습니다.")
    operation = raw["operation"]
    if (not isinstance(operation, str) or re.fullmatch(r"[0-9a-f]{32}", operation) is None
            or raw["state"] != str(state) or raw["cache"] != str(cache)
            or not isinstance(raw["stage"], str) or raw["stage"] not in {"prepared", "copied", "installed", "backed_up", "linked", "committed"}
            or not isinstance(raw["entries"], dict) or not isinstance(raw["file_identities"], dict)):
        raise KISUsageError("주문 이전 단계 기록이 현재 배치와 일치하지 않습니다.")
    entries = raw["entries"]
    if set(entries) != set(raw["file_identities"]):
        raise KISUsageError("주문 이전 파일 목록이 일치하지 않습니다.")
    for name, metadata in entries.items():
        if (not isinstance(name, str) or Path(name).name != name
                or not (name.endswith((".json", ".json.lock")))
                or not isinstance(metadata, dict) or set(metadata) != {"sha256", "size", "mode"}
                or not isinstance(metadata["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"]) is None
                or type(metadata["size"]) is not int or metadata["size"] < 0
                or type(metadata["mode"]) is not int or not 0 <= metadata["mode"] <= 0o777):
            raise KISUsageError("주문 이전 파일 검증 정보가 올바르지 않습니다.")
    for identity in [raw["source_identity"], *raw["file_identities"].values()]:
        if not isinstance(identity, list) or len(identity) != 2 or any(type(n) is not int or n < 0 for n in identity):
            raise KISUsageError("주문 이전 파일 정체성 정보가 올바르지 않습니다.")
    for identity in (raw["stage_identity"], raw["placeholder_identity"]):
        if identity is not None and (not isinstance(identity, list) or len(identity) != 2 or any(type(n) is not int or n < 0 for n in identity)):
            raise KISUsageError("주문 이전 임시 디렉터리 정보가 올바르지 않습니다.")
    return cast(dict[str, object], raw)


def _copy_stage(
    source: Path, stage: Path, entries: dict[str, dict[str, str | int]],
    marker: Path, transaction: dict[str, object], locks: _JournalLocks,
) -> None:
    stage.mkdir()
    transaction["stage_identity"] = list(_identity(stage))
    _write_transaction(marker, transaction)
    for name, metadata in entries.items():
        # 복사는 목적지 파일시스템에서 배타적으로 생성한다. 원본은 어떤 단계에서도 삭제하지 않는다.
        with locks.open_read(source / name) as original, (stage / name).open("xb") as output:
            shutil.copyfileobj(original, output)
            output.flush()
            os.chmod(stage / name, int(metadata["mode"]))
            os.fsync(output.fileno())
    store._fsync_dir(stage)
    store._fsync_dir(stage.parent)


def _probe_alias(state: Path, cache: Path, operation: str) -> None:
    if not _requires_legacy_alias():
        return
    probe = cache.parent / (".orders-link-probe-" + operation)
    created = False
    try:
        probe.symlink_to(state, target_is_directory=True)
        created = True
    except OSError:
        raise KISUsageError("디렉터리 별칭을 만들 수 없어 이전을 시작하지 않았습니다.") from None
    finally:
        if created:
            probe.unlink()
            store._fsync_dir(probe.parent)


def _resume_migration(
    state: Path, cache: Path, marker: Path, transaction: dict[str, object], stack: _JournalLocks,
    *, source_locked: bool = False,
) -> None:
    operation = cast(str, transaction["operation"])
    stage = state.parent / (".orders-stage-" + operation)
    backup = cache.parent / (".orders-backup-" + operation)
    entries = cast(dict[str, dict[str, str | int]], transaction["entries"])
    # 준비된 작업의 원본은 캐시 또는 보존 백업 중 정확히 한 곳에 있어야 한다.
    if _present(cache) and not cache.is_symlink():
        if _present(backup):
            raise KISUsageError("원본과 백업이 동시에 있어 이전을 재개할 수 없습니다.")
        _assert_source_metadata(cache, transaction)
        if not source_locked:
            _lock_inventory(stack, cache)
        _assert_source(cache, transaction, stack)
    else:
        _assert_source_metadata(backup, transaction)
        _lock_inventory(stack, backup)
        _assert_source(backup, transaction, stack)
    destination_locked = False
    if transaction["stage"] == "prepared":
        _assert_source(cache, transaction, stack)
        if not _present(stage):
            _copy_stage(cache, stage, entries, marker, transaction, stack)
        if list(_identity(stage)) != transaction["stage_identity"]:
            raise KISUsageError("기록되지 않은 임시 디렉터리는 채택하지 않습니다. 모든 사본을 보존하세요.")
        _assert_copy_metadata(stage, entries)
        _lock_inventory(stack, stage)
        destination_locked = True
        _assert_copy(stage, entries, stack)  # 부분 복사는 추측해서 덮어쓰지 않고 보존·거부한다.
        _advance(marker, transaction, "copied")
    if transaction["stage"] == "copied":
        if _present(stage):
            _assert_copy_metadata(stage, entries)
            if list(_identity(stage)) != transaction["stage_identity"]:
                raise KISUsageError("이전 임시 디렉터리가 기록과 다릅니다. 모든 사본을 보존하고 수동 점검하세요.")
            if not destination_locked:
                _lock_inventory(stack, stage)
                destination_locked = True
            _assert_copy(stage, entries, stack)
            _assert_source(cache, transaction, stack)
            if _present(state):
                if (state.is_symlink() or not state.is_dir() or any(state.iterdir())
                        or list(_identity(state)) != transaction["placeholder_identity"]):
                    raise KISUsageError("목적지 주문 디렉터리가 비어 있지 않습니다. 병합하지 않습니다.")
                state.rmdir()  # 오프라인에서 검증한 빈 자리만 제거한다.
            _rename_no_replace(stage, state)
            store._fsync_dir(state.parent)
        _assert_copy(state, entries, stack)
        if list(_identity(state)) != transaction["stage_identity"]:
            raise KISUsageError("설치된 주문 디렉터리가 기록과 다릅니다.")
        _advance(marker, transaction, "installed")
    if not destination_locked:
        _assert_copy_metadata(state, entries)
        if list(_identity(state)) != transaction["stage_identity"]:
            raise KISUsageError("설치된 주문 디렉터리가 기록과 다릅니다. 모든 사본을 보존하고 수동 점검하세요.")
        _lock_inventory(stack, state)
    _assert_copy(state, entries, stack)
    if list(_identity(state)) != transaction["stage_identity"]:
        raise KISUsageError("주문 state 디렉터리가 이전 중 교체되었습니다.")
    if transaction["stage"] == "installed":
        if _present(cache) and not cache.is_symlink():
            _assert_source(cache, transaction, stack)
            _rename_no_replace(cache, backup)
            store._fsync_dir(cache.parent)
        _assert_source(backup, transaction, stack)
        _advance(marker, transaction, "backed_up")
    if transaction["stage"] == "backed_up":
        _assert_source(backup, transaction, stack)
        if _requires_legacy_alias():
            if not _present(cache):
                cache.symlink_to(state, target_is_directory=True)
                store._fsync_dir(cache.parent)
            if not _shared_alias(state, cache):
                raise KISUsageError("이전 중 독립 캐시 경로가 생겼습니다. 모든 사본을 보존합니다.")
        elif _present(cache):
            raise KISUsageError("이전 중 독립 캐시 경로가 생겼습니다. 모든 사본을 보존합니다.")
        _advance(marker, transaction, "linked")
    _assert_source(backup, transaction, stack)
    _assert_copy(state, entries, stack)
    if _requires_legacy_alias() and not _shared_alias(state, cache):
        raise KISUsageError("완료 전 주문 디렉터리 별칭 검증에 실패했습니다.")
    _advance(marker, transaction, "committed")
    marker.unlink()
    store._fsync_dir(marker.parent)


def migrate_order_store_directory(state: Path, cache: Path, *, offline: bool) -> None:
    """모든 사용자를 중지한 운영자의 명시적 선언 아래에만 디렉터리를 이전·복구한다.

    이 선언은 구 프로세스의 재시작을 기술적으로 막지 않는다. 완료할 때까지 모든 계좌의
    구·신 프로세스와 자동 재시작을 중지해야 한다. 백업은 자동 삭제하지 않으며, 스키마나
    계좌 귀속을 바꾸지 않는다. 알 수 없는 중단 상태는 사본을 보존하고 거부한다.
    """
    if offline is not True:
        raise KISUsageError("--offline 은 모든 주문 저장소 사용자와 자동 재시작을 중지했다는 선언입니다.")
    _validate_roots(state, cache)
    state.parent.mkdir(parents=True, exist_ok=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    marker = state.parent / ".orders-migration.json"
    with _JournalLocks() as stack:
        stack.enter_context(store.OrderStore(path=state.parent / ".orders-layout", retention_days=0))
        _validate_roots(state, cache)
        if _present(marker):
            transaction = _load_transaction(marker, state, cache)
            _resume_migration(state, cache, marker, transaction, stack)
            return
        if _present(cache) and not cache.is_symlink():
            if not cache.is_dir():
                raise KISUsageError("기존 주문 캐시 경로가 실제 디렉터리가 아닙니다.")
            if _present(state) and any(state.iterdir()):
                raise KISUsageError("양쪽에 독립 주문 데이터가 있습니다. 자동 병합하지 않습니다.")
            _lock_inventory(stack, cache)
            entries = _inventory(cache, stack)
            operation = uuid.uuid4().hex
            _probe_alias(state, cache, operation)
            transaction = {
                "operation": operation, "state": str(state), "cache": str(cache), "stage": "prepared",
                "source_identity": list(_identity(cache)), "entries": entries,
                "file_identities": {name: list(_identity(cache / name)) for name in entries},
                "stage_identity": None,
                "placeholder_identity": list(_identity(state)) if _present(state) else None,
            }
            _write_transaction(marker, transaction)
            # 이미 원본 락을 잡았으므로 재개 경로에 이 소유권을 전달한다.
            _resume_migration(state, cache, marker, transaction, stack, source_locked=True)
            return
        if _present(cache) and not _shared_alias(state, cache):
            raise KISUsageError("알 수 없는 주문 디렉터리 별칭은 자동 복구하지 않습니다.")
        if not state.is_dir():
            raise KISUsageError("이전할 주문 저장 디렉터리가 없습니다. 일반 시작으로 초기화하세요.")
        _lock_inventory(stack, state)
        before = _inventory(state, stack)
        if _requires_legacy_alias() and not _present(cache):
            _probe_alias(state, cache, uuid.uuid4().hex)
            cache.symlink_to(state, target_is_directory=True)
            store._fsync_dir(cache.parent)
        if _inventory(state, stack) != before or (_requires_legacy_alias() and not _shared_alias(state, cache)):
            raise KISUsageError("복구 중 주문 디렉터리가 변경되었습니다.")
