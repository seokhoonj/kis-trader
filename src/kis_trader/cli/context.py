"""세션 조립 -- 환경변수와 플래그를 :class:`~kis_trader.client.KISClient` 로 해석.

자격증명은 오직 환경변수(``KIS_APP_KEY``/``KIS_APP_SECRET``/``KIS_ACCOUNT``)에서 읽는다.
플래그로 받지 않는 이유: 셸 히스토리·``ps`` 출력도 노출 경로이기 때문이다. 값은 어디에도
echo 하지 않는다(누락 여부만 확인).
"""
from __future__ import annotations

import os
from argparse import Namespace
from typing import TYPE_CHECKING

from ..client import KISClient
from .errors import CliConfigError

if TYPE_CHECKING:
    from ..domestic.stock import DomesticStock
    from ..overseas.stock import OverseasStock


def resolve_account(args: Namespace) -> str | None:
    """계좌번호 결정 -- ``--account`` 플래그 우선, 없으면 ``KIS_ACCOUNT``."""
    return getattr(args, "account", None) or os.environ.get("KIS_ACCOUNT") or None


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
    """플래그·환경변수로 세션을 연다. 자격증명이 없으면 :class:`CliConfigError`(종료 코드 3)."""
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise CliConfigError(
            "자격증명이 없습니다 -- KIS_APP_KEY / KIS_APP_SECRET 환경변수를 설정하세요."
        )
    return KISClient(
        app_key=app_key,
        app_secret=app_secret,
        account=resolve_account(args),
        environment=args.env,
    )
