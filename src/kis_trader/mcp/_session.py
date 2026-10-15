"""MCP 런타임 계좌 상태 -- :class:`MCPSession` (활성 ``(profile, environment)`` + 슬롯 캐시) 와
주문 호출마다 뜨는 :class:`OrderSnapshot`.

``mcp`` 에 의존하지 않아 단독 테스트된다. 주입된 ``build`` 팩토리로 ``(profile, environment)`` 슬롯마다
:class:`~kis_trader.client.KISClient` 를 열어 캐시하고, 읽기 맥락(활성 슬롯)을 런타임에 바꾼다
(``use_environment``/``use_profile``). **실주문 권한은 기동 시 한 번 읽은
:class:`_StartupAuthorization` 에 고정되어 전환으로 바뀌지 않는다**(안전 불변 -- 읽기 전환은
자유이되 실거래 언락은 기동 환경변수에만 묶인다).

주문 경로는 호출 시점에 :meth:`MCPSession.order_snapshot` 으로 활성 슬롯을 한 번 떠서(await 이전)
in-flight 주문이 동시 전환에 흔들리지 않게 한다 -- 스냅샷이 client·gate 를 함께 얼려(frozen) 든다.
슬롯 캐시는 **비우지 않는다** -- 다른 슬롯으로 갔다 돌아오면 그 슬롯의 주문 저장소(:class:`~kis_trader.
store.OrderStore`)·토큰이 그대로 복원된다(정정/취소가 지목하는 주문 정체성 유지).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import NotRequired, TypedDict, cast, get_args

from ..client import KISClient
from ..errors import KISUsageError
from ..transport import Environment
from ._guardrails import RealOrderGate
from ._policy import _SWITCHABLE_PROFILES, _account_descriptor, _account_masked

#: 활성 환경 멤버십 -- 공개 :data:`~kis_trader.transport.Environment` Literal 에서 파생해 드리프트를
#: 원천 차단한다(프로필 축이 _SWITCHABLE_PROFILES 를 SwitchableProfile 에서 파생하는 것과 평행).
_ENVIRONMENTS = frozenset(get_args(Environment))

#: ``(profile, environment)`` -> KISClient 팩토리. profile 은 저장 프로필 이름(``None`` 은 직접-자격증명
#: 기동이라 전환 불가 -- 이 팩토리가 쓰이지 않는다).
ClientBuilder = Callable[[str, str], KISClient]


class CurrentAccountResult(TypedDict):
    """활성 MCP 슬롯의 공개 설명 형상. 계좌 상품코드를 알 수 있을 때만 ``product_code`` 가 붙는다."""

    profile: str | None
    environment: Environment
    account: str
    account_type: str | None
    product_code: NotRequired[str]


@dataclass(frozen=True, slots=True)
class _StartupAuthorization:
    """실주문 언락 비트 -- 기동 시 환경변수에서 **딱 한 번** 읽어 얼린다(:meth:`from_env`). 전환
    도구는 이 필드를 읽거나 쓰는 경로가 없어(frozen + setter 없음), 활성 환경을 real 로 바꿔도
    실주문 언락에 영향을 못 준다(안전 분리의 구조적 증명)."""

    allow_real: bool
    confirmed: bool

    @classmethod
    def from_env(cls, environ: dict[str, str]) -> _StartupAuthorization:
        """``KIS_MCP_ALLOW_REAL`` + ``KIS_MCP_REAL_CONFIRM`` 을 :class:`RealOrderGate` 와 같은 규칙으로
        읽어 두 비트만 얼린다(언락 문구/TRUTHY 판정의 단일 출처 공유)."""
        gate = RealOrderGate.from_env(environ, "real")  # environment 인자는 두 비트 추출엔 무관
        return cls(allow_real=gate.allow_real, confirmed=gate.confirmed)


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    """한 주문 호출의 얼린 맥락 -- 활성 슬롯의 client 와 그 환경의 gate 를 함께 든다. 주문 도구가
    await(사람 확인 elicitation) 이전에 한 번 떠서, 집행 중 동시 전환이 대상 계좌/환경을 바꾸지
    못하게 한다. ``profile`` 은 **해외** 주문 권한 게이트(위탁 전용)에 쓴다 -- 국내 주문 권한은 계좌의
    orderable 로만 가르므로 profile 을 보지 않는다(``None`` 은 직접-자격증명 고정 슬롯)."""

    profile: str | None
    environment: str
    client: KISClient
    gate: RealOrderGate


class MCPSession:
    """MCP 서버의 런타임 계좌 상태. 활성 ``(profile, environment)`` 를 들고, 슬롯마다 KISClient 를
    캐시하며(비우지 않음), 읽기 맥락 전환과 주문 스냅샷을 제공한다. 모든 상태 전이·스냅샷은 락 아래서
    일어난다(도구 호출이 교차해도 레이스 없음)."""

    def __init__(
        self, *, profile: str | None, environment: str, startup_client: KISClient,
        auth: _StartupAuthorization, build: ClientBuilder | None = None,
    ) -> None:
        """``startup_client`` 는 기동 슬롯(``profile``/``environment``)의 이미 만들어진 세션이다. ``build``
        가 있으면 런타임 전환이 가능하고(저장 프로필 기동), ``None`` 이면 고정 슬롯이다(직접-자격증명 기동
        -- 전환 도구는 거부). ``auth`` 는 기동 시 얼린 실주문 언락 비트."""
        if environment not in _ENVIRONMENTS:
            raise KISUsageError(f"environment 는 real/paper 중 하나여야 한다: {environment!r}")
        if startup_client.environment != environment:
            raise KISUsageError(
                f"기동 클라이언트 환경({startup_client.environment!r})이 세션 환경({environment!r})과 "
                "어긋난다 -- split-brain 차단."
            )
        if build is not None and profile is None:
            # 전환 가능 세션(build 주입)은 기동 프로필이 있어야 한다 -- profile=None 은 직접-자격증명 고정
            # 슬롯(build=None)이어야 한다. 이 불법 조합을 생성 시 거부해 _get_or_build 의 assert(-O 에서
            # 소거됨)에 기대지 않는다(소거되면 build(None, env) 로 기본 프로필을 오해석할 수 있다).
            raise KISUsageError(
                "전환 가능 세션은 기동 프로필이 필요하다 -- profile=None(직접-자격증명)이면 build 를 주지 말라."
            )
        self._profile = profile
        # 위 가드(startup_client.environment != environment)가 environment 를 Literal 로 좁히지만, 활성
        # 환경은 전환 도구가 str 을 다시 대입하므로 str 로 고정한다.
        self._environment: str = environment
        self._auth = auth
        self._build = build
        self._lock = threading.Lock()
        self._cache: dict[tuple[str | None, str], KISClient] = {(profile, environment): startup_client}

    def _get_or_build(self, profile: str | None, environment: str) -> KISClient:
        """캐시에서 슬롯을 꺼내거나(hit) 팩토리로 만들어 캐시한다(miss). **호출자가 락을 쥔다.** 전환
        불가(``build`` 없음) 세션에서 미보유 슬롯을 요구하면 거부한다."""
        key = (profile, environment)
        client = self._cache.get(key)
        if client is None:
            if self._build is None:
                raise KISUsageError(
                    "런타임 전환은 저장 프로필 기동이 필요하다 -- KIS_MCP_PROFILE 로 기동하라."
                )
            assert profile is not None  # build 가 있으면 기동 프로필이 있었다(전환 가능 세션)
            client = self._build(profile, environment)
            self._cache[key] = client
        return client

    def read_client(self) -> KISClient:
        """현재 활성 슬롯의 KISClient(조회 도구가 호출 시점에 쓴다)."""
        with self._lock:
            return self._get_or_build(self._profile, self._environment)

    @property
    def active_profile(self) -> str | None:
        """현재 활성 프로필 이름(직접-자격증명 기동은 ``None``). 해외-조회·주문 게이트가 쓴다."""
        return self._profile

    @property
    def switching_enabled(self) -> bool:
        """저장 프로필 팩토리가 있어 런타임 계좌·환경 전환이 가능한 세션인지."""
        return self._build is not None

    def read_context(self) -> tuple[KISClient, str | None]:
        """활성 슬롯의 (client, active_profile) 를 **한 락 아래서 함께** 떠서 돌려준다. client 와
        profile 을 ``read_client()`` + ``active_profile`` 로 따로 읽으면 그 사이 동시 ``use_profile`` 가
        general 프로필에 ISA(01) 클라이언트를 짝지어 해외-조회 게이트(:func:`require_overseas_general_
        account`)를 잘못 통과시킬 수 있다(TOCTOU). 두 값을 함께 캡처해 읽기 핸들러가 일관된 짝을 쓰게
        한다(읽기 전용이되 짝은 원자적)."""
        with self._lock:
            return self._get_or_build(self._profile, self._environment), self._profile

    def order_snapshot(self) -> OrderSnapshot:
        """활성 슬롯의 주문 맥락을 한 번 떠서 얼린다 -- client 와 그 환경의 gate 를 함께. gate 는
        기동 시 얼린 언락 비트(:class:`_StartupAuthorization`)로 만들어지므로, 활성 환경을 바꿔도 언락은
        바뀌지 않는다. gate.environment 와 client.environment 가 같은지 떠는 시점에 다시 단언한다
        (split-brain 방지 -- 이 자리로 기동 1회 검증을 주문마다 검증으로 옮겼다)."""
        with self._lock:
            profile = self._profile
            environment = self._environment
            client = self._get_or_build(profile, environment)
            gate = RealOrderGate(
                environment=environment, allow_real=self._auth.allow_real, confirmed=self._auth.confirmed
            )
            if gate.environment != client.environment:
                raise KISUsageError(
                    f"게이트 환경({gate.environment!r})과 클라이언트 환경({client.environment!r})이 "
                    "어긋난다 -- split-brain(모의 게이트 + 실전 소켓) 차단."
                )
            return OrderSnapshot(profile=profile, environment=environment, client=client, gate=gate)

    def use_environment(self, environment: str) -> CurrentAccountResult:
        """활성 환경만 바꾼다(real/paper). 기동 시 얼린 실주문 언락 비트(:class:`_StartupAuthorization`)는
        전환으로 바뀌지 않는다 -- 다만 기동 시 그 비트가 켜져 있었다면 real 로 바꾼 뒤 실주문이 집행
        가능해진다(사람 확인은 여전히 필요). 대상 슬롯을 commit 전에 먼저 만들어 보고(atomic -- 없는
        조합이면 활성 상태 불변으로 거부) 성공해야 전환을 확정한다."""
        if environment not in _ENVIRONMENTS:
            raise KISUsageError(f"environment 는 real/paper 중 하나여야 한다: {environment!r}")
        with self._lock:
            self._get_or_build(self._profile, environment)  # eager-build; 실패 시 활성 상태 불변
            self._environment = environment
            return self._describe()

    def use_profile(self, profile: str) -> CurrentAccountResult:
        """활성 프로필(계좌 종류)만 바꾼다. {general, isa, pension, irp} 로 제한한다 -- 그 밖/파생이면
        지원 목록과 함께 거부(파생은 선택 불가). 대상 슬롯을 commit 전에 먼저 만들어 보고(atomic)
        성공해야 확정한다. 조회뿐 아니라 **주문 대상 계좌**도 함께 바뀐다 -- 실주문이 언락된 세션이면
        이후 주문은 전환된 계좌로 나간다(국내는 계좌의 주문 능력 orderable 로, 해외는 위탁(general)
        전용으로 가르고, 사람 확인은 여전히 필요). 기동 시 얼린 실주문 언락 비트 자체는 바꾸지 못한다."""
        if profile not in _SWITCHABLE_PROFILES:
            raise KISUsageError(
                f"use_profile 로 바꿀 수 있는 프로필은 {sorted(_SWITCHABLE_PROFILES)} 뿐이다"
                f"(파생 등은 MCP 에 노출하지 않는다): {profile!r}"
            )
        with self._lock:
            self._get_or_build(profile, self._environment)  # eager-build; 실패 시 활성 상태 불변
            self._profile = profile
            return self._describe()

    def current_account(self) -> CurrentAccountResult:
        """지금 활성 ``(profile, environment)`` + 마스킹 계좌 + 계좌 종류(best-effort)."""
        with self._lock:
            return self._describe()

    def _describe(self) -> CurrentAccountResult:
        """활성 슬롯 설명 dict. **호출자가 락을 쥔다.** 계좌번호는 마지막 4자리만(마스킹)."""
        client = self._get_or_build(self._profile, self._environment)
        account_type, product_code = _account_descriptor(client)
        out: CurrentAccountResult = {
            "profile": self._profile,
            "environment": cast("Environment", self._environment),
            "account": _account_masked(client),
            "account_type": account_type,
        }
        if product_code is not None:
            out["product_code"] = product_code
        return out
