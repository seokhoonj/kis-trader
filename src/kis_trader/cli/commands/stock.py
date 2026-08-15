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
    book = resolve_stock(kis, args).order_book()
    if args.fmt != "table":
        return book  # JSON/jsonl: 구조화된 원본(각 변 최우선 먼저, ISO 시각) 그대로
    # 사람용 표: 표준 호가창 배치 -- 매도(asks)를 높은 가격부터 위에 두어(best ask 가 맨 아래),
    # 그 아래 매수(bids)가 높은 가격부터. 위에서 아래로 가격이 단조감소하고 스프레드가 가운데 온다.
    return {
        "symbol": book.symbol,
        "market": book.market,
        "asks": list(reversed(book.asks)),
        "bids": list(book.bids),
        "total_ask_quantity": book.total_ask_quantity,
        "total_bid_quantity": book.total_bid_quantity,
        "as_of": book.as_of,
    }


def cmd_trades(kis: KISClient, args: Namespace) -> Any:
    return resolve_stock(kis, args).trades()


def cmd_status(kis: KISClient, args: Namespace) -> Any:
    if args.venue != "domestic":
        raise CliConfigError("status 는 국내 종목만 지원합니다(--venue domestic).")
    return kis.domestic.stock(args.identifier).status()
