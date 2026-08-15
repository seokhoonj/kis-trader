"""계좌 조회 명령 -- 잔고/보유/미체결. 통화가 다른 해외 시장을 CLI 가 임의 합산하지 않는다."""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING, Any

from ..errors import CliConfigError

if TYPE_CHECKING:
    from ...client import KISClient


def cmd_balance(kis: KISClient, args: Namespace) -> Any:
    if args.venue == "overseas":
        if not args.market:
            raise CliConfigError("해외 잔고는 시장을 지정해야 합니다(--market US/HK/CN_SH/...).")
        return kis.overseas.account.balance(market=args.market)
    return kis.domestic.account.balance()


def cmd_positions(kis: KISClient, args: Namespace) -> Any:
    if args.venue == "overseas":
        return kis.overseas.account.positions(market=args.market)
    return kis.domestic.account.positions()


def cmd_orders(kis: KISClient, args: Namespace) -> Any:
    if args.venue == "overseas":
        return kis.overseas.account.open_orders(market=args.market)
    return kis.domestic.account.open_orders()
