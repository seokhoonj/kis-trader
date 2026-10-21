"""로컬 주문 멱등 저장소 -- :class:`OrderStore`.

KIS Open API 주문 엔드포인트에는 네이티브 멱등키(서버 dedup)가 **없다** -- 같은 주문을 두 번
보내면 두 번 접수된다. 따라서 중복 전송 방지를 클라이언트가 책임지며, 이 저장소가 그 장벽이다. 각 ``client_order_id`` 는 세 상태를 가진다:

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
from decimal import InvalidOperation
from pathlib import Path
from types import ModuleType
from typing import Literal, NamedTuple, Self, cast

from ._internal._wire import required_decimal
from .errors import KISError, KISUsageError, UnsupportedSchemaVersionError
from .order import (
    ChangeActionFingerprint,
    Fingerprint,
    Side,
    decode_fingerprint,
    encode_fingerprint,
)
from .report import ExecutionReport, OrderStatus
from .transport import Environment

try:
    import fcntl  # Unix 전용 -- 프로세스 간 단일라이터 락(advisory)
except ImportError:  # pragma: no cover -- 비-Unix
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # Windows 전용 표준 라이브러리
    msvcrt = None  # type: ignore[assignment]

# Windows CRT 의 같은 프로세스 내 중복 핸들은 별도 배제한다. POSIX flock/fork 에는 적용하지 않는다.
_locked_paths: set[str] = set()
_locked_paths_lock = threading.Lock()

_KST = timezone(timedelta(hours=9))

#: 이 릴리스가 쓰는 스키마 버전.
#: v2: Fingerprint 에 credit_type/loan_date(신용주문). v3: session(미국 오버나이트 거래). v4: division(국내
#: 주문구분: 최유리/최우선/조건부). v5: board (현 exchange, 국내 체결 보드 KRX/NXT/UN) 추가. v6: 리포트에
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
#: v10: 지문 위치 형식이 19-슬롯(미국주식 algo 전략·시작·종료)으로 늘었으나 구 16-슬롯 이하 레코드는
#: decode 가 뒤쪽 기본값("")으로 채워 그대로 읽는다(비-algo 주문은 dedup 정체성 불변). 리포트 형상은
#: v9 과 동일하다.
#: v11: 저장소 전체의 불변 계좌·상품·환경 귀속(scope). null 은 귀속 미확인이다.
#: v12: 최초 claim 시각과 출처가 구분된 레거시 조회 기준 시각을 항목별로 보존한다.
_SCHEMA_VERSION = 12
#: 읽을 수 있는 스키마 버전 집합(이 밖은 UnsupportedSchemaVersionError 로 거부).
_READABLE_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12})
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


@dataclass(frozen=True, slots=True)
class PriorChangeInFlight:
    """같은 원주문을 겨눈 **다른** request_id 의 변경(정정/취소)이 이미 in-flight 다 -- 이 변경은
    거부한다(두 변경이 원 ODNO 상태를 경합하면 확정 불가). ``other_request_id`` 로 reconcile 하라."""

    other_request_id: str


@dataclass(frozen=True, slots=True)
class StaleOriginal:
    """원주문이 기대한 세대와 다르다 -- 새 바인딩 기준으로 다시 계산한 뒤 재시도해야 한다."""

    current_fingerprint: Fingerprint | None


#: :meth:`OrderStore.try_claim` / :meth:`try_claim_change` 의 판별 결과(주문 전송 여부를 가르는
#: 결정) -- 변형별로 필요한 필드만 담아 "완료인데 리포트 없음" 같은 반쪽 상태를 표현 불가능하게 한다.
ClaimResult = Claimed | Completed | InFlight | Conflict | PriorChangeInFlight | StaleOriginal


class OrderStore:
    """``client_order_id`` -> (상태, 지문) 의 스레드 안전 로컬 저장소.

    **리소스 수명**: ``path`` 를 주면 프로세스 간 이중전송을 막기 위해 OS 단일라이터 락
    (POSIX ``fcntl.flock`` / Windows ``msvcrt.locking``)을 이 인스턴스 수명 동안 **보유**한다. 다 쓰면 :meth:`close` 를
    부르거나 ``with OrderStore(path=...) as store:`` 로 써서 락을 놓아야 한다(안 놓으면
    같은 프로세스의 다른 인스턴스가 열리지 않는다). 지원 파일 잠금 백엔드가 없으면
    경로 기반 저장소는 생성 시 거부된다.

    ``retention_days`` 는 완료(비-in-flight) 리포트를 저장에 유지할 일수다. 이보다 오래된
    완료 리포트만 정리되고 **in-flight 는 나이와 무관하게 절대 정리하지 않는다**(멱등 장벽
    유지). ``<= 0`` 이면 정리 자체를 끈다(무한 보존).

    :meth:`bind_scope` 는 전체 기록을 한 계좌·상품·환경에 불변 귀속한다. 미귀속 상태에서도
    독립 claim 연산은 가능하지만, 기존 기록의 귀속 선언은 명시적 인수 확인을 요구한다.

    이 인스턴스는 ``KISClient(store=...)`` 로 세션에 넘겨 영속 dedup 을 켜는 용도다(권장).
    claim 결과 타입(:class:`Claimed`/:class:`Completed`/:class:`InFlight`/:class:`Conflict`/
    :class:`PriorChangeInFlight`)과 :class:`Binding` 은 내부용이라 공개 표면에 두지 않는다.
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
        self._submission_anchors: dict[str, tuple[str | None, str | None, str | None]] = {}
        self._recovery_generations: dict[str, object] = {}
        self._lock = threading.Lock()
        self._process_lock_fd: int | None = None
        self._process_lock_backend: ModuleType | None = None
        self._process_lock_key: str | None = None
        self._closed = False
        self._scope: tuple[str, str, Environment] | None = None
        if self._path is not None:
            self._acquire_single_writer_lock()  # 프로세스 간 이중전송 방지
            try:
                if self._path.exists():
                    self._load()
            except BaseException:
                self.close()  # 로드 실패 시 락 fd 를 누수하지 않는다(회복 차단 방지)
                raise

    def bind_scope(
        self, *, cano: str, product_code: str, environment: Environment,
        adopt_legacy: bool = False,
    ) -> None:
        """저장소 전체를 한 계좌·상품·환경에 불변 귀속한다. 같은 귀속은 아무것도 쓰지 않는다.

        기록이 있는 미귀속 저장소는 기본 거부한다. ``adopt_legacy=True`` 는 호출자가 전체 기록의
        소유 계좌를 별도 확인했다는 선언이며 자동 검증이 아니다. 귀속 저장 중에는 보존기간 정리를
        하지 않는다. 영속화 실패 시 실제 디스크 상태를 확인하도록 닫고 재개방을 요구한다.
        """
        persistence_failed = False
        try:
            with self._lock:
                self._require_open()
                if not isinstance(adopt_legacy, bool):
                    raise KISUsageError("adopt_legacy 는 명시적 불리언(True/False)이어야 한다.")
                if not isinstance(cano, str) or not cano or not isinstance(product_code, str) or not product_code:
                    raise KISUsageError("저장소 귀속에는 비어 있지 않은 계좌·상품 문자열이 필요하다.")
                if environment not in ("real", "paper"):
                    raise KISUsageError("저장소 귀속 환경은 real/paper 여야 한다.")
                scope = (cano, product_code, environment)
                if self._scope is not None:
                    if self._scope != scope:
                        raise KISUsageError("다른 계좌·상품·환경에 귀속된 OrderStore 다. 별도 저장소를 사용하라.")
                    return
                if (self._reports or self._fingerprints or self._in_flight) and not adopt_legacy:
                    raise KISUsageError(
                        "기존 기록의 계좌 귀속을 확인할 수 없다. 전체 기록의 소유를 확인한 뒤 "
                        "bind_scope(..., adopt_legacy=True) 로 명시적으로 인수하라.")
                self._scope = scope
                try:
                    self._save_locked(prune=False)
                except BaseException:
                    self._closed = True              # 잠금을 놓기 전 다른 사용을 차단한다
                    persistence_failed = True
                    raise
        except BaseException:
            if persistence_failed:
                self.close()                         # 비재진입 mutex 밖에서 파일 잠금을 해제한다
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
            prior = self._classify_locked(client_order_id, fingerprint)
            if prior is not None:
                return prior
            claimed_at = _aware_anchor_timestamp(self._now().isoformat())
            self._in_flight[client_order_id] = claimed_at
            self._submission_anchors[client_order_id] = (claimed_at, None, claimed_at)
            self._recovery_generations[client_order_id] = object()
            self._fingerprints[client_order_id] = fingerprint
            self._save_locked()
            return Claimed()

    def try_claim_change(
        self, request_id: str, fingerprint: ChangeActionFingerprint,
        *, original_client_order_id: str,
        expected_original: Binding | None = None,
    ) -> ClaimResult:
        """정정/취소 전용 원자 check-and-claim: "같은 원주문을 겨눈 다른 in-flight 변경이 있는가"
        검사와 request_id 확보를 **한 락** 안에서 함께 한다.

        두 연산을 별도 락으로 나누면(과거 구현: ``in_flight_change_for`` 후 ``try_claim``) 같은
        원주문에 대한 두 변경이 둘 다 각자의 검사를 통과해 둘 다 와이어로 나가는 TOCTOU 경합이 난다.
        반환: :class:`PriorChangeInFlight` (다른 변경 미확인 -> 거부) / :class:`StaleOriginal`
        (원주문 세대 변경 -> 재계산 필요) / :class:`Claimed`/`Completed`/`InFlight`/`Conflict`
        (:meth:`try_claim` 과 동일 의미, 같은 request_id 재시도 처리 포함)."""
        with self._lock:
            self._require_open()
            for cid in self._in_flight:
                if cid == request_id:
                    continue  # 같은 request_id 재시도는 아래 멱등 분류가 처리한다(거부 아님)
                other = self._fingerprints.get(cid)
                if isinstance(other, ChangeActionFingerprint) \
                        and other.original_client_order_id == original_client_order_id:
                    return PriorChangeInFlight(cid)
            current_report = self._reports.get(original_client_order_id)
            current_fingerprint = self._fingerprints.get(original_client_order_id)
            prior = self._classify_locked(request_id, fingerprint)
            if prior is not None:
                return prior
            if expected_original is not None and (
                    current_fingerprint != expected_original.fingerprint
                    or current_report is None
                    or current_report.order_id != expected_original.report.order_id
                    or current_report.filled_quantity != expected_original.report.filled_quantity):
                return StaleOriginal(current_fingerprint)
            claimed_at = _aware_anchor_timestamp(self._now().isoformat())
            self._in_flight[request_id] = claimed_at
            self._submission_anchors[request_id] = (claimed_at, None, claimed_at)
            self._recovery_generations[request_id] = object()
            self._fingerprints[request_id] = fingerprint
            self._save_locked()
            return Claimed()

    def _classify_locked(
        self, client_order_id: str, fingerprint: Fingerprint
    ) -> ClaimResult | None:
        """락 보유 중 현재 상태를 판정한다(쓰기 없음). ``None`` = 미확보(claim 가능).
        :meth:`try_claim` 과 :meth:`classify` 가 공유해 두 경로의 판별이 어긋나지 않게 한다."""
        prior = self._reports.get(client_order_id)
        if prior is not None:
            if self._fingerprints.get(client_order_id) != fingerprint:
                return Conflict(prior)
            return Completed(prior)
        if client_order_id in self._in_flight:
            if self._fingerprints.get(client_order_id) != fingerprint:
                return Conflict(None)
            return InFlight()
        return None

    def classify(self, client_order_id: str, fingerprint: Fingerprint) -> ClaimResult:
        """읽기 전용 원자 판별 -- :meth:`try_claim` 과 **같은** 분류(Completed/Conflict/InFlight)를
        돌려주되 확보(쓰기)는 하지 않는다. 미확보(claim 가능)면 :class:`Claimed`. place() 의 멱등
        replay 빠른길이 실패 가능 게이트 전에 **한 락**으로 상태를 보도록 -- 여러 조회를 쪼개 찢긴
        상태를 보는 것을 막는다."""
        with self._lock:
            self._require_open()
            return self._classify_locked(client_order_id, fingerprint) or Claimed()

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

    def binding_for(self, client_order_id: str) -> Binding | None:
        """확정된 주문의 (리포트, 지문)을 **한 락**으로 원자 스냅샷한다 -- 둘이 항상 같은 세대다.

        정정/취소는 잔량(지문 수량 - 리포트 체결량)을 이 쌍에서 계산해 와이어에 싣는다. 리포트와
        지문을 각각 (:meth:`report_for` 후 :meth:`fingerprint_for`) 읽으면 그 사이 :meth:`record_change`
        의 원자 리바인드가 끼어들어 옛 지문 + 새 리포트처럼 세대가 어긋난 쌍을 볼 수 있고, 그러면
        잘못된 잔량이 취소/정정 와이어로 나간다. 둘 다 있을 때만 :class:`Binding`, 하나라도 없으면
        ``None`` (확정 안 된 주문 -- in-flight 는 지문만 있어 여기선 ``None``, 호출자가 fail-closed)."""
        with self._lock:
            report = self._reports.get(client_order_id)
            fingerprint = self._fingerprints.get(client_order_id)
            if report is None or fingerprint is None:
                return None
            return Binding(report, fingerprint)

    def snapshot_for(
        self, client_order_id: str
    ) -> tuple[ExecutionReport | None, Fingerprint | None]:
        """(리포트, 지문)을 **한 락**으로 원자 스냅샷한다 -- :meth:`binding_for` 와 달리 **둘 중 하나가
        없어도**(리포트 None 인 in-flight/타임아웃 복구 포함) 그대로 돌려준다. reconcile 이 리포트와 지문을
        같은 세대로 읽고(:meth:`report_for` 후 :meth:`fingerprint_for` 로 따로 읽으면 그 사이 동시 정정/취소
        재바인딩이 끼어들어 세대가 어긋난다), 이 스냅샷을 ``expected`` 로 :meth:`record_if_unchanged` 에
        넘겨 읽기~기록 사이의 재바인딩을 덮어쓰지 않게 한다."""
        with self._lock:
            return self._reports.get(client_order_id), self._fingerprints.get(client_order_id)

    def _query_anchor_locked(
        self, client_order_id: str,
    ) -> tuple[str | None, Literal["first_claim", "legacy_recorded", "unknown"]]:
        _, legacy, current = self._submission_anchors.get(client_order_id, (None, None, None))
        if current is not None:
            return current, "first_claim"
        if legacy is not None:
            return legacy, "legacy_recorded"
        return None, "unknown"

    def query_anchor_for(
        self, client_order_id: str,
    ) -> tuple[str | None, Literal["first_claim", "legacy_recorded", "unknown"]]:
        """조회 기준 시각과 출처. first_claim 은 현재 주문번호 세대의 기원 claim 이며 접수 증거가 아니다.

        최초 항목 claim 은 별도로 보존한다. 레거시 관측 시각은 현재 세대의 약한 조회용 폴백이다."""
        with self._lock:
            self._require_open()
            return self._query_anchor_locked(client_order_id)

    def _recovery_snapshot_for(
        self, client_order_id: str,
    ) -> tuple[ExecutionReport | None, Fingerprint | None,
               tuple[str | None, Literal["first_claim", "legacy_recorded", "unknown"]], object | None]:
        """기록·지문·기준 시각·인스턴스 내 세대 토큰을 한 락으로 읽는다. 시각은 세대 식별자가 아니다."""
        with self._lock:
            self._require_open()
            return (self._reports.get(client_order_id), self._fingerprints.get(client_order_id),
                    self._query_anchor_locked(client_order_id), self._recovery_generations.get(client_order_id))

    def _record_recovery_if_unchanged(
        self, report: ExecutionReport, fingerprint: Fingerprint, *,
        expected: tuple[ExecutionReport | None, Fingerprint | None,
                        tuple[str | None, Literal["first_claim", "legacy_recorded", "unknown"]], object | None],
    ) -> bool:
        """기록·지문·기준 시각과 불투명 세대 토큰이 그대로일 때만 재조회 결과를 반영한다."""
        cid = report.client_order_id
        with self._lock:
            self._require_open()
            current = (self._reports.get(cid), self._fingerprints.get(cid), self._query_anchor_locked(cid),
                       self._recovery_generations.get(cid))
            if current != expected:
                return False
            self._bind_locked(report, fingerprint)
            self._save_locked()
            return True

    def record_if_unchanged(
        self, report: ExecutionReport, fingerprint: Fingerprint, *,
        expected: tuple[ExecutionReport | None, Fingerprint | None],
    ) -> bool:
        """현재 (리포트, 지문)이 ``expected`` 스냅샷과 같을 때만 기록하고 ``True``. 다르면(읽기~기록 사이
        동시 정정/취소가 이 id 를 새 ODNO 로 재바인딩함) 아무것도 안 하고 ``False`` -- reconcile 의 옛 세대
        스캔 결과가 새 세대 바인딩을 덮어써 취소가 죽은 ODNO 를 겨냥하는 일을 막는다(compare-and-swap)."""
        cid = report.client_order_id
        with self._lock:
            self._require_open()
            current = (self._reports.get(cid), self._fingerprints.get(cid))
            if current != expected:
                return False
            self._bind_locked(report, fingerprint)
            self._save_locked()
            return True

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
        self._submission_anchors.setdefault(report.client_order_id, (None, None, None))
        self._recovery_generations.setdefault(report.client_order_id, object())
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
        (있으면) 재바인딩 원 id 둘 다. 원 id 는 이미 완료 상태라 해제는 보통 no-op 이다.
        명시적 rebind 는 원 항목의 최초 claim 을 보존하되 현재 주문번호 세대의 조회 기준과 세대
        토큰을 변경 요청에서 교체한다. 변경 시각이 미상이면 이전 주문번호의 기준을 재사용하지 않는다."""
        with self._lock:
            self._require_open()
            self._bind_locked(report, fingerprint)
            if rebind is not None:
                self._bind_locked(rebind.report, rebind.fingerprint)
                original_id = rebind.report.client_order_id
                first, _, _ = self._submission_anchors[original_id]
                _, legacy, current = self._submission_anchors[report.client_order_id]
                self._submission_anchors[original_id] = (first, legacy, current)
                self._recovery_generations[original_id] = object()
            self._save_locked()

    def clear_in_flight(self, client_order_id: str) -> None:
        """주문이 확실히 접수 안 됐을 때(거부 등) in-flight 와 지문을 해제(영속). 완료된 id 엔 no-op --
        완료 주문의 지문을 지우면 리포트만 남고 지문이 사라져 크로스맵 불변식을 깨므로 in-flight 멤버십을
        확인하고 해제한다(멤버십 검사이지 pop() 참값 검사가 아님 -- 레거시 claim 시각은 ""으로 falsy)."""
        with self._lock:
            self._require_open()
            if client_order_id in self._in_flight:
                self._in_flight.pop(client_order_id)
                self._fingerprints.pop(client_order_id, None)
                self._submission_anchors.pop(client_order_id, None)
                self._recovery_generations.pop(client_order_id, None)
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
            descriptor = self._process_lock_fd
            backend = self._process_lock_backend
            key = self._process_lock_key
            if descriptor is not None:
                try:
                    if backend is not None:
                        with contextlib.suppress(OSError):
                            os.lseek(descriptor, 0, os.SEEK_SET)
                            backend.locking(descriptor, backend.LK_UNLCK, 1)
                finally:
                    try:
                        with contextlib.suppress(OSError):
                            os.close(descriptor)
                    finally:
                        self._process_lock_fd = None
                        self._process_lock_backend = None
                        self._process_lock_key = None
                        if key is not None:
                            with _locked_paths_lock:
                                _locked_paths.discard(key)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- 프로세스 간 단일라이터 락 -----------------------------------
    def _acquire_single_writer_lock(self) -> None:
        """이 프로세스만 저장소를 쓰도록 advisory 락을 잡는다(프로세스 간 이중전송 방지).

        POSIX 는 flock, Windows 는 오프셋 0 의 1바이트 비차단 잠금으로 인스턴스 수명 동안 배제한다.
        지원 백엔드가 없으면 메모리 저장소로 우회하지 않고 거부한다.
        """
        assert self._path is not None
        if _WINDOWS:
            if msvcrt is None:
                raise KISError("이 플랫폼은 안전한 파일 잠금 백엔드를 지원하지 않는다.")
            self._acquire_windows_lock(msvcrt)
            return
        if fcntl is None:
            raise KISError("이 플랫폼은 안전한 파일 잠금 백엔드를 지원하지 않는다.")
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

    def _acquire_windows_lock(self, backend: ModuleType) -> None:
        """정규 경로 예약 후 CRT 바이트 잠금을 확보한다. 실패한 시도의 자원만 해제한다."""
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        key = os.path.normcase(str(lock_path.resolve()))
        with _locked_paths_lock:
            if key in _locked_paths:
                raise KISError("주문 dedup 저장소가 이미 열려 있다. 별도 인스턴스를 중복 생성하지 마라.")
            _locked_paths.add(key)
        descriptor: int | None = None
        acquired = False
        try:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o644)
                os.lseek(descriptor, 0, os.SEEK_SET)
            except OSError as error:
                raise KISError("주문 dedup 저장소 파일 잠금을 확보할 수 없다.") from error
            try:
                backend.locking(descriptor, backend.LK_NBLCK, 1)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise KISError("주문 dedup 저장소가 다른 프로세스에 의해 이미 열려 있다.") from error
                raise KISError("주문 dedup 저장소 파일 잠금을 확보할 수 없다.") from error
            self._process_lock_fd = descriptor
            self._process_lock_backend = backend
            self._process_lock_key = key
            acquired = True
        finally:
            if not acquired:
                try:
                    if descriptor is not None:
                        with contextlib.suppress(OSError):
                            os.close(descriptor)
                finally:
                    with _locked_paths_lock:
                        _locked_paths.discard(key)

    def _prune_locked(self) -> None:
        """보존 창을 넘긴 **비-in-flight** 리포트를 정리해 무한 성장을 막는다.
        in-flight 는 절대 정리하지 않는다(접수 여부가 불명인 주문의 중복 전송 차단을 유지한다)."""
        if self._retention is None:
            return
        cutoff = self._now() - self._retention
        stale = [
            cid for cid, report in self._reports.items()
            if cid not in self._in_flight and report.recorded_at < cutoff
        ]
        for cid in stale:
            del self._reports[cid]
            self._fingerprints.pop(cid, None)
            self._submission_anchors.pop(cid, None)
            self._recovery_generations.pop(cid, None)

    # --- 영속화(원자적 + 내구성 JSON) --------------------------------
    def _save_locked(self, *, prune: bool = True) -> None:
        """호출자가 ``self._lock`` 을 쥔 상태에서만 부른다.

        임시파일에 쓰고 ``fsync`` 후 원자적 ``os.replace``, 디렉터리도 ``fsync`` 한다 --
        전원차단 뒤에도 방금 만든 claim 이 디스크에 남아 재시작 후 재전송을 막게(내구성).
        """
        if self._path is None:
            return
        if prune:
            self._prune_locked()
        data = {
            "schema_version": _SCHEMA_VERSION,
            "scope": (dict(zip(("cano", "product_code", "environment"), self._scope, strict=True))
                      if self._scope is not None else None),
            "submission_anchors": {cid: {"first_claimed_at": first, "legacy_recorded_at": legacy,
                                         "current_claimed_at": current}
                                   for cid, (first, legacy, current) in self._submission_anchors.items()},
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
        # v10 이후 파일은 _save_locked 가 in_flight/fingerprints/reports 세 맵을
        # **항상** 기록한다(비어도 {}). 그 버전인데 맵이 빠졌으면 이 라이브러리가 쓴 파일이 아니라
        # 손상/수기편집이다 -- .get(key, {}) 로 조용히 빈 dedup 로 시작하면 중복 체결 장벽이 사라지므로
        # (비-dict 루트·버전 미지원과 동일하게) fail-closed 한다. 레거시 버전은 맵 구성이 달라 관대하게 둔다.
        if version >= 10:
            missing = [key for key in ("in_flight", "fingerprints", "reports") if key not in data]
            if missing:
                raise KISError(
                    f"주문 dedup 저장소(v{version})에 필수 맵이 없다: {missing}. {self._path}. "
                    f"자동 복구하지 않는다(빈 상태 시작은 중복 체결 위험). 파일을 점검한 뒤 재시작하라."
                )
        if version >= 11:
            if "scope" not in data:
                raise KISError("주문 dedup 저장소에 필수 scope 가 없다. 자동 귀속하지 않는다.")
            scope = data["scope"]
            if scope is not None:
                if (
                    not isinstance(scope, dict)
                    or set(scope) != {"cano", "product_code", "environment"}
                    or not isinstance(scope["cano"], str) or not scope["cano"]
                    or not isinstance(scope["product_code"], str) or not scope["product_code"]
                    or scope["environment"] not in ("real", "paper")
                ):
                    raise KISError("주문 dedup 저장소의 scope 가 손상됐다. 자동 귀속하지 않는다.")
                self._scope = (scope["cano"], scope["product_code"], cast(Environment, scope["environment"]))
        try:
            raw_in_flight = data.get("in_flight", {})
            if isinstance(raw_in_flight, list):        # v1..v7: id 리스트 -> 시각 미상("")
                in_flight = {str(cid): "" for cid in raw_in_flight}
            else:                                       # v8: {id: claim 시각}
                in_flight = {str(k): str(v) for k, v in raw_in_flight.items()}
            fingerprints = {
                cid: decode_fingerprint(fp) for cid, fp in data.get("fingerprints", {}).items()
            }
            reports = {cid: _report_from_dict(d) for cid, d in data.get("reports", {}).items()}
        except (ValueError, InvalidOperation, TypeError, KeyError, AttributeError, KISError) as err:
            # 스키마는 맞지만 레코드 값이 손상(잘못된 status/수량/날짜 등) -> 도메인 에러로 fail-closed.
            # KISError 포함: 손상된 side 는 ExecutionReport.__post_init__ 가 KISError 로 올리는데,
            # 그걸 안 잡으면 경로 안내 없는 맨 메시지가 새어 형제 손상필드와 다른 처리가 된다.
            raise KISError(
                f"주문 dedup 저장소 레코드가 손상됐다: {self._path}. 자동 복구하지 않는다"
                f"(빈 상태 시작은 중복 체결 위험). 파일을 점검한 뒤 재시작하라."
            ) from err
        # 크로스-맵 불변식(수용 전 검증) -- 세 맵을 독립 디코드하면 reports 키 != 내장 client_order_id 인
        # 손상 파일(손상 에러 문구가 사용자에게 수편집을 지시하므로 현실적 트리거)이 try_claim 에서 다른
        # 주문의 Completed 리포트를 재생(오확정)할 수 있다. 위반은 레코드-손상과 같은 fail-closed.
        invariant_ok = (
            all(cid == report.client_order_id for cid, report in reports.items())
            and reports.keys().isdisjoint(in_flight)
            # 유령 지문 금지: 모든 지문엔 대응 claim(in_flight 또는 report)이 있어야 한다. 역은 강요하지
            # 않는다 -- 레거시 v1..v9 claim 은 지문이 없을 수 있고(위 _parse 주석 참조), 그 스토어를 한 번
            # 변경하면 v10 으로 재기록되므로 완전 일치를 요구하면 정상 마이그레이션이 재오픈 시 벽돌이 된다.
            # 지문 없는 claim 은 try_claim 에서 저장 지문 None != 제출 지문 -> Conflict 로 fail-safe 한다.
            and set(fingerprints) <= reports.keys() | in_flight.keys()
        )
        if not invariant_ok:
            raise KISError(
                f"주문 dedup 저장소의 맵 불변식이 깨졌다: {self._path}. 자동 복구하지 않는다"
                f"(빈 상태 시작은 중복 체결 위험). 파일을 점검한 뒤 재시작하라."
            )
        entries = reports.keys() | in_flight.keys()
        if version >= 12:
            raw_anchors = data.get("submission_anchors")
            if not isinstance(raw_anchors, dict) or raw_anchors.keys() != entries:
                raise KISError("주문 dedup 저장소의 submission_anchors 맵이 없거나 항목 집합이 다르다.")
            anchors = {cid: _submission_anchor_from_dict(value) for cid, value in raw_anchors.items()}
        else:
            anchors = {}
            for cid in entries:
                if cid in in_flight:
                    timestamp = in_flight[cid]
                    first = _aware_anchor_timestamp(timestamp) if version >= 8 and timestamp else None
                    anchors[cid] = (first, None, first)
                else:
                    anchors[cid] = (None, reports[cid].recorded_at.isoformat(), None)
        self._submission_anchors = anchors
        self._recovery_generations = {cid: object() for cid in entries}
        self._in_flight = in_flight
        self._fingerprints = fingerprints
        self._reports = reports


def _aware_anchor_timestamp(value: object) -> str:
    """시각 출처에 쓸 수 있는 실제 aware ISO 문자열만 허용한다. 로컬 시간대를 추정하지 않는다."""
    if not isinstance(value, str) or not value:
        raise KISError("주문 dedup 기준 시각이 유효한 aware ISO 문자열이 아니다.")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("timezone missing")
    except ValueError as error:
        raise KISError("주문 dedup 기준 시각이 유효한 aware ISO 문자열이 아니다.") from error
    return value


def _submission_anchor_from_dict(value: object) -> tuple[str | None, str | None, str | None]:
    if not isinstance(value, dict) or set(value) != {"first_claimed_at", "legacy_recorded_at", "current_claimed_at"}:
        raise KISError("주문 dedup submission_anchors 항목 형상이 손상됐다.")
    first = value["first_claimed_at"]
    legacy = value["legacy_recorded_at"]
    current = value["current_claimed_at"]
    if current is not None and legacy is not None:
        raise KISError("주문 dedup 현재 세대 기준 시각 출처가 중복됐다.")
    return (_aware_anchor_timestamp(first) if first is not None else None,
            _aware_anchor_timestamp(legacy) if legacy is not None else None,
            _aware_anchor_timestamp(current) if current is not None else None)


#: 디렉터리 fsync 를 지원하지 않는 플랫폼/파일시스템이 내는 errno -- 이때만 무시한다. EIO/ENOSPC
#: 같은 진짜 내구성 실패는 여기 없으므로 그대로 전파돼 '저장됨' 주장이 거짓이 되지 않는다.
_UNSUPPORTED_FSYNC_ERRNOS = frozenset({errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP})

#: 프로세스가 Windows 에서 도는가(``os.name`` 은 프로세스 내 불변이라 import 시 한 번 읽는다). 모듈
#: 상수로 두면 디렉터리-fsync 의 플랫폼 분기를 테스트가 이 bool 하나로 뒤집을 수 있다 -- 런타임에
#: ``os.name`` 을 'nt' 로 바꿔 흉내내면 Python 3.11 의 pathlib 이 모든 ``Path()`` 를 WindowsPath 로
#: 만들어(비-Windows 에서 인스턴스화 불가) 테스트 수집·리포팅이 깨진다.
_WINDOWS = os.name == "nt"


def _fsync_dir(directory: Path) -> None:
    """디렉터리 엔트리를 fsync -- rename 이 크래시에도 살아남게.

    Windows 는 디렉터리를 열 수 없어 열기 실패를 통째로 건너뛴다(데이터 파일은 이미 fsync + 원자
    교체됨 -- 디렉터리 fsync 는 best-effort). 그 외 플랫폼에선 디렉터리 fsync 미지원 errno
    (EINVAL/ENOTSUP/EOPNOTSUPP)만 무시하고 EIO/ENOSPC/EMFILE 등 진짜 내구성 실패는 삼키지 않고
    올린다(거짓 '저장됨' 방지)."""
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError as err:  # pragma: no cover -- 플랫폼이 디렉터리 fd 열기를 미지원
        # Windows 는 디렉터리를 O_RDONLY 로 열 수 없어 PermissionError(EACCES) 를 낸다 -- 구조적
        # 플랫폼 특성이지 내구성 실패가 아니다(데이터 파일은 이미 fsync + 원자 교체됨). 건너뛴다.
        # 그 외 플랫폼에선 디렉터리 fsync 미지원 errno 만 무시하고 EMFILE/EIO 등 진짜 실패는 올린다.
        if _WINDOWS or err.errno in _UNSUPPORTED_FSYNC_ERRNOS:
            return
        raise  # EMFILE/EIO 등 진짜 실패는 올린다(거짓 '저장됨' 방지, fsync 분기와 동일 규율)
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


def _required_report_str(report_data: dict[str, object], key: str, *, allow_empty: bool) -> str:
    """리포트 식별 필드를 문자열로 -- JSON null 을 str(None)=='None' 으로 흡수하지 않는다. 누락/None,
    (allow_empty=False 면) 빈 문자열은 ValueError 로 올려 _load 가 손상-스토어로 fail-closed 하게 한다."""
    value = report_data.get(key)
    if value is None:
        raise ValueError(f"리포트 {key} 가 없거나 null 이다")
    text = str(value)
    if not allow_empty and not text:
        raise ValueError(f"리포트 {key} 가 비어 있다")
    return text


def _report_from_dict(report_data: dict[str, object]) -> ExecutionReport:
    average_price = report_data["average_price"]
    # v7 은 "recorded_at" 키, v6 이하는 "submitted_at" 키를 쓴다 -- 구 저장소를 재작성 없이 열도록
    # 옛 키를 recorded_at 으로 매핑한다(온-디스크 스키마가 바뀌면 하위호환 로드 경로를 함께 둔다).
    recorded_raw = report_data.get("recorded_at", report_data.get("submitted_at"))
    filled_quantity = required_decimal(report_data["filled_quantity"], "filled_quantity")
    if filled_quantity < 0:
        raise ValueError("리포트 filled_quantity 는 0 이상이어야 한다")
    parsed_average_price = None if average_price is None else required_decimal(average_price, "average_price")
    try:
        recorded_at = datetime.fromisoformat(str(recorded_raw))
    except ValueError as err:
        raise ValueError("리포트 recorded_at 은 ISO 날짜·시각이어야 한다") from err
    if recorded_at.utcoffset() is None:
        raise ValueError("리포트 recorded_at 에 시간대 오프셋이 없다")
    return ExecutionReport(
        client_order_id=_required_report_str(report_data, "client_order_id", allow_empty=False),
        order_id=None if report_data["order_id"] is None else str(report_data["order_id"]),
        symbol=_required_report_str(report_data, "symbol", allow_empty=True),
        side=cast(Side, str(report_data["side"])),
        status=OrderStatus(str(report_data["status"])),
        filled_quantity=filled_quantity,
        average_price=parsed_average_price,
        recorded_at=recorded_at,
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
