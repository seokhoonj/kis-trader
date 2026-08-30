"""계좌 조회 명령 -- 잔고/보유/미체결. 통화가 다른 해외 시장을 CLI 가 임의 합산하지 않는다."""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING, Any

from ...account import StockAccount
from ..errors import CliConfigError

if TYPE_CHECKING:
    from ...client import KISClient


def _stock_account(kis: KISClient) -> StockAccount:
    """주식(위탁 01) 계좌 뷰가 필요한 조회·명령에서만 호출된다 -- 다른 상품계좌(선물옵션 03 등)면
    명확히 거부한다."""
    view = kis.account
    if not isinstance(view, StockAccount):
        raise CliConfigError("이 명령은 주식(위탁 01) 계좌에서만 사용할 수 있습니다.")
    return view


def cmd_balance(kis: KISClient, args: Namespace) -> Any:
    account = _stock_account(kis)
    if getattr(args, "asset", "stock") == "bond":
        # 장내채권 lot 목록(buy_date/buy_sequence/잔량/매수단가) -- 채권 매도의 lot 지목에 필요하다.
        # 목록의 buy_sequence 컬럼을 매도 시 --buy-seq 로, buy_date 를 --buy-date 로 넘긴다.
        if args.venue == "overseas":
            raise CliConfigError("장내채권은 국내 전용입니다(--venue overseas 불가).")
        return account.domestic.bonds.balance()
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
    if getattr(args, "asset", "stock") == "bond":
        # 채권 미체결(정정취소가능) 조회 -- 실주문 타임아웃 시 상태 확인 경로. 주문일자 필수.
        if args.venue == "overseas":
            raise CliConfigError("장내채권은 국내 전용입니다(--venue overseas 불가).")
        if not args.date:
            raise CliConfigError("채권 미체결 조회는 주문일자가 필요합니다(--date YYYYMMDD).")
        return account.domestic.bonds.open_orders(args.date)
    if args.venue == "overseas":
        return account.overseas.open_orders(market=args.market)
    return account.domestic.open_orders()


def cmd_fills(kis: KISClient, args: Namespace) -> Any:
    account = _stock_account(kis)
    asset = getattr(args, "asset", "stock")
    # 국내주식/장내채권 일별 주문·체결 내역(기간). 날짜 8자리 형식 검증은 라이브러리가 수행하므로
    # CLI 는 기간 존재만 확인한다(미체결 조회의 --date 검증과 같은 방식).
    if args.venue == "overseas":
        raise CliConfigError("체결내역 조회는 국내 전용입니다(--venue overseas 불가).")
    if not args.start or not args.end:
        raise CliConfigError("체결내역 조회는 기간이 필요합니다(--start/--end YYYYMMDD).")
    # 사용자가 준 필터만 전달한다 -- side/symbol/unfilled_only 의 기본값은 라이브러리가 정한다
    # (경계: 소비자가 패키지 기본값을 재기술하지 않는다).
    filters: dict[str, Any] = {}
    if args.side is not None:
        filters["side"] = args.side
    if args.symbol is not None:
        filters["symbol"] = args.symbol
    if args.unfilled_only:
        filters["unfilled_only"] = True
    if asset == "bond":
        return account.domestic.bonds.fills(start=args.start, end=args.end, **filters)
    return account.domestic.fills(start=args.start, end=args.end, **filters)


def cmd_reserved(kis: KISClient, args: Namespace) -> Any:
    account = _stock_account(kis)
    if not args.start or not args.end:
        raise CliConfigError("예약주문 조회는 기간이 필요합니다(--start/--end YYYYMMDD).")
    if args.venue == "overseas":
        # 해외 예약주문 조회(미국+아시아 합산). process 는 국내 전용 개념이라 거부. 실전전용은
        # 라이브러리가 소유(조회 TR 이 모의 미지원 -- CLI 중복검증 없음).
        if args.process is not None:
            raise CliConfigError("--process 는 국내 예약주문 조회 전용입니다.")
        return account.overseas.reserved_orders(start=args.start, end=args.end)
    # 사용자가 준 필터만 전달한다 -- process 기본값은 라이브러리가 정한다(경계: 소비자가 패키지 기본값을 재기술하지 않는다).
    filters: dict[str, Any] = {}
    if args.process is not None:
        filters["process"] = args.process
    return account.domestic.reserved_orders(start=args.start, end=args.end, **filters)


def cmd_profits(kis: KISClient, args: Namespace) -> Any:
    account = _stock_account(kis)
    if not args.start or not args.end:
        raise CliConfigError("손익 조회는 기간이 필요합니다(--start/--end YYYYMMDD).")
    # 사용자가 준 필터만 전달한다 -- 기본값은 라이브러리가 정한다(경계: 소비자가 패키지 기본값을 재기술하지 않는다).
    if args.venue == "overseas":
        # 해외주식 기간손익. --by/--sort 는 국내 손익 전용.
        if args.by != "symbol":
            raise CliConfigError("--by day 는 국내 손익 전용입니다(해외는 기간손익 단일).")
        if args.sort is not None:
            raise CliConfigError("--sort 는 국내 손익 전용입니다.")
        extra: dict[str, Any] = {}
        if args.symbol is not None:
            extra["symbol"] = args.symbol
        if args.currency is not None:
            extra["currency"] = args.currency
        if args.won_basis:
            extra["won_basis"] = True
        return account.overseas.period_profit(start=args.start, end=args.end, **extra)
    # 국내주식: --by symbol(종목별 실현손익) / day(일별 매매손익).
    if args.currency is not None:
        raise CliConfigError("--currency 는 해외 손익 전용입니다.")
    if args.won_basis:
        raise CliConfigError("--won-basis 는 해외 손익 전용입니다.")
    extra = {}
    if args.symbol is not None:
        extra["symbol"] = args.symbol
    if args.sort is not None:
        extra["sort"] = args.sort
    if args.by == "day":
        return account.domestic.daily_profits(start=args.start, end=args.end, **extra)
    return account.domestic.trade_profits(start=args.start, end=args.end, **extra)


def cmd_transactions(kis: KISClient, args: Namespace) -> Any:
    account = _stock_account(kis)
    if args.venue != "overseas":
        raise CliConfigError("거래·입출금내역 조회는 현재 해외만 지원합니다(--venue overseas).")
    if not args.start or not args.end:
        raise CliConfigError("거래내역 조회는 기간이 필요합니다(--start/--end YYYYMMDD).")
    # 사용자가 준 필터만 전달한다 -- side/symbol 기본값은 라이브러리가 정한다(경계).
    extra: dict[str, Any] = {}
    if args.symbol is not None:
        extra["symbol"] = args.symbol
    if args.side is not None:
        extra["side"] = args.side
    return account.overseas.transactions(start=args.start, end=args.end, **extra)
