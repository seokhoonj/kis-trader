"""예외 -> 종료 코드·결과·안정 메시지 번역.

CLI 는 도메인 조건을 **탐지하지 않고 번역만** 한다: 패키지 예외 계층을 그대로 받아 사용자
메시지와 프로세스 종료 코드로 옮긴다. 사용오류·리스크 거부·브로커 거부·결과불명을 각각 다른
종료 코드로 구분해, 스크립트가 결과를 프로그램적으로 분기할 수 있게 한다.

종료 코드: 0 성공 / 2 인자 오류(argparse) / 3 로컬 구성·자격증명 / 4 도메인 검증·리스크 거부 /
5 전송 실패 / 6 브로커 거부 / 7 주문 결과 불확실(reconcile 필요) / 130 사용자 중단.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..errors import (
    AccountNotOrderableError,
    KISAuthError,
    KISError,
    KISRateLimitError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
    PreTradeRiskError,
)


class CliConfigError(Exception):
    """로컬 구성·자격증명 문제(환경변수 누락, 플래그 불일치 등). 종료 코드 3."""


class CliAborted(Exception):
    """사용자가 확인 프롬프트에서 취소. 종료 코드 3(전송 안 됨)."""


@dataclass(frozen=True, kw_only=True)
class Translated:
    """번역 결과 -- 종료 코드와 사용자에게 보일 안정 필드.

    ``retryable``/``reconcile_required`` 는 인접한 두 안전 신호라 kw-only 로 강제한다
    (위치 인자로 뒤바뀌면 "재조회 필요"가 "재시도 가능"으로 조용히 뒤집힌다)."""

    exit_code: int
    outcome: Literal["unknown", "rejected", "not_sent", "failed", "config"]
    retryable: bool
    reconcile_required: bool
    message: str


_RECONCILE_HINT = (
    "재전송하지 마세요 -- `kis order reconcile <client_order_id>` 로 실제 상태를 확정하세요."
)


def translate(exc: BaseException) -> Translated:
    """예외 하나를 종료 코드·결과로 번역한다. 가장 좁은 예외부터 검사하고, 인식하지 못하는
    예외는 그대로 다시 raise 한다(번역하거나-재발생, 삼키지 않는다)."""
    if isinstance(exc, CliConfigError):
        return Translated(exit_code=3, outcome="config", retryable=False,
                          reconcile_required=False, message=str(exc))
    if isinstance(exc, CliAborted):
        return Translated(exit_code=3, outcome="not_sent", retryable=False,
                          reconcile_required=False, message=str(exc) or "사용자가 취소했습니다.")
    if isinstance(exc, OrderTimeoutError):
        # 타임아웃 예외는 client_order_id 를 실어 온다 -- 그걸 그대로 노출해 사용자가 바로 재조회할 수
        # 있게 한다(예전엔 id 를 버리고 자리표시만 찍어, 재조회 대상을 알 수 없었다).
        return Translated(exit_code=7, outcome="unknown", retryable=False, reconcile_required=True,
                          message=f"주문 결과 불명(타임아웃) -- kis order reconcile {exc.client_order_id} "
                                  f"로 확인하세요. {_RECONCILE_HINT}")
    if isinstance(exc, OrderRejectedError):
        return Translated(exit_code=6, outcome="rejected", retryable=False,
                          reconcile_required=False, message=f"브로커가 주문을 거부했습니다: {exc}")
    if isinstance(exc, PreTradeRiskError):
        return Translated(exit_code=4, outcome="not_sent", retryable=False,
                          reconcile_required=False, message=f"사전 리스크 한도에 걸려 전송하지 않았습니다: {exc}")
    if isinstance(exc, OrderError):
        # 접수됐으나 결과 불명(예: rt_cd=0 인데 ODNO 없음 -> in-flight 유지). 주문이 살아
        # 있을 수 있으니 실패로 단정하지 않고 reconcile 을 요구한다(타임아웃과 동급 처리).
        return Translated(exit_code=7, outcome="unknown", retryable=False, reconcile_required=True,
                          message=f"주문 결과 불명. {_RECONCILE_HINT} ({exc})")
    if isinstance(exc, AccountNotOrderableError):
        return Translated(exit_code=4, outcome="not_sent", retryable=False,
                          reconcile_required=False, message=f"이 계좌는 주문할 수 없습니다: {exc}")
    if isinstance(exc, KISUsageError):
        return Translated(exit_code=4, outcome="not_sent", retryable=False,
                          reconcile_required=False, message=f"사용 오류: {exc}")
    if isinstance(exc, KISAuthError):
        return Translated(exit_code=3, outcome="config", retryable=False,
                          reconcile_required=False, message=f"인증 실패(자격증명 확인): {exc}")
    if isinstance(exc, KISRateLimitError):
        return Translated(exit_code=5, outcome="failed", retryable=True,
                          reconcile_required=False, message=f"호출 한도 초과: {exc}")
    if isinstance(exc, KISError):
        return Translated(exit_code=5, outcome="failed", retryable=False,
                          reconcile_required=False, message=f"요청 실패: {exc}")
    raise exc
