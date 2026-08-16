"""세션 조립 -- 프로필과 플래그를 :class:`~kis_trader.client.KISClient` 로 해석.

자격증명은 :class:`~kis_trader.config.KISConfig` 가 프로필별로 해석한다(환경변수 ->
``credentials.json`` -> ``config.toml``). 자격증명을 플래그로 받지 않는 이유: 셸 히스토리·``ps``
출력도 노출 경로이기 때문이다. 값은 어디에도 echo 하지 않는다(누락 여부만 확인).
"""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING

from ..client import KISClient
from ..config import resolve_environment as _profile_environment
from ..errors import KISUsageError
from .errors import CliConfigError

if TYPE_CHECKING:
    from ..domestic.stock import DomesticStock
    from ..overseas.stock import OverseasStock
    from ..transport import Environment


def resolve_environment(args: Namespace) -> Environment:
    """접속 환경(실전/모의) -- ``--profile`` 에 저장된 값(기본 real). 주문 게이트·출력 메타가 이걸로
    판정한다. 프로필 이름이 아니라 저장된 environment 필드가 정한다."""
    return _profile_environment(args.profile)


def account_suffix(account: str | None) -> str:
    """계좌 끝 4자리(하이픈 제거 후) -- real 주문 확인용. 마스킹된 식별자라 노출해도 안전하다."""
    if not account:
        return ""
    digits = account.replace("-", "")
    return digits[-4:]


def resolve_stock(kis: KISClient, args: Namespace) -> DomesticStock | OverseasStock:
    """``--venue`` 로 국내/해외 종목 핸들을 만든다(해외는 ``--exchange``, 생략 시 자동 판별).
    반환은 DomesticStock 또는 OverseasStock -- 둘의 공통 시세·주문 메서드를 CLI 가 쓴다."""
    if args.venue == "overseas":
        return kis.overseas.stock(args.identifier, exchange=args.exchange)
    return kis.domestic.stock(args.identifier)


def build_client(args: Namespace) -> KISClient:
    """프로필로 세션을 연다. 자격증명이 없거나 형상 오류면 :class:`CliConfigError`(종료 코드 3).

    ``--profile`` 이 어느 자격증명 묶음과 환경(실전/모의)을 쓸지 정한다. ``--account`` 플래그가
    있으면 프로필이 해석한 계좌 대신 그것을 쓴다."""
    account = getattr(args, "account", None) or None
    try:
        return KISClient(profile=args.profile, account=account)
    except KISUsageError as err:
        # 자격증명 누락/형상 오류(변수 이름만 담김) -> CLI 설정 오류로 번역(값은 노출 안 됨).
        raise CliConfigError(str(err)) from err
