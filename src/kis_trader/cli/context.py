"""세션 조립 -- 프로필과 플래그를 :class:`~kis_trader.client.KISClient` 로 해석.

자격증명은 :func:`~kis_trader.config.resolve_credentials` 가 프로필별로 해석한다(환경변수 ->
``credentials.json``). 자격증명을 플래그로 받지 않는 이유: 셸 히스토리·``ps``
출력도 노출 경로이기 때문이다. 값은 어디에도 echo 하지 않는다(누락 여부만 확인).
"""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING

from ..client import KISClient
from ..config import order_store_path, resolve_credentials
from ..errors import KISUsageError
from ..store import OrderStore
from .errors import CliConfigError

if TYPE_CHECKING:
    from ..domestic.bond import Bond
    from ..domestic.stock import DomesticStock
    from ..overseas.stock import OverseasStock


def account_suffix(account: str | None) -> str:
    """계좌 마지막 4자리(하이픈 제거 후) -- real 주문 확인용. 마스킹된 식별자라 노출해도 안전하다."""
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


def resolve_bond(kis: KISClient, args: Namespace) -> Bond:
    """장내채권 종목 핸들을 만든다(국내 전용). 채권은 지역/거래소 축이 없다."""
    return kis.domestic.bond(args.identifier)


def build_client(args: Namespace) -> KISClient:
    """프로필로 세션을 연다. 자격증명이 없거나 형상 오류면 :class:`CliConfigError`(종료 코드 3).

    ``--profile`` 이 어느 자격증명 묶음과 환경(실전/모의)을 쓸지 정한다. ``--account`` 플래그가
    있으면 프로필이 해석한 계좌 대신 그것을 쓴다.

    라이브러리 기본 :class:`~kis_trader.store.OrderStore` 는 인메모리라 프로세스가 끝나면 dedup 이
    사라진다. CLI 는 호출마다 새 프로세스라 이전 호출이 낸 주문을 취소·재조회·dedup 하려면 상태가
    디스크에 남아야 한다 -- 계좌·환경별 영속 저장소(:func:`~kis_trader.config.order_store_path`)를
    주입한다. 계좌가 없는(시세 전용) 세션은 dedup 대상이 없어 인메모리 그대로 둔다."""
    account = getattr(args, "account", None) or None
    try:
        # 계좌·환경을 먼저 해석해 그에 맞는 영속 저장소 경로를 정한다(세션 생성이 파일을 한 번 더
        # 읽지만 같은 저장분이라 일관된다). 자격증명 누락/형상 오류는 여기서 KISUsageError 로 난다.
        credentials = resolve_credentials(args.profile)
        store_account = account or credentials.account
        store = (
            OrderStore(path=order_store_path(account=store_account, environment=credentials.environment))
            if store_account is not None else None
        )
        return KISClient(profile=args.profile, account=account, store=store)
    except KISUsageError as err:
        # 자격증명 누락/형상 오류(변수 이름만 담김) -> CLI 설정 오류로 번역(값은 노출 안 됨).
        raise CliConfigError(str(err)) from err
