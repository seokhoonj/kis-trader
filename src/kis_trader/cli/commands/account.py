"""계좌 조회 명령 -- 잔고/보유/미체결. 통화가 다른 해외 시장을 CLI 가 임의 합산하지 않는다."""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING, Any

from ...account import StockAccounts
from ..errors import CliConfigError

if TYPE_CHECKING:
    from ...client import KISClient


def _stock_account(kis: KISClient) -> StockAccounts:
    """주식(위탁 01) 계좌 뷰만 받는다 -- 잔고/보유/미체결 명령은 시장별(domestic/overseas)
    뷰가 필요하므로 다른 상품계좌(선물옵션 03 등)면 명확히 거부한다."""
    view = kis.account
    if not isinstance(view, StockAccounts):
        raise CliConfigError("이 명령은 주식(위탁 01) 계좌에서만 사용할 수 있습니다.")
    return view


def cmd_balance(kis: KISClient, args: Namespace) -> Any:
    account = _stock_account(kis)
    if args.venue == "overseas":
        if not args.market:
            raise CliConfigError("해외 잔고는 시장을 지정해야 합니다(--market US/HK/CN_SH/...).")
        return account.overseas.balance(market=args.market)
    return account.domestic.balance()


def cmd_positions(kis: KISClient, args: Namespace) -> Any:
    account = _stock_account(kis)
    if args.venue == "overseas":
        return account.overseas.positions(market=args.market)
    return account.domestic.positions()


def cmd_orders(kis: KISClient, args: Namespace) -> Any:
    account = _stock_account(kis)
    if args.venue == "overseas":
        return account.overseas.open_orders(market=args.market)
    return account.domestic.open_orders()
