"""종목 시세 명령 -- 공개 stock(...) 핸들 호출을 그대로 운반한다(재계산·필터 없음)."""
from __future__ import annotations

from argparse import Namespace
from typing import Any

from ..errors import CliConfigError


def _handle(kis: Any, args: Namespace) -> Any:
    if args.venue == "overseas":
        return kis.overseas.stock(args.identifier, exchange=args.exchange)
    return kis.domestic.stock(args.identifier)


def cmd_quote(kis: Any, args: Namespace) -> Any:
    return _handle(kis, args).quote()


def cmd_bars(kis: Any, args: Namespace) -> Any:
    return _handle(kis, args).bars(args.interval, start=args.start, end=args.end)


def cmd_book(kis: Any, args: Namespace) -> Any:
    return _handle(kis, args).order_book()


def cmd_trades(kis: Any, args: Namespace) -> Any:
    return _handle(kis, args).trades()


def cmd_status(kis: Any, args: Namespace) -> Any:
    if args.venue != "domestic":
        raise CliConfigError("status 는 국내 종목만 지원합니다(--venue domestic).")
    return kis.domestic.stock(args.identifier).status()
