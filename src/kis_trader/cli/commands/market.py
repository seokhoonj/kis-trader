"""검색·순위·시장 수급 명령 -- 라이브러리 어휘(search/ranking/market)를 그대로 쓴다(경계 동의어 금지)."""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...client import KISClient


def cmd_search(kis: KISClient, args: Namespace) -> Any:
    """이름/코드로 후보를 **모두** 돌려준다 -- 모호해도 한 종목으로 임의 확정하지 않는다."""
    # --market 미지정이면 라이브러리 기본을 쓴다(CLI 가 기본값을 재기술하지 않는다).
    extra = {} if args.market is None else {"market": args.market}
    return kis.domestic.search_stocks(args.query, **extra)


def cmd_ranking_change(kis: KISClient, args: Namespace) -> Any:
    return kis.domestic.ranking.by_change(direction=args.direction)


def cmd_ranking_volume(kis: KISClient, args: Namespace) -> Any:
    # --metric 미지정이면 라이브러리 기본을 쓴다(CLI 가 기본값을 재기술하지 않는다).
    extra = {} if args.metric is None else {"metric": args.metric}
    return kis.domestic.ranking.by_trading_activity(**extra)


def cmd_ranking_market_cap(kis: KISClient, args: Namespace) -> Any:
    return kis.domestic.ranking.by_market_cap()


def cmd_market_investors(kis: KISClient, args: Namespace) -> Any:
    """시장 전체(KOSPI/KOSDAQ) 투자자 수급 -- 외국인·개인·기관 순매수."""
    # --market 미지정이면 라이브러리 기본을 쓴다(CLI 가 기본값을 재기술하지 않는다).
    extra = {} if args.market is None else {"market": args.market}
    return kis.domestic.market.investor_flows(**extra)


def cmd_market_net_buy(kis: KISClient, args: Namespace) -> Any:
    """투자자별 순매수 상위 종목. 미지정 플래그는 라이브러리 기본을 쓴다(준 것만 전달)."""
    extra = {}
    if args.market is not None:
        extra["market"] = args.market
    if args.basis is not None:
        extra["basis"] = args.basis
    if args.side is not None:
        extra["side"] = args.side
    if args.investor is not None:
        extra["investor"] = args.investor
    return kis.domestic.market.investor_net_buy_stocks(**extra)
