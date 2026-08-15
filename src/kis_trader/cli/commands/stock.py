"""종목 시세 명령 -- 공개 stock(...) 핸들 호출을 그대로 운반한다(재계산·필터 없음)."""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING, Any

from ..context import resolve_stock
from ..errors import CliConfigError

if TYPE_CHECKING:
    from ...client import KISClient


def cmd_quote(kis: KISClient, args: Namespace) -> Any:
    return resolve_stock(kis, args).quote()


def cmd_bars(kis: KISClient, args: Namespace) -> Any:
    return resolve_stock(kis, args).bars(args.interval, start=args.start, end=args.end)


def cmd_book(kis: KISClient, args: Namespace) -> Any:
    return resolve_stock(kis, args).order_book()


def cmd_trades(kis: KISClient, args: Namespace) -> Any:
    return resolve_stock(kis, args).trades()


def cmd_status(kis: KISClient, args: Namespace) -> Any:
    if args.venue != "domestic":
        raise CliConfigError("status 는 국내 종목만 지원합니다(--venue domestic).")
    return kis.domestic.stock(args.identifier).status()
