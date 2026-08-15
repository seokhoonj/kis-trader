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
    OrderRejectedError,
    OrderTimeoutError,
    PreTradeRiskError,
)


class CliConfigError(Exception):
    """로컬 구성·자격증명 문제(환경변수 누락, 플래그 불일치 등). 종료 코드 3."""


class CliAborted(Exception):
    """사용자가 확인 프롬프트에서 취소. 종료 코드 3(전송 안 됨)."""


@dataclass(frozen=True)
class Translated:
    """번역 결과 -- 종료 코드와 사용자에게 보일 안정 필드."""

    exit_code: int
    outcome: Literal["unknown", "rejected", "not_sent", "failed", "config"]
    retryable: bool
    reconcile_required: bool
    message: str


def translate(exc: BaseException) -> Translated:
    """예외 하나를 종료 코드·결과로 번역. 가장 좁은 예외부터 검사한다."""
    if isinstance(exc, CliConfigError):
        return Translated(3, "config", False, False, str(exc))
    if isinstance(exc, CliAborted):
        return Translated(3, "not_sent", False, False, str(exc) or "사용자가 취소했습니다.")
    if isinstance(exc, OrderTimeoutError):
        return Translated(
            7, "unknown", False, True,
            "주문 결과 불명(타임아웃). 재전송하지 마세요 -- "
            "`kis order reconcile <client_order_id>` 로 실제 상태를 확정하세요.",
        )
    if isinstance(exc, OrderRejectedError):
        return Translated(6, "rejected", False, False, f"브로커가 주문을 거부했습니다: {exc}")
    if isinstance(exc, PreTradeRiskError):
        return Translated(4, "not_sent", False, False, f"사전 리스크 한도에 걸려 전송하지 않았습니다: {exc}")
    if isinstance(exc, AccountNotOrderableError):
        return Translated(4, "not_sent", False, False, f"이 계좌는 주문할 수 없습니다: {exc}")
    if isinstance(exc, KISUsageError):
        return Translated(4, "not_sent", False, False, f"사용 오류: {exc}")
    if isinstance(exc, KISAuthError):
        return Translated(3, "config", False, False, f"인증 실패(자격증명 확인): {exc}")
    if isinstance(exc, KISRateLimitError):
        return Translated(5, "failed", True, False, f"호출 한도 초과: {exc}")
    if isinstance(exc, KISError):
        return Translated(5, "failed", False, False, f"요청 실패: {exc}")
    raise exc
