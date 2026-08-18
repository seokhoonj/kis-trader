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
import errno
import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple, Self, cast

from .errors import KISError, UnsupportedSchemaVersionError
from .order import (
    ChangeActionFingerprint,
    Fingerprint,
    Side,
    decode_fingerprint,
    encode_fingerprint,
)
from .report import ExecutionReport, OrderStatus

try:
    import fcntl  # Unix 전용 -- 프로세스 간 단일라이터 락(advisory)
except ImportError:  # pragma: no cover -- 비-Unix
    fcntl = None  # type: ignore[assignment]

_KST = timezone(timedelta(hours=9))

#: 이 릴리스가 쓰는 스키마 버전.
#: v2: Fingerprint 에 credit_type/loan_date(신용주문). v3: session(미국 오버나이트 거래). v4: division(국내
#: 주문구분: 최유리/최우선/조건부). v5: board(국내 체결 보드 KRX/NXT/UN) 추가. v6: 리포트에
#: organization_number(국내 조직번호) 영속 -- 재기동 후 정정취소가 조직번호를 읽게(전엔 미영속 _raw
#: 에만 있어 재시작하면 취소 불가). v7: 리포트 레코드 키 submitted_at -> recorded_at(저장 시각임을
#: 정직하게; 지문 위치 형식은 그대로다). 구버전 레코드는 새 지문 필드가 기본값("", "regular", "KRX")으로
#: 채워지고(decode_fingerprint 가 뒤쪽 누락 슬롯을), 리포트는 dict.get 이 누락 키를, v6 이하의
#: submitted_at 키는 recorded_at 으로 매핑돼 그대로 읽힌다(하위호환 로드, _report_from_dict).
#: v8: in_flight 를 id 집합(list) -> {id: claim 시각(ISO8601)} 매핑으로 바꿔 각 claim 의 확보 시각을
#: 영속한다(재기동 후 in-flight 나이를 알아 재조회 정책에 쓴다). 구버전(v1..v7) 파일은 in_flight 가
#: list 라 로드 시 {id: ""}(시각 미상 폴백 앵커)로 마이그레이션된다. 지문 위치 형식도 v8 에서 14-슬롯
#: (파생 derivative_item)으로 늘었으나 구 13-슬롯 이하 레코드는 decode 가 뒤쪽 기본값으로 채워 그대로 읽는다.
#: 구 바이너리는 새 버전 파일을 손상이 아니라 미지원 버전으로 거부하게 해 오진단을 막는다.
#: v9: 리포트에 receipt_date(해외 예약 접수일자 RSVN_ORD_RCIT_DT) 영속 -- 재기동 후에도 해외 예약
#: 취소가 접수일자를 읽게(전엔 미영속 _raw 에만 있어 재시작하면 취소 불가). 지문 위치 형식도 v9 에서
#: 16-슬롯(예약 overseas_exchange·currency)으로 늘었으나 구 14-슬롯 이하 레코드는 decode 가 뒤쪽
#: 기본값으로 채워 그대로 읽고, 구버전 리포트의 누락 receipt_date 키는 None 으로 로드된다(하위호환).
_SCHEMA_VERSION = 9
#: 읽을 수 있는 스키마 버전 집합(이 밖은 UnsupportedSchemaVersionError 로 거부).
_READABLE_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9})
#: 완료(비-in-flight) 리포트 보존 기본 일수 -- 이 이후엔 정리(무한 성장 방지). client_order_id
#: 가 날짜를 포함하므로 같은 id 재전송 위험 창은 당일이라, 넉넉한 기본값이 dedup 을 약화하지 않는다.
_DEFAULT_RETENTION_DAYS = 7


class Binding(NamedTuple):
    """원자적으로 함께 심을 (리포트, 지문) 한 쌍 -- :meth:`OrderStore.record_change` 의 ``rebind``.
    "리포트 없는 지문"/"지문 없는 리포트" 같은 반쪽 상태를 표현 불가능하게 해 원자성을 구조로 만든다."""

    report: ExecutionReport
    fingerprint: Fingerprint


@dataclass(frozen=True, slots=True)
class Claimed:
    """새로 in-flight 를 확보했다 -- 전송해도 된다(이 변형만 와이어에 닿는다)."""


@dataclass(frozen=True, slots=True)
class Completed:
    """이미 완료된 주문이다 -- 이 ``report`` 를 그대로 replay 한다. 리포트는 **항상 존재**하므로
    "완료인데 리포트 없음" 은 구조적으로 표현 불가능하다(호출자의 방어적 재확인이 사라진다)."""

    report: ExecutionReport


@dataclass(frozen=True, slots=True)
class InFlight:
    """이미 전송됐고 결과가 미확인이다 -- 재조회를 요구한다(재전송 금지)."""


@dataclass(frozen=True, slots=True)
class Conflict:
    """같은 id 를 다른 지문(다른 주문)에 재사용했다 -- 거부한다. ``prior`` 는 원 id 가 완료 상태면
    그 리포트, in-flight 충돌이면 ``None``."""

    prior: ExecutionReport | None


#: :meth:`OrderStore.try_claim` 의 판별 결과(주문 전송 여부를 가르는 결정) -- 변형별로 필요한
#: 필드만 담아 "완료인데 리포트 없음" 같은 반쪽 상태를 표현 불가능하게 한다.
ClaimResult = Claimed | Completed | InFlight | Conflict


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
        self, path: str | Path | None = None, *, retention_days: int = _DEFAULT_RETENTION_DAYS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._retention = timedelta(days=retention_days) if retention_days > 0 else None
        # claim 시각의 시계 -- 주입 가능(테스트 결정성). 기본은 벽시계(KST).
        self._now = now if now is not None else (lambda: datetime.now(_KST))
        self._reports: dict[str, ExecutionReport] = {}
        self._fingerprints: dict[str, Fingerprint] = {}
        # client_order_id -> claim 시각(ISO8601). 구 저장소에서 로드된 항목은 시각 미상("")일 수 있다.
        self._in_flight: dict[str, str] = {}
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
    def try_claim(self, client_order_id: str, fingerprint: Fingerprint) -> ClaimResult:
        """한 락 안에서 상태를 판정하고 필요 시 in-flight 로 확보한다(원자적 check-and-claim).

        반환은 판별 결과다: :class:`Claimed` 전송 가능 / :class:`Completed` (report) replay /
        :class:`InFlight` 재조회 요구 / :class:`Conflict` (prior) 같은 id 다른 주문. 리포트가 있는
        변형에는 리포트가 항상 실려, 호출자가 "완료인데 리포트 없음" 을 다시 점검할 필요가 없다.
        """
        with self._lock:
            self._require_open()
            prior = self._reports.get(client_order_id)
            if prior is not None:
                if self._fingerprints.get(client_order_id) != fingerprint:
                    return Conflict(prior)
                return Completed(prior)
            if client_order_id in self._in_flight:
                if self._fingerprints.get(client_order_id) != fingerprint:
                    return Conflict(None)
                return InFlight()
            self._in_flight[client_order_id] = self._now().isoformat()
            self._fingerprints[client_order_id] = fingerprint
            self._save_locked()
            return Claimed()

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

    def claim_time_for(self, client_order_id: str) -> str | None:
        """이 id 의 in-flight claim 확보 시각(ISO8601). in-flight 가 아니면 None,
        구 저장소에서 로드돼 시각 미상이면 ""(폴백 앵커)."""
        with self._lock:
            return self._in_flight.get(client_order_id)

    def in_flight_change_for(self, original_client_order_id: str) -> str | None:
        """이 원주문을 겨눈 in-flight 변경(정정/취소) 동작의 request_id (없으면 None)."""
        with self._lock:
            for cid in self._in_flight:
                fp = self._fingerprints.get(cid)
                if isinstance(fp, ChangeActionFingerprint) \
                        and fp.original_client_order_id == original_client_order_id:
                    return cid
            return None

    # --- 상태 전이 ----------------------------------------------------
    def _bind_locked(self, report: ExecutionReport, fingerprint: Fingerprint) -> None:
        """락을 쥔 상태에서 한 주문의 (리포트, 지문)을 심고 in-flight 를 해제한다 -- 저장(persist)은
        호출자가 한다. 여러 바인딩을 한 번의 ``_save_locked`` 로 원자적으로 묶으려는 헬퍼."""
        self._reports[report.client_order_id] = report
        self._fingerprints[report.client_order_id] = fingerprint
        self._in_flight.pop(report.client_order_id, None)

    def record(self, report: ExecutionReport, fingerprint: Fingerprint) -> None:
        """접수 리포트를 기록하고 in-flight 를 해제(영속)."""
        with self._lock:
            self._require_open()
            self._bind_locked(report, fingerprint)
            self._save_locked()

    def record_change(
        self, report: ExecutionReport, fingerprint: Fingerprint, *,
        rebind: Binding | None = None,
    ) -> None:
        """변경(정정/취소) 결과를 기록하고, 필요하면 원주문 id 를 정정된 주문으로 **원자적으로**
        재바인딩한다(한 락, 한 번의 영속 쓰기).

        변경요청 리포트는 자기 ``request_id`` 아래, ``rebind`` 이 있으면 (재바인딩 리포트,
        지문)을 원 ``client_order_id`` 아래에 **함께** 커밋한다. 두 전이를 나눠 쓰면 그 사이
        크래시 시 request_id 만 완료로 남고 원 id 는 낡은 주문번호에 고착돼(재시도는 완료
        replay 로 조기반환) 복구 불가해지므로, 단일 ``_save_locked`` 로 묶는다.

        :meth:`record` 와 마찬가지로 각 id 의 in-flight 표시를 해제한다 -- request_id 와
        (있으면) 재바인딩 원 id 둘 다. 원 id 는 이미 완료 상태라 해제는 보통 no-op 이다."""
        with self._lock:
            self._require_open()
            self._bind_locked(report, fingerprint)
            if rebind is not None:
                self._bind_locked(rebind.report, rebind.fingerprint)
            self._save_locked()

    def clear_in_flight(self, client_order_id: str) -> None:
        """주문이 확실히 접수 안 됐을 때(거부 등) in-flight 와 지문을 해제(영속)."""
        with self._lock:
            self._require_open()
            self._in_flight.pop(client_order_id, None)
            self._fingerprints.pop(client_order_id, None)
            self._save_locked()

    def _require_open(self) -> None:
        if self._closed:
            raise KISError("OrderStore 가 닫혔다(close 이후). 단일라이터 락이 없으니 새 인스턴스를 열어라.")

    def close(self) -> None:
        """단일라이터 락을 해제하고 이후 연산을 막는다(종료 시/명시적). 여러 번 호출 안전.

        close 이후에는 단일라이터 보장이 사라지므로 :meth:`try_claim` 등 변경 연산은
        :class:`KISError` 로 거부된다(보장 상실을 조용히 넘기지 않는다). 락을 쥐고 수행해,
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
            raise KISError(
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
            raise KISError(
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
            if cid not in self._in_flight and report.recorded_at < cutoff
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
            "in_flight": dict(self._in_flight),
            "fingerprints": {cid: encode_fingerprint(fp) for cid, fp in self._fingerprints.items()},
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
            raise KISError(
                f"주문 dedup 저장소가 손상됐다: {self._path}. 자동 복구하지 않는다"
                f"(빈 상태 시작은 중복 체결 위험). 파일을 점검/재구성한 뒤 재시작하라."
            ) from err
        if not isinstance(data, dict):
            # 루트가 JSON object 가 아니면(배열/스칼라 등) data.get 이 AttributeError 로 새는 대신
            # 손상으로 fail-closed -- 빈 상태 시작은 dedup 장벽을 지워 중복 체결 위험이므로.
            raise KISError(
                f"주문 dedup 저장소 루트가 JSON object 가 아니다: {self._path}. 자동 복구하지 않는다"
                f"(빈 상태 시작은 중복 체결 위험). 파일을 점검한 뒤 재시작하라."
            )
        version = data.get("schema_version")
        if version not in _READABLE_SCHEMA_VERSIONS:
            raise UnsupportedSchemaVersionError(
                f"주문 dedup 저장소 스키마 버전 미지원: {version!r} "
                f"(읽을 수 있는 버전 {sorted(_READABLE_SCHEMA_VERSIONS)}). {self._path}"
            )
        try:
            raw_in_flight = data.get("in_flight", {})
            if isinstance(raw_in_flight, list):        # v1..v7: id 리스트 -> 시각 미상("")
                self._in_flight = {str(cid): "" for cid in raw_in_flight}
            else:                                       # v8: {id: claim 시각}
                self._in_flight = {str(k): str(v) for k, v in raw_in_flight.items()}
            self._fingerprints = {
                cid: decode_fingerprint(fp) for cid, fp in data.get("fingerprints", {}).items()
            }
            self._reports = {cid: _report_from_dict(d) for cid, d in data.get("reports", {}).items()}
        except (ValueError, InvalidOperation, TypeError, KeyError, AttributeError) as err:
            # 스키마는 맞지만 레코드 값이 손상(잘못된 status/수량/날짜 등) -> 도메인 에러로 fail-closed.
            raise KISError(
                f"주문 dedup 저장소 레코드가 손상됐다: {self._path}. 자동 복구하지 않는다"
                f"(빈 상태 시작은 중복 체결 위험). 파일을 점검한 뒤 재시작하라."
            ) from err


#: 디렉터리 fsync 를 지원하지 않는 플랫폼/파일시스템이 내는 errno -- 이때만 무시한다. EIO/ENOSPC
#: 같은 진짜 내구성 실패는 여기 없으므로 그대로 전파돼 '저장됨' 주장이 거짓이 되지 않는다.
_UNSUPPORTED_FSYNC_ERRNOS = frozenset({errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP})


def _fsync_dir(directory: Path) -> None:
    """디렉터리 엔트리를 fsync -- rename 이 크래시에도 살아남게. 디렉터리 fsync 미지원(EINVAL/ENOTSUP)
    만 무시하고, EIO/ENOSPC 등 진짜 내구성 실패는 삼키지 않고 올린다(거짓 '저장됨' 방지)."""
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover -- 디렉터리를 열 수 없는 드문 플랫폼
        return
    try:
        os.fsync(dir_fd)
    except OSError as err:
        if err.errno not in _UNSUPPORTED_FSYNC_ERRNOS:
            raise
    finally:
        os.close(dir_fd)


def _report_to_dict(report: ExecutionReport) -> dict[str, object]:
    # raw 는 벤더 와이어 DTO -- 영속하지 않는다(재로드 시 raw={} 로 복원, 아래 명시).
    return {
        "client_order_id": report.client_order_id,
        "order_id": report.order_id,
        "symbol": report.symbol,
        "side": report.side,
        "status": report.status.value,
        "filled_quantity": str(report.filled_quantity),
        "average_price": None if report.average_price is None else str(report.average_price),
        "recorded_at": report.recorded_at.isoformat(),
        "organization_number": report.organization_number,
        "receipt_date": report.receipt_date,
    }


def _report_from_dict(report_data: dict[str, object]) -> ExecutionReport:
    average_price = report_data["average_price"]
    # v7 은 "recorded_at" 키, v6 이하는 "submitted_at" 키를 쓴다 -- 구 저장소를 재작성 없이 열도록
    # 옛 키를 recorded_at 으로 매핑한다(온-디스크 스키마가 바뀌면 하위호환 로드 경로를 함께 둔다).
    recorded_raw = report_data.get("recorded_at", report_data.get("submitted_at"))
    return ExecutionReport(
        client_order_id=str(report_data["client_order_id"]),
        order_id=None if report_data["order_id"] is None else str(report_data["order_id"]),
        symbol=str(report_data["symbol"]),
        side=cast(Side, str(report_data["side"])),
        status=OrderStatus(str(report_data["status"])),
        filled_quantity=Decimal(str(report_data["filled_quantity"])),
        average_price=None if average_price is None else Decimal(str(average_price)),
        recorded_at=datetime.fromisoformat(str(recorded_raw)),
        # 구버전(v5 이하) 레코드엔 없으니 누락 시 None (해당 주문은 재기동 후 취소 불가 -- 종전과 동일).
        organization_number=(
            None if report_data.get("organization_number") is None
            else str(report_data["organization_number"])
        ),
        # 구버전(v8 이하) 레코드엔 없으니 누락 시 None (해당 해외 예약은 재기동 후 취소 불가 -- 종전과 동일).
        receipt_date=(
            None if report_data.get("receipt_date") is None
            else str(report_data["receipt_date"])
        ),
        # raw 는 영속되지 않음 -- 재로드된 리포트는 raw={} (빈 와이어 바디와 구별 안 됨).
    )
