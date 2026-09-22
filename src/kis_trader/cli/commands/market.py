"""검색·순위 명령 -- 라이브러리 어휘(search/ranking)를 그대로 쓴다(경계 동의어 금지)."""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...client import KISClient


def cmd_search(kis: KISClient, args: Namespace) -> Any:
    """이름/코드로 후보를 **모두** 돌려준다 -- 모호해도 한 종목으로 임의 확정하지 않는다."""
    # --market 미지정이면 라이브러리 기본을 쓴다(CLI 가 기본값을 재기술하지 않는다).
    extra = {} if args.market is None else {"market": args.market}
    return kis.domestic.search(args.query, **extra)


def cmd_ranking_change(kis: KISClient, args: Namespace) -> Any:
    return kis.domestic.ranking.by_change(direction=args.direction)


def cmd_ranking_volume(kis: KISClient, args: Namespace) -> Any:
    # --metric 미지정이면 라이브러리 기본을 쓴다(CLI 가 기본값을 재기술하지 않는다).
    extra = {} if args.metric is None else {"metric": args.metric}
    return kis.domestic.ranking.by_volume(**extra)


def cmd_ranking_market_cap(kis: KISClient, args: Namespace) -> Any:
    return kis.domestic.ranking.by_market_cap()
