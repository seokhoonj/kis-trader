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
