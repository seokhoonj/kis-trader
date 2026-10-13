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

#: 해외 조회(``region="overseas"``)가 열리는 프로필 -- 위탁(general)만. ISA 는 상품코드가 위탁과
#: 같은 01 이라 상품코드로 구별할 수 없어 프로필 이름으로만 가른다(해외 조회가 위탁 전용인 것은 MCP
#: 정책). 주문(place/cancel/modify)은 이 이름 게이트를 쓰지 않고 계좌의 주문 능력(orderable)으로 가른다.
_OVERSEAS_ENABLED_PROFILES = frozenset({"general"})

#: 위탁(일반 주식) 상품코드 -- 해외조회·국내 미체결 조회가 열리는 상품코드(연금저축 22/IRP 29 는 제외).
_COMMISSION_PRODUCT_CODE = "01"


def is_orderable_account(account: object) -> bool:
    """이 계좌 뷰가 주문(place/cancel/modify) 대상인지 -- 주문 가능한 주식 계좌만 ``True``. 위탁(01)/
    ISA(01)/연금저축(22)은 ``True``, IRP(29)는 조회전용이라 ``False``(KIS APBK1744), 파생 뷰
    (StockAccount 아님)도 ``False``. 프로필 이름이 아니라 계좌의 주문 능력(:attr:`StockAccount.orderable`,
    상품코드에서 유도)으로 가른다 -- 전환해 연 ISA/연금저축도 주문 가능하다. 계좌가 미설정이면
    ``account.orderable`` 이 상품코드를 읽다가 :class:`~kis_trader.errors.KISUsageError` 를 전파한다
    (StockAccount 인 경우; 그 자체가 계좌 없는 주문을 깔끔히 막는다)."""
    return isinstance(account, StockAccount) and account.orderable


def require_overseas_general_account(profile: str | None, account: StockAccount) -> None:
    """해외(조회·주문 공통)는 위탁(general) 전용 축으로 가둔다 -- 프로필 이름 general + 상품코드 01.
    ISA 는 상품코드가 위탁과 같은 01 이라 상품코드로는 못 가르므로 이름으로 ISA 를 막고, 상품코드 01
    게이트로 연금저축(22)을 막는다. 실전 요건은 호출자가 별도로 건다(조회=실전 전용, 주문=모의도 허용).
    직접-자격증명(``profile=None``)은 조회 경로와 동일하게 이름 게이트를 건너뛰고 상품코드 01 만 본다
    (운영자 기동 슬롯; 직접-자격증명 ISA 는 상품 01 이라 구별 불가 -- 조회 경로와 같은 한계)."""
    if profile is not None and profile not in _OVERSEAS_ENABLED_PROFILES:
        raise KISUsageError(
            f"해외 주문·조회는 general(위탁) 계좌 전용이다(현재 프로필: {profile}) -- "
            "use_profile('general') 로 바꾸라(ISA/연금저축의 해외 주식 주문 가능 여부 미확인)."
        )
    # 상품코드 01 체크는 순수 계좌 능력(orderable)이 아니라 프로필 이름 + 상품코드를 결합한
    # **MCP 정책**이라 StockAccount 속성으로 올리지 않고 이 leaf 에 둔다.
    if account.product_code != _COMMISSION_PRODUCT_CODE:
        raise KISUsageError(
            f"해외 주문·조회는 위탁(상품코드 {_COMMISSION_PRODUCT_CODE}) 주식 계좌 전용이다 -- "
            f"현재 상품코드 {account.product_code}."
        )


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
