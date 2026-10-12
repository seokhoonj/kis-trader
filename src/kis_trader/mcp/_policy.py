"""MCP 계좌 정책 leaf -- 전환 가능한 프로필 집합, 주문/해외조회가 열리는 기준(프로필 + 상품코드),
계좌번호 마스킹·설명. :mod:`kis_trader.mcp.server`/:mod:`~.handlers`/:mod:`~._session` 셋이 공유하는
순수 leaf 라 모듈 순환을 만들지 않는다(handlers<->_session 지연 import 제거). ``mcp`` 에 의존하지 않는다.
"""

from __future__ import annotations

from typing import Literal, get_args

from ..account import StockAccount
from ..client import KISClient
from ..errors import KISUsageError

#: ``use_profile`` 로 런타임 전환 가능한 프로필(계좌 종류). 파생(derivatives/overseas_derivatives)은
#: 여기에 없어 선택 자체가 불가하다(위험 -- MCP 에 읽기도 노출하지 않는다). 기동 프로필
#: (``KIS_MCP_PROFILE``)은 운영자 설정이라 이 집합에 매이지 않고 그대로 신뢰한다(런타임 전환만 제한).
#: 리터럴과 런타임 집합을 한 출처(:data:`SwitchableProfile`)에서 파생해 전환 도구의 MCP 입력 스키마가
#: 같은 enum 을 싣게 한다(스키마<->런타임 가드 드리프트 방지).
SwitchableProfile = Literal["general", "isa", "pension", "irp"]
_SWITCHABLE_PROFILES = frozenset(get_args(SwitchableProfile))

#: 주문(place/cancel/modify)과 해외 조회가 열리는 프로필 -- 위탁(general)만. 연금/ISA/IRP 맥락은
#: 전환해 **읽을** 수는 있어도 주문 경로/해외 조회는 열리지 않는다(한 상수, 한 의미 -- 주문 게이트와
#: 해외-조회 게이트가 같은 기준을 공유한다).
_ORDER_ENABLED_PROFILES = frozenset({"general"})

#: 위탁(일반 주식) 상품코드 -- 주문/해외조회가 열리는 유일한 상품코드(연금저축 22/IRP 29 는 조회만).
_COMMISSION_PRODUCT_CODE = "01"


def is_order_eligible_account(account: object) -> bool:
    """이 계좌 뷰가 주문/해외조회 대상인지 -- 위탁(상품코드 01) 주식 계좌만 ``True``. 파생 뷰(StockAccount
    아님)·연금저축(22)·IRP(29) 는 ``False``. 주문 게이트와 해외-조회 게이트가 공유하는 단일 술어라
    'general + 01' 합성이 두 곳에 각각 코딩되지 않는다."""
    return isinstance(account, StockAccount) and account.product_code == _COMMISSION_PRODUCT_CODE


def _account_masked(kis: KISClient) -> str:
    """계좌번호를 마지막 4자리만 남기고 마스킹(전체 계좌번호 비노출)."""
    acct = str(getattr(kis, "_account", "") or "")
    digits = "".join(ch for ch in acct if ch.isdigit())
    return f"****{digits[-4:]}" if len(digits) >= 4 else "****"


def _account_descriptor(client: KISClient) -> tuple[str | None, str | None]:
    """best-effort ``(account_type, product_code)`` -- ``current_account`` 설명용. 계좌 미설정/미지원
    계좌면 판별 불가 자리에 ``None`` 을 둔다(크래시 없이 설명만)."""
    try:
        account = client.account
    except KISUsageError:
        return None, None
    account_type = getattr(account, "kind", type(account).__name__)
    try:
        product_code: str | None = account.product_code
    except (AttributeError, KISUsageError):
        product_code = None
    return account_type, product_code
