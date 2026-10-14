"""지수·환율·국채·금 기간봉 명령 -- 공개 ``index(...)`` 핸들의 ``.bars()`` 를 그대로 운반한다
(재계산·필터 없음). 종목 ``cmd_bars`` 의 지수류 형제다 -- 리시버가 ``stock`` 이 아니라 ``index`` 라
별도 명령 그룹(``kis index``)을 쓴다."""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING, Any

from ..errors import CliConfigError

if TYPE_CHECKING:
    from ...client import KISClient


def cmd_index_bars(kis: KISClient, args: Namespace) -> Any:
    """지수·환율·국채·금 기간봉. ``--region overseas`` 는 ``--kind`` index/fx(환율, 예 code FX@KRW=
    원/달러)/bond/gold, ``--region domestic`` 은 국내 지수코드(kind 미적용)."""
    if args.region == "overseas":
        # --kind 미지정이면 라이브러리 기본(index)을 쓴다 -- CLI 가 기본값을 재기술하지 않는다.
        extra = {} if args.kind is None else {"kind": args.kind}
        return kis.overseas.index(args.code, **extra).bars(
            args.interval, start=args.start, end=args.end)
    if args.kind is not None:
        raise CliConfigError("--kind 는 해외(--region overseas) 전용입니다.")
    return kis.domestic.index(args.code).bars(args.interval, start=args.start, end=args.end)
