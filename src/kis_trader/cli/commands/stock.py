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
    # 사람용 표: 표준 호가창 사다리 -- 가격이 가운데 한 열(호가)로 위에서 아래로 내림차순,
    # 매도잔량은 왼쪽(매도 행에만)·매수잔량은 오른쪽(매수 행에만), 스프레드가 가운데 온다.
    ladder = [
        {"ask_size": level.quantity, "price": level.price, "bid_size": None}
        for level in reversed(book.asks)  # 높은 가격이 위(best ask 가 매도 블록 맨 아래)
    ] + [
        {"ask_size": None, "price": level.price, "bid_size": level.quantity}
        for level in book.bids  # best bid(가장 높은 매수호가)부터 아래로
    ]
    return {
        "symbol": book.symbol,
        "market": book.market,
        "order_book": ladder,
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
