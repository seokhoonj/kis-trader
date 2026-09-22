"""전송 계층 계약 -- :class:`Transport` 프로토콜과 :class:`RawResponse`.

파사드는 구체 HTTP 세션이 아니라 이 **프로토콜**에 의존한다. 그래서 테스트가 자격증명·
네트워크 없이 저장된 KIS 응답을 주입해 파싱/주문/dedup 경로를 결정적으로 검증할 수 있다.

재시도 정책은 여기 산다: **읽기(GET, idempotent)만 자동 재시도**하고, **쓰기(주문)는
타임아웃 시 재시도하지 않고** :class:`~kis_trader.errors.OrderTimeoutError` 를 올린다
(KIS 타임아웃은 미체결을 뜻하지 않으므로 중복 체결을 막기 위함).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

from .errors import KISError

#: 접속 환경 -- 실전(real) / 모의(paper). 세션/계좌/주문 TR 선택에 쓰인다(어느 KIS 서버냐).
Environment = Literal["real", "paper"]


class TransportTimeout(KISError):
    """네트워크 타임아웃 -- 요청이 서버에 도달했는지 **불명**한 저수준 오류.

    파사드가 이를 잡아 주문 경로에서는 ``client_order_id`` 를 실은
    :class:`~kis_trader.errors.OrderTimeoutError` 로 승격한다(재전송 금지 신호). 조회(GET) 경로는
    승격 없이 그대로 오르므로 :class:`~kis_trader.errors.KISError` 를 뿌리로 둬야 ``except KISError``
    가 놓치지 않는다(모든 예외의 뿌리는 KISError 라는 계약).
    """

    def __init__(self, message: str = "네트워크 타임아웃 -- 요청이 서버에 도달했는지 불명.") -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RawResponse:
    """KIS 표준 응답 골격 + 원본 바디.

    ``rt_cd`` == "0" 이 성공이다. ``body`` 는 ``output`` / ``output1`` / ``output2`` 등
    벤더 원본(정본 와이어 형태)이다.
    """

    rt_cd: str
    msg_cd: str
    msg1: str
    # 인바운드 벤더 페이로드는 진짜로 이질적이라 Any 가 정당한 경계다.
    body: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    tr_cont: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", MappingProxyType(dict(self.body)))

    @property
    def ok(self) -> bool:
        """성공 응답인가(``rt_cd`` == "0")."""
        return self.rt_cd == "0"


@runtime_checkable
class Transport(Protocol):
    """저수준 전송 계약. 실제 HTTP 세션과 테스트용 가짜 전송이 모두 구현한다."""

    #: 이 전송이 향하는 KIS 환경("real"/"paper"). 세션의 안전 게이트가 실제 소켓 목적지와
    #: 갈라지지 않았는지(split-brain) 확인하는 데 쓴다. 실제 전송은 반드시 밝힌다. 세션은 이 값을
    #: **best-effort** 로 읽어(``getattr(transport, "environment", None)``), 이 필드가 없는 최소한의
    #: 가짜 전송(실 소켓 없음)은 정렬된 것으로 보고 통과시킨다 -- 게이트가 막는 건 환경을 밝히면서
    #: 세션과 어긋난 실 전송뿐이다.
    environment: Environment

    def request(
        self,
        *,
        method: str,
        path: str,
        tr_id: str,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, str] | None = None,
        idempotent: bool,
        tr_cont: str = "",
    ) -> RawResponse:
        """한 번의 KIS 호출. 아웃바운드 파라미터/바디는 문자열-값(KIS 인코딩).

        ``idempotent=False``(쓰기)면 타임아웃에 재시도하지 않는다. ``tr_cont`` 는 연속조회
        요청 표지다 -- 공백("")이 초기 조회, "N" 이 다음 페이지(직전 응답헤더 ``tr_cont`` 가
        F/M 이었을 때). 응답측 연속 여부는 :attr:`RawResponse.tr_cont` 로 노출된다.
        """
        ...
