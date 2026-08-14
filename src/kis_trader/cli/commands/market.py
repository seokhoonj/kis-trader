"""검색·순위 명령 -- 라이브러리 어휘(search/ranking)를 그대로 쓴다(경계 동의어 금지)."""
from __future__ import annotations

from argparse import Namespace
from typing import Any


def cmd_search(kis: Any, args: Namespace) -> Any:
    """이름/코드로 후보를 **모두** 돌려준다 -- 모호해도 한 종목으로 임의 확정하지 않는다."""
    return kis.domestic.search(args.query, market=args.market)


def cmd_ranking_change(kis: Any, args: Namespace) -> Any:
    return kis.domestic.ranking.by_change(direction=args.direction)


def cmd_ranking_volume(kis: Any, args: Namespace) -> Any:
    return kis.domestic.ranking.by_volume()


def cmd_ranking_market_cap(kis: Any, args: Namespace) -> Any:
    return kis.domestic.ranking.by_market_cap()
