"""재생성 가능한 캐시 아티팩트를 위한 보안 원자적 파일 쓰기(내부, 표준 라이브러리만).

부분 파일이 남지 않도록 같은 디렉터리의 임시파일에 쓰고 ``fsync`` 후 원자적
``os.replace`` 로 교체한다. 임시 이름은 :func:`tempfile.mkstemp` 로 예측 불가하게 만들어
고정된 재사용 tmp 경로(``path + ".tmp"``)의 심볼릭링크/선점 문제를 피하고, 파일 모드는
디렉터리 umask 를 믿지 않고 명시적으로 세운다.

이 헬퍼는 마스터/캐시 경로 전용이다. 주문 dedup 저장소(``store.py``)는 더 엄격한 자체
fsync 규율을 따로 가지며 이 헬퍼를 쓰지 않는다.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

__all__ = ["atomic_write_bytes", "xdg_cache_subdir", "xdg_config_subdir"]


def xdg_cache_subdir(*parts: str) -> Path:
    """``XDG_CACHE_HOME`` (없으면 ``~/.cache``) 아래의 하위 경로. 재생성 가능한 런타임 캐시 전용
    -- 토큰/마스터 캐시가 이 한 경로 규칙을 공유하도록 여기 한 곳에 둔다."""
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base).joinpath(*parts)


def xdg_config_subdir(*parts: str) -> Path:
    """``XDG_CONFIG_HOME`` (없으면 ``~/.config``) 아래의 하위 경로. 사용자가 편집하는 설정
    (자격증명/설정 파일) 전용 -- 재생성 가능한 캐시(:func:`xdg_cache_subdir`)와 XDG 규약대로 분리한다."""
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base).joinpath(*parts)


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes, *, mode: int = 0o600) -> None:
    """``data`` 를 ``path`` 에 원자적으로 쓴다(부분 파일 방지, 예측 불가 임시파일).

    같은 디렉터리에 :func:`tempfile.mkstemp` 로 임시파일을 만들어(=같은 파일시스템이라
    ``os.replace`` 가 원자적) ``data`` 를 쓰고 ``flush`` + ``fsync`` 한 뒤 ``os.replace`` 로
    교체한다. 파일 모드는 ``mode`` 로 명시적으로 세운다(디렉터리 umask 불신). 담는 디렉터리는
    내구성을 위해 best-effort 로 fsync 한다(플랫폼 미지원이면 무시 -- 재생성 가능한 캐시라
    커널 저장소만큼 엄격하지 않다). 어떤 실패에서도 임시파일을 지우고 예외를 다시 던진다."""
    target = Path(path)
    directory = target.parent
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")  # 예측 불가 임시 이름
    try:
        with os.fdopen(fd, "wb") as out:   # 성공하면 fd 소유권이 out 으로 이전
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, mode)                # 디렉터리 umask 를 믿지 않고 모드 명시
        os.replace(tmp, target)            # 원자적 교체
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)                 # 실패 시 임시파일 청소 -- 잔여물 없음
        raise
    _fsync_dir(directory)                  # best-effort: rename 내구성


def _fsync_dir(directory: Path) -> None:
    """디렉터리 엔트리를 fsync -- rename 을 내구성 있게(플랫폼 미지원이면 조용히 무시)."""
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover -- 일부 플랫폼은 디렉터리 열기 불가
        return
    try:
        os.fsync(dir_fd)
    except OSError:  # pragma: no cover -- 일부 플랫폼은 디렉터리 fsync 미지원
        pass
    finally:
        os.close(dir_fd)
