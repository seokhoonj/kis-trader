"""로컬 주문 멱등 저장소 -- :class:`OrderStore`.

KIS Open API에는 네이티브 멱등키(서버 dedup)가 **없다**(하베스트로 검증). 따라서
중복 전송 방지를 클라이언트가 책임진다. 각 ``client_order_id`` 는 세 상태를 가진다:

- **미기록**: 처음 보는 주문 -> 전송 가능.
- **in-flight**: 전송했으나 결과 미확인(타임아웃 등) -> **재전송 금지**, 재조회 필요.
- **완료**: 접수 리포트 보유 -> 재요청 시 그 리포트를 그대로 반환(멱등 replay).

check-and-claim(:meth:`try_claim`)은 하나의 락 안에서 원자적으로 이뤄져, 한 저장소를
공유하는 여러 스레드/`Orders` 인스턴스가 동시에 같은 id를 전송하는 이중체결을 막는다.
또한 각 id의 **요청 지문**을 함께 보관해, 같은 id를 *다른* 주문에 재사용하면 조용히
replay 하지 않고 충돌로 거부한다.

선택적으로 ``path`` 를 주면 원자적으로 JSON 영속화해 프로세스 재시작 뒤에도 dedup이
유지된다(스키마 버전 포함).
"""

from __future__ import annotations

import contextlib
import enum
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Self

from .errors import KisError, UnsupportedSchemaVersionError
from .order import Fingerprint
from .report import ExecutionReport, OrderStatus

try:
    import fcntl  # Unix 전용 -- 프로세스 간 단일라이터 락(advisory)
except ImportError:  # pragma: no cover -- 비-Unix
    fcntl = None  # type: ignore[assignment]

_KST = timezone(timedelta(hours=9))

#: 이 릴리스가 쓰는 스키마 버전.
_SCHEMA_VERSION = 1
#: 읽을 수 있는 스키마 버전 집합(이 밖은 UnsupportedSchemaVersionError 로 거부).
_READABLE_SCHEMA_VERSIONS = frozenset({1})
#: 완료(비-in-flight) 리포트 보존 기본 일수 -- 이 이후엔 정리(무한 성장 방지). client_order_id
#: 가 날짜를 포함하므로 같은 id 재전송 위험 창은 당일이라, 넉넉한 기본값이 dedup 을 약화하지 않는다.
_DEFAULT_RETENTION_DAYS = 7


class ClaimOutcome(enum.Enum):
    """:meth:`OrderStore.try_claim` 의 결과(주문 전송 여부를 가르는 결정)."""

    CLAIMED = "claimed"        # 새로 in-flight 확보 -> 전송해도 됨
    IN_FLIGHT = "in_flight"    # 이미 전송됐고 결과 미확인 -> 재조회 요구
    COMPLETED = "completed"    # 이미 완료 리포트 있음 -> replay
    CONFLICT = "conflict"      # 같은 id, 다른 지문 -> 거부


class OrderStore:
    """``client_order_id`` -> (상태, 지문) 의 스레드 안전 로컬 저장소.

    **리소스 수명**: ``path`` 를 주면 프로세스 간 이중전송을 막기 위해 OS 단일라이터 락
    (``fcntl.flock``)을 이 인스턴스 수명 동안 **보유**한다. 다 쓰면 :meth:`close` 를
    부르거나 ``with OrderStore(path=...) as store:`` 로 써서 락을 놓아야 한다(안 놓으면
    같은 프로세스의 다른 인스턴스가 열리지 않는다). 락은 Unix 에서만 강제되므로 비-Unix
    에서 path-backed 스토어는 생성 시 거부된다.

    ``retention_days`` 는 완료(비-in-flight) 리포트를 저장에 유지할 일수다. 이보다 오래된
    완료 리포트만 정리되고 **in-flight 는 나이와 무관하게 절대 정리하지 않는다**(멱등 장벽
    유지). ``<= 0`` 이면 정리 자체를 끈다(무한 보존).
    """

    def __init__(
        self, path: str | Path | None = None, *, retention_days: int = _DEFAULT_RETENTION_DAYS
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._retention = timedelta(days=retention_days) if retention_days > 0 else None
        self._reports: dict[str, ExecutionReport] = {}
        self._fingerprints: dict[str, Fingerprint] = {}
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()
        self._process_lock_fd: int | None = None
        self._closed = False
        if self._path is not None:
            self._acquire_single_writer_lock()  # 프로세스 간 이중전송 방지
            try:
                if self._path.exists():
                    self._load()
            except BaseException:
                self.close()  # 로드 실패 시 락 fd 를 누수하지 않는다(회복 차단 방지)
                raise

    # --- 원자적 check-and-claim(핵심 안전 연산) ----------------------
    def try_claim(
        self, client_order_id: str, fingerprint: Fingerprint
    ) -> tuple[ClaimOutcome, ExecutionReport | None]:
        """한 락 안에서 상태를 판정하고 필요 시 in-flight 로 확보한다.

        반환: ``(CLAIMED, None)`` 전송 가능 / ``(COMPLETED, report)`` replay /
        ``(IN_FLIGHT, None)`` 재조회 요구 / ``(CONFLICT, report|None)`` 같은 id 다른 주문.
        """
        with self._lock:
            self._require_open()
            prior = self._reports.get(client_order_id)
            if prior is not None:
                if self._fingerprints.get(client_order_id) != fingerprint:
                    return ClaimOutcome.CONFLICT, prior
                return ClaimOutcome.COMPLETED, prior
            if client_order_id in self._in_flight:
                if self._fingerprints.get(client_order_id) != fingerprint:
                    return ClaimOutcome.CONFLICT, None
                return ClaimOutcome.IN_FLIGHT, None
            self._in_flight.add(client_order_id)
            self._fingerprints[client_order_id] = fingerprint
            self._save_locked()
            return ClaimOutcome.CLAIMED, None

    # --- 조회 ---------------------------------------------------------
    def report_for(self, client_order_id: str) -> ExecutionReport | None:
        """완료된 주문의 리포트(없으면 None)."""
        with self._lock:
            return self._reports.get(client_order_id)

    def is_in_flight(self, client_order_id: str) -> bool:
        """전송됐으나 결과 미확인 상태인가."""
        with self._lock:
            return client_order_id in self._in_flight

    def fingerprint_for(self, client_order_id: str) -> Fingerprint | None:
        """이 id로 기록된 요청 지문(재조회 매칭용). 없으면 None."""
        with self._lock:
            return self._fingerprints.get(client_order_id)

    # --- 상태 전이 ----------------------------------------------------
    def record(self, report: ExecutionReport, fingerprint: Fingerprint) -> None:
        """접수 리포트를 기록하고 in-flight 를 해제(영속)."""
        with self._lock:
            self._require_open()
            self._reports[report.client_order_id] = report
            self._fingerprints[report.client_order_id] = fingerprint
            self._in_flight.discard(report.client_order_id)
            self._save_locked()

    def clear_in_flight(self, client_order_id: str) -> None:
        """주문이 확실히 접수 안 됐을 때(거부 등) in-flight 와 지문을 해제(영속)."""
        with self._lock:
            self._require_open()
            self._in_flight.discard(client_order_id)
            self._fingerprints.pop(client_order_id, None)
            self._save_locked()

    def _require_open(self) -> None:
        if self._closed:
            raise KisError("OrderStore 가 닫혔다(close 이후). 단일라이터 락이 없으니 새 인스턴스를 열어라.")

    def close(self) -> None:
        """단일라이터 락을 해제하고 이후 연산을 막는다(종료 시/명시적). 여러 번 호출 안전.

        close 이후에는 단일라이터 보장이 사라지므로 :meth:`try_claim` 등 변경 연산은
        :class:`KisError` 로 거부된다(보장 상실을 조용히 넘기지 않는다). 락을 쥐고 수행해,
        진행 중인 ``_save_locked`` 가 끝난 **뒤에** 락 fd 를 놓는다(다른 프로세스가 절반
        쓰인 상태를 읽지 않게).
        """
        with self._lock:
            self._closed = True
            if self._process_lock_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(self._process_lock_fd)
                self._process_lock_fd = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- 프로세스 간 단일라이터 락 -----------------------------------
    def _acquire_single_writer_lock(self) -> None:
        """이 프로세스만 저장소를 쓰도록 advisory 락을 잡는다(프로세스 간 이중전송 방지).

        Unix 에서만 강제된다(``fcntl.flock``). 비-Unix 에선 보장 불가 -- 이 경우 단일
        프로세스로 운용해야 한다. 락은 이 인스턴스 수명 동안 유지된다.
        """
        assert self._path is not None
        if fcntl is None:  # pragma: no cover -- 비-Unix
            # fail-closed: 락을 강제할 수 없는 플랫폼에선 보장이 없다고 조용히 넘기지 않는다.
            raise KisError(
                "이 플랫폼(비-Unix)은 파일 락(fcntl)을 지원하지 않아 프로세스 간 단일라이터 "
                "보장을 강제할 수 없다. path-backed OrderStore 는 Unix 에서만 안전하다."
            )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as err:
            os.close(fd)
            raise KisError(
                f"주문 dedup 저장소가 다른 프로세스에 의해 이미 열려 있다: {self._path}. "
                f"OrderStore 는 단일 프로세스 전용이다(프로세스 간 이중전송 방지)."
            ) from err
        self._process_lock_fd = fd

    def _prune_locked(self) -> None:
        """보존 창을 넘긴 **완료(비-in-flight)** 리포트를 정리해 무한 성장을 막는다.
        in-flight 는 절대 정리하지 않는다(반드시 재조회로 확정돼야 하므로)."""
        if self._retention is None:
            return
        cutoff = datetime.now(_KST) - self._retention
        stale = [
            cid for cid, report in self._reports.items()
            if cid not in self._in_flight and report.submitted_at < cutoff
        ]
        for cid in stale:
            del self._reports[cid]
            self._fingerprints.pop(cid, None)

    # --- 영속화(원자적 + 내구성 JSON) --------------------------------
    def _save_locked(self) -> None:
        """호출자가 ``self._lock`` 을 쥔 상태에서만 부른다.

        임시파일에 쓰고 ``fsync`` 후 원자적 ``os.replace``, 디렉터리도 ``fsync`` 한다 --
        전원차단 뒤에도 방금 만든 claim 이 디스크에 남아 재시작 후 재전송을 막게(내구성).
        """
        if self._path is None:
            return
        self._prune_locked()
        data = {
            "schema_version": _SCHEMA_VERSION,
            "in_flight": sorted(self._in_flight),
            "fingerprints": {cid: list(fp) for cid, fp in self._fingerprints.items()},
            "reports": {cid: _report_to_dict(r) for cid, r in self._reports.items()},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 인스턴스/프로세스마다 유니크한 임시파일 -> 공유 tmp 이름 clobber 방지.
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            file = os.fdopen(fd, "w", encoding="utf-8")  # 성공하면 fd 소유권이 file 로 이전
        except BaseException:
            os.close(fd)  # fdopen 이 fd 를 못 가져갔을 때만 -- 누수 방지
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        try:
            with file:
                json.dump(data, file, ensure_ascii=False, indent=1)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, self._path)  # 원자적 교체
            _fsync_dir(self._path.parent)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _load(self) -> None:
        assert self._path is not None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            # fail-closed: 빈 상태로 시작하면 dedup 장벽이 사라져 중복체결 위험.
            raise KisError(
                f"주문 dedup 저장소가 손상됐다: {self._path}. 자동 복구하지 않는다"
                f"(빈 상태 시작은 중복 체결 위험). 파일을 점검/재구성한 뒤 재시작하라."
            ) from err
        version = data.get("schema_version")
        if version not in _READABLE_SCHEMA_VERSIONS:
            raise UnsupportedSchemaVersionError(
                f"주문 dedup 저장소 스키마 버전 미지원: {version!r} "
                f"(읽을 수 있는 버전 {sorted(_READABLE_SCHEMA_VERSIONS)}). {self._path}"
            )
        try:
            self._in_flight = set(data.get("in_flight", []))
            self._fingerprints = {cid: Fingerprint(*fp) for cid, fp in data.get("fingerprints", {}).items()}
            self._reports = {cid: _report_from_dict(d) for cid, d in data.get("reports", {}).items()}
        except (ValueError, InvalidOperation, TypeError, KeyError, AttributeError) as err:
            # 스키마는 맞지만 레코드 값이 손상(잘못된 status/수량/날짜 등) -> 도메인 에러로 fail-closed.
            raise KisError(
                f"주문 dedup 저장소 레코드가 손상됐다: {self._path}. 자동 복구하지 않는다"
                f"(빈 상태 시작은 중복 체결 위험). 파일을 점검한 뒤 재시작하라."
            ) from err


def _fsync_dir(directory: Path) -> None:
    """디렉터리 엔트리를 fsync -- rename 이 크래시에도 살아남게(플랫폼 미지원이면 무시)."""
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover
        return
    try:
        os.fsync(dir_fd)
    except OSError:  # pragma: no cover -- 일부 플랫폼은 디렉터리 fsync 미지원
        pass
    finally:
        os.close(dir_fd)


def _report_to_dict(r: ExecutionReport) -> dict[str, object]:
    # raw 는 벤더 와이어 DTO -- 영속하지 않는다(재로드 시 raw={} 로 복원, 아래 명시).
    return {
        "client_order_id": r.client_order_id,
        "order_id": r.order_id,
        "symbol": r.symbol,
        "side": r.side,
        "status": r.status.value,
        "filled_quantity": str(r.filled_quantity),
        "average_price": None if r.average_price is None else str(r.average_price),
        "submitted_at": r.submitted_at.isoformat(),
    }


def _report_from_dict(d: dict[str, object]) -> ExecutionReport:
    avg = d["average_price"]
    return ExecutionReport(
        client_order_id=str(d["client_order_id"]),
        order_id=None if d["order_id"] is None else str(d["order_id"]),
        symbol=str(d["symbol"]),
        side=str(d["side"]),
        status=OrderStatus(str(d["status"])),
        filled_quantity=Decimal(str(d["filled_quantity"])),
        average_price=None if avg is None else Decimal(str(avg)),
        submitted_at=datetime.fromisoformat(str(d["submitted_at"])),
        # raw 는 영속되지 않음 -- 재로드된 리포트는 raw={} (빈 와이어 바디와 구별 안 됨).
    )
