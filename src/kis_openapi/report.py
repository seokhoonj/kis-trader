"""주문 결과(RESULT) 타입 -- :class:`ExecutionReport` 와 :class:`OrderStatus`.

주문의 '현재 상태'는 낙관적으로 앞서가지 않고 **거래소가 준 리포트로만** 전이한다
(FIX 라이프사이클 규칙). 로컬 상태는 캐시이며, 이 리포트가 진실 원천이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .errors import KISError
from .order import _SIDES, Side


class OrderStatus(StrEnum):
    """주문 라이프사이클 상태(국제 표준 명칭 + FIX 4.2 OrdStatus 대응).

    ``pending_*`` 는 요청은 보냈으나 거래소 확정 전 상태다 -- 취소를 보냈다고 곧바로
    ``canceled`` 로 두지 않는다.
    """

    PENDING_NEW = "pending_new"
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    PENDING_CANCEL = "pending_cancel"
    PENDING_REPLACE = "pending_replace"
    REPLACED = "replaced"


#: 더 이상 전이가 없는 종료 상태.
TERMINAL_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """한 주문의 상태 스냅샷(진실 원천).

    ``client_order_id`` (우리가 발행한 멱등키)로 요청과 리포트를 잇고, ``order_id`` 는 브로커가
    접수 시 부여한 식별자다 -- 즉시주문은 거래소 주문번호(KIS ``ODNO``), 예약주문은 예약 식별자
    (국내 ``rsvn_ord_seq`` / 해외 ``ovrs_rsvn_odno``, 정정·취소 지목용)가 담긴다. 두 id를 모두 보관한다.
    """

    client_order_id: str
    order_id: str | None
    symbol: str
    side: Side
    status: OrderStatus
    #: 이 ``order_id`` (대기주문) 기준 누적 체결량 -- 원 client_order_id 의 전 생애 합이 아니다.
    #: 정정(modify)은 새 ODNO 를 부여하며 그 신규 대기주문 리포트의 이 값은 0 에서 다시 시작한다
    #: (정정 이전 체결은 정정이 반환한 리포트에 남는다). 재조회(reconcile)로 만든 리포트에선 조회 시점 값.
    filled_quantity: Decimal
    average_price: Decimal | None
    #: 이 리포트를 로컬에 기록한 시각(보존 정리 기준). 전송 경로에선 접수 시각과 사실상
    #: 같지만, 재조회(reconcile)로 만든 리포트에선 원 접수 시각이 아니라 재조회 시각이다.
    submitted_at: datetime
    #: 국내 주문의 한국거래소전송주문조직번호(KRX_FWDG_ORD_ORGNO) -- ``order_id`` 와 함께
    #: 정정·취소 요청의 대상 식별에 필요하다. 접수/재조회 응답에서 뽑아 보관하며, ``_raw`` 와 달리
    #: 영속되므로 프로세스 재기동 뒤에도 정정·취소가 가능하다. 해외 주문엔 해당 없음(None).
    organization_number: str | None = None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        # side 는 신뢰 못 할 경계(영속 JSON, 지문 튜플)에서도 도메인 값이어야 한다.
        if self.side not in _SIDES:
            raise KISError(f"ExecutionReport.side 는 buy/sell 중 하나여야 한다: {self.side!r}")
        # frozen 이 재바인딩만 막으므로, _raw 를 읽기전용 스냅샷으로 얼려 진짜 불변으로.
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

    @property
    def is_terminal(self) -> bool:
        """더 이상 상태 전이가 없는가."""
        return self.status in TERMINAL_STATUSES
