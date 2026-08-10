"""kis_openapi 예외 계층.

모든 예외의 뿌리는 :class:`KISError` 이고, KIS 응답의 ``rt_cd`` / ``msg_cd`` /
``msg1`` 과 원본 바디(``raw``)를 실어 호출자가 원인을 프로그램으로 분기할 수 있게 한다.
경계(transport)에서 벤더 응답을 이 타입들로 변환해 올린다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class KISError(Exception):
    """kis_openapi 모든 예외의 뿌리.

    KIS 표준 응답 3필드(``rt_cd`` 성공실패, ``msg_cd`` 응답코드, ``msg1`` 응답메시지)와
    원본 바디를 담는다. ``raise ... from err`` 로 원인을 체이닝한다.
    """

    def __init__(
        self,
        message: str,
        *,
        rt_cd: str | None = None,
        msg_cd: str | None = None,
        msg1: str | None = None,
        raw: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.rt_cd = rt_cd
        self.msg_cd = msg_cd
        self.msg1 = msg1
        self.raw = raw


class KISUsageError(KISError):
    """호출자 잘못 -- 잘못된 인자, 미충족 사전조건 등. 재시도해도 소용없다."""


class AccountNotOrderableError(KISUsageError):
    """조회전용 계좌(퇴직연금 IRP/DC)에 주문을 시도.

    KIS Open API는 퇴직연금 계좌의 주문 엔드포인트를 거부한다(``APBK1744``). 와이어에
    닿기 전에 클라이언트에서 미리 막아 명확한 메시지를 준다.
    """


class KISAuthError(KISError):
    """인증/토큰 실패 -- 재인증(토큰 재발급)이 필요하다."""


class KISRateLimitError(KISError):
    """유량(rate limit) 초과 -- 잠시 backoff 후 재시도한다."""


class UnsupportedSchemaVersionError(KISError):
    """영속 저장소의 스키마 버전이 이 릴리스가 읽을 수 있는 집합에 없다(내구 형식 계약)."""


class OrderError(KISError):
    """주문 관련 실패의 뿌리."""


class PreTradeRiskError(OrderError):
    """주문이 사전 리스크 한도(:class:`~kis_openapi.risk.RiskLimits`)를 어겨 전송 전에 막혔다.

    과대 수량/금액, 현재가 대비 % 이탈(collar), 호가단위 위반 같은 fat-finger(오주문)를
    와이어에 닿기 전에 잡는다. 접수 거부(:class:`OrderRejectedError`)와 달리 주문은 아예
    전송되지 않았다 -- 한도를 고쳐 다시 주문하면 된다.
    """


class OrderRejectedError(OrderError):
    """거래소가 주문을 거부했다(정상 응답이되 ``rt_cd`` != 0).

    체결이 아니라 접수 거부다. 절대 '체결'과 혼동하지 않는다.
    """


class OrderTimeoutError(OrderError):
    """주문 전송이 시간초과됐다 -- 체결 여부가 **불명**이다.

    KIS 타임아웃(``EGW00301`` / ``EGW00302``)은 "주문이 안 들어갔다"는 뜻이 **아니다**.
    따라서 **자동 재전송하지 않는다**(중복 체결 위험). 반드시 ``kis.orders.reconcile(client_order_id)``
    로 실제 상태를 재조회한 뒤 판단한다. 실패한 주문의 ``client_order_id`` 를 실어 재조회에
    쓰게 한다.
    """

    def __init__(self, message: str, *, client_order_id: str, **kw: object) -> None:
        super().__init__(message, **kw)  # kw = KISError 의 rt_cd/msg_cd/msg1/raw
        self.client_order_id = client_order_id
