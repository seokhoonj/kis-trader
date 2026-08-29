"""``kis`` 명령 애플리케이션 -- 파서 조립, dispatch, 오류 -> 종료 코드 번역.

CLI 는 consumer(어댑터)다: 인자를 공개 :class:`~kis_trader.client.KISClient` 호출로 옮기고
결과를 표시할 뿐, 잔고 합산·순위 재계산·주문 판정 같은 도메인 로직을 만들지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, get_args

from .. import (
    Direction,
    DomesticDivision,
    ReservedCurrency,
    ReservedProcess,
    SearchMarket,
    VolumeMetric,
)
from ..errors import KISError
from ..order import Side
from .commands import account, market, order, stock
from .context import account_suffix, build_client
from .errors import CliAborted, CliConfigError, Translated, translate
from .output import render

if TYPE_CHECKING:
    from ..client import KISClient


def _distribution_version() -> str:
    try:
        return version("kis-trader")
    except PackageNotFoundError:  # 개발 트리에서 미설치
        return "0.0.0"


def _add_venue(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--venue", choices=["domestic", "overseas"], default="domestic",
                     help="국내(기본)/해외")
    sub.add_argument("--exchange", default=None, help="해외 거래소코드(생략 시 자동 판별)")


def _add_order_gate(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--execute", choices=["paper", "real"], default=None,
                     help="전송 권한 겸 환경 선언(프로필 환경과 일치해야 함). 없으면 dry-run")
    sub.add_argument("--yes", action="store_true", help="비대화형 전송 확인(대화형이면 프롬프트)")
    sub.add_argument("--confirm-account", dest="confirm_account", default=None,
                     help="비대화형 real 주문: 계좌 끝 4자리")


def _common_flags() -> argparse.ArgumentParser:
    """모든 하위 명령이 공유하는 전역 플래그(부모 파서). ``SUPPRESS`` 기본값이라, 하위 명령에서
    주지 않으면 최상위 파서가 정한 값을 덮어쓰지 않는다 -- 그래서 ``--format`` 등을 하위 명령
    앞·뒤 어디에 놓아도 동작한다."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", default=argparse.SUPPRESS,
                        help="자격증명 프로필(자유 이름). 미지정 시 KIS_DEFAULT_PROFILE > default_profile 마커 > credentials.json 첫 항목 > main. 환경은 프로필에 저장된 값")
    common.add_argument("--account", default=argparse.SUPPRESS, help="계좌번호(생략 시 프로필 계좌)")
    common.add_argument("--format", dest="fmt", choices=["table", "json", "jsonl"],
                        default=argparse.SUPPRESS, help="출력 형식(기본 table)")
    common.add_argument("--no-header", dest="no_header", action="store_true",
                        default=argparse.SUPPRESS, help="표 머리글 생략")
    common.add_argument("--include-raw", dest="include_raw", action="store_true",
                        default=argparse.SUPPRESS, help="._raw 원본 포함(--format json/jsonl 과 함께만)")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_flags()  # 하위 명령용(SUPPRESS 기본값)
    parser = argparse.ArgumentParser(
        prog="kis", description="한국투자증권(KIS) Open API 클라이언트 kis_trader 의 명령줄 도구.")
    parser.add_argument("--version", action="version", version=f"kis {_distribution_version()}")
    # 최상위는 실제 기본값을 직접 가진다. 하위 명령은 common(SUPPRESS)이라, 미지정 시 이 값을
    # 덮어쓰지 않고 그대로 유지한다 -- 그래서 전역 플래그를 하위 명령 앞뒤 어디에 놓아도 된다.
    parser.add_argument("--profile", default=None,
                        help="자격증명 프로필(자유 이름). 미지정 시 KIS_DEFAULT_PROFILE > default_profile 마커 > credentials.json 첫 항목 > main. 환경은 프로필에 저장된 값")
    parser.add_argument("--account", default=None, help="계좌번호(생략 시 프로필 계좌)")
    parser.add_argument("--format", dest="fmt", choices=["table", "json", "jsonl"], default="table",
                        help="출력 형식(기본 table)")
    parser.add_argument("--no-header", dest="no_header", action="store_true", help="표 머리글 생략")
    parser.add_argument("--include-raw", dest="include_raw", action="store_true",
                        help="._raw 원본 포함(--format json/jsonl 과 함께만)")
    groups = parser.add_subparsers(dest="group", required=True)

    def leaf(subparsers: Any, name: str, **kw: Any) -> argparse.ArgumentParser:
        subcommand_parser: argparse.ArgumentParser = subparsers.add_parser(
            name, parents=[common], **kw
        )
        return subcommand_parser

    # kis stock quote|bars|book|trades|status
    stock_p = groups.add_parser("stock", help="종목 시세")
    stock_sub = stock_p.add_subparsers(dest="action", required=True)
    for name, func in [("quote", stock.cmd_quote), ("book", stock.cmd_book),
                       ("trades", stock.cmd_trades), ("status", stock.cmd_status)]:
        sp = leaf(stock_sub, name)
        sp.add_argument("identifier", help="종목코드(국내 6자리) 또는 심볼(해외)")
        _add_venue(sp)
        sp.set_defaults(func=func)
    bars_p = leaf(stock_sub, "bars")
    bars_p.add_argument("identifier")
    bars_p.add_argument("--interval", required=True, help="1m/1d/1wk/1mo 등")
    bars_p.add_argument("--start", default=None, help="YYYYMMDD")
    bars_p.add_argument("--end", default=None, help="YYYYMMDD")
    _add_venue(bars_p)
    bars_p.set_defaults(func=stock.cmd_bars)

    # kis search
    search_p = leaf(groups, "search", help="이름/코드로 국내 종목 검색")
    search_p.add_argument("query")
    search_p.add_argument("--market", choices=list(get_args(SearchMarket)), default="all")
    search_p.set_defaults(func=market.cmd_search)

    # kis ranking change|volume|market-cap
    ranking_p = groups.add_parser("ranking", help="시장 순위")
    ranking_sub = ranking_p.add_subparsers(dest="action", required=True)
    rc = leaf(ranking_sub, "change")
    rc.add_argument("--direction", choices=list(get_args(Direction)), required=True)
    rc.set_defaults(func=market.cmd_ranking_change)
    rv = leaf(ranking_sub, "volume")
    rv.add_argument("--metric", choices=list(get_args(VolumeMetric)), default="cumulative_trading_amount",
                    help="거래량 기준(기본 cumulative_trading_amount 거래대금): trading_volume(거래량)/"
                         "cumulative_trading_amount(거래대금)/volume_growth(거래증가율)/turnover(회전율)")
    rv.set_defaults(func=market.cmd_ranking_volume)
    leaf(ranking_sub, "market-cap").set_defaults(func=market.cmd_ranking_market_cap)

    # kis account balance|positions|orders|fills|reserved|profits|transactions
    account_p = groups.add_parser("account", help="계좌 잔고·보유·미체결·체결내역·예약주문·손익·거래내역")
    account_sub = account_p.add_subparsers(dest="action", required=True)
    for name, func in [("balance", account.cmd_balance), ("positions", account.cmd_positions),
                       ("orders", account.cmd_orders), ("fills", account.cmd_fills),
                       ("reserved", account.cmd_reserved), ("profits", account.cmd_profits),
                       ("transactions", account.cmd_transactions)]:
        sp = leaf(account_sub, name)
        sp.add_argument("--venue", choices=["domestic", "overseas"], default="domestic")
        sp.add_argument("--market", default=None, help="해외 시장(US/HK/CN_SH/...)")
        if name in ("balance", "orders", "fills"):
            sp.add_argument("--asset", choices=["stock", "bond"], default="stock",
                            help="자산군: stock(기본)/bond(장내채권)")
        if name == "orders":
            sp.add_argument("--date", dest="date", default=None,
                            help="채권 미체결 조회 주문일자 YYYYMMDD(--asset bond 전용)")
        if name == "fills":
            sp.add_argument("--start", dest="start", default=None,
                            help="체결내역 조회 시작일 YYYYMMDD(국내 주식/채권)")
            sp.add_argument("--end", dest="end", default=None,
                            help="체결내역 조회 종료일 YYYYMMDD(국내 주식/채권)")
            sp.add_argument("--side", dest="side", choices=["all", "buy", "sell"], default=None,
                            help="매매구분: all(기본)/buy/sell")
            sp.add_argument("--symbol", dest="symbol", default=None,
                            help="종목코드(주식 6자리 / 채권 ISIN); 생략 시 전체 종목")
            sp.add_argument("--unfilled-only", dest="unfilled_only", action="store_true",
                            help="미체결만")
        if name == "reserved":
            sp.add_argument("--start", dest="start", default=None,
                            help="예약주문 조회 시작일 YYYYMMDD")
            sp.add_argument("--end", dest="end", default=None,
                            help="예약주문 조회 종료일 YYYYMMDD")
            sp.add_argument("--process", dest="process",
                            choices=list(get_args(ReservedProcess)), default=None,
                            help="처리상태: all(기본)/processed/unprocessed")
        if name == "profits":
            sp.add_argument("--start", dest="start", default=None, help="손익 조회 시작일 YYYYMMDD")
            sp.add_argument("--end", dest="end", default=None, help="손익 조회 종료일 YYYYMMDD")
            sp.add_argument("--symbol", dest="symbol", default=None,
                            help="종목코드; 생략 시 전체 종목")
            sp.add_argument("--by", dest="by", choices=["symbol", "day"], default="symbol",
                            help="국내: symbol(종목별 실현손익, 기본)/day(일별 매매손익)")
            sp.add_argument("--sort", dest="sort", choices=["recent", "oldest"], default=None,
                            help="국내 정렬: recent(기본)/oldest")
            sp.add_argument("--currency", dest="currency", default=None,
                            help="해외 통화(생략 시 전체)")
            sp.add_argument("--won-basis", dest="won_basis", action="store_true",
                            help="해외 손익을 원화 기준으로(생략 시 외화)")
        if name == "transactions":
            sp.add_argument("--start", dest="start", default=None, help="거래내역 조회 시작일 YYYYMMDD")
            sp.add_argument("--end", dest="end", default=None, help="거래내역 조회 종료일 YYYYMMDD")
            sp.add_argument("--symbol", dest="symbol", default=None,
                            help="종목코드; 생략 시 전체 종목")
            sp.add_argument("--side", dest="side", choices=["all", "buy", "sell"], default=None,
                            help="매매구분: all(기본)/buy/sell")
        sp.set_defaults(func=func)

    # kis order buy|sell|reconcile|modify|cancel|cancel-reserved|modify-reserved
    order_p = groups.add_parser("order", help="주문(기본 dry-run; --execute 로 전송)")
    order_sub = order_p.add_subparsers(dest="action", required=True)
    for name, func in [("buy", order.cmd_buy), ("sell", order.cmd_sell)]:
        sp = leaf(order_sub, name)
        sp.add_argument("identifier")
        sp.add_argument("quantity", type=int)
        sp.add_argument("--limit-price", dest="limit_price", default=None,
                        help="지정가(생략 시 시장가)")
        sp.add_argument("--division", choices=list(get_args(DomesticDivision)), default=None,
                        help="KRX 주문구분(국내 현금/파생): conditional_limit(조건부지정가)/"
                             "immediate_limit(최유리지정가)/priority_limit(최우선지정가; 현금 전용)/"
                             "midpoint(중간가; 현금 전용)/pre_market_close(장전 시간외; 현금 전용)/"
                             "post_market_close(장후 시간외; 현금 전용)/after_hours_single(시간외 단일가; 현금 전용)")
        sp.add_argument("--asset", choices=["stock", "bond", "futures", "option"],
                        default="stock", help="자산군: stock(기본)/bond(장내채권)/futures/option(파생)")
        sp.add_argument("--buy-date", dest="buy_date", default=None,
                        help="채권 매도 lot 매수일자 YYYYMMDD(bond sell 전용)")
        sp.add_argument("--buy-seq", dest="buy_seq", default=None,
                        help="채권 매도 lot 매수순번(bond sell 전용)")
        sp.add_argument("--right", choices=["call", "put"], default=None,
                        help="옵션 콜/풋(국내 --asset option 전용)")
        sp.add_argument("--night", action="store_true",
                        help="국내 파생 야간장(--asset futures/option 전용, 실전전용)")
        sp.add_argument("--stop-price", dest="stop_price", default=None,
                        help="스톱 트리거가 -- 국내 주식 스톱지정가(--limit-price 와 함께) 또는 "
                             "해외 파생 스톱")
        sp.add_argument("--reserve", action="store_true",
                        help="예약주문: 국내 주식(다음 영업일 동시호가, 실전전용) 또는 "
                             "해외 주식(--venue overseas, 정규장 시작 전 예약, 지정가 필수, 모의 허용)")
        sp.add_argument("--end-date", dest="end_date", default=None,
                        help="예약 유효 종료일 YYYYMMDD(국내 --reserve 전용; 해외 예약은 미지원)")
        sp.add_argument("--currency", choices=list(get_args(ReservedCurrency)), default=None,
                        help="해외 예약 통화 -- 홍콩 예약 전용(그 외 거래소에 주면 거부)")
        _add_venue(sp)
        _add_order_gate(sp)
        sp.set_defaults(func=func)
    rec = leaf(order_sub, "reconcile")
    rec.add_argument("client_order_id")
    rec.set_defaults(func=order.cmd_reconcile)
    mod = leaf(order_sub, "modify")
    mod.add_argument("client_order_id")
    mod.add_argument("--limit-price", dest="limit_price", required=True)
    mod.add_argument("--quantity", type=int, default=None)
    _add_order_gate(mod)
    mod.set_defaults(func=order.cmd_modify)
    can = leaf(order_sub, "cancel")
    can.add_argument("client_order_id")
    can.add_argument("--quantity", type=int, default=None)
    _add_order_gate(can)
    can.set_defaults(func=order.cmd_cancel)
    rcan = leaf(order_sub, "cancel-reserved")
    rcan.add_argument("sequence", help="국내 예약순번 또는 해외(미국) 예약번호")
    rcan.add_argument("--venue", choices=["domestic", "overseas"], default="domestic",
                      help="국내(기본)/해외. 해외는 미국 예약만 -- 아시아는 kis order cancel 로 취소")
    rcan.add_argument("--order-date", dest="order_date", default=None,
                      help="예약집행 예정일 YYYYMMDD(국내 전용, 같은 순번 구분이 필요할 때)")
    rcan.add_argument("--receipt-date", dest="receipt_date", default=None,
                      help="접수일자 YYYYMMDD(해외 미국 예약 취소 전용, 필수)")
    _add_order_gate(rcan)
    rcan.set_defaults(func=order.cmd_cancel_reserved)
    rmod = leaf(order_sub, "modify-reserved")
    rmod.add_argument("sequence")
    rmod.add_argument("--symbol", required=True, help="정정할 종목코드(전체 재지정)")
    rmod.add_argument("--side", choices=list(get_args(Side)), required=True, help="매수/매도")
    rmod.add_argument("--quantity", type=int, required=True, help="정정 수량")
    rmod.add_argument("--limit-price", dest="limit_price", default=None,
                      help="지정가(생략 시 시장가로 재지정 -- 기존 단가 유지 아님)")
    rmod.add_argument("--end-date", dest="end_date", default=None,
                      help="예약 유효 종료일 YYYYMMDD")
    rmod.add_argument("--order-date", dest="order_date", default=None,
                      help="예약집행 예정일 YYYYMMDD(같은 순번 구분이 필요할 때)")
    _add_order_gate(rmod)
    rmod.set_defaults(func=order.cmd_modify_reserved)

    return parser


def _emit(result: Any, args: argparse.Namespace, kis: KISClient) -> None:
    # 환경·계좌는 세션(kis)이 생성 시 이미 해석한 값을 재사용한다(자격증명 재조회 없음).
    meta = {"environment": kis.environment,
            "account_suffix": account_suffix(kis._account)}
    print(render(result, fmt=args.fmt, include_raw=args.include_raw,
                 no_header=args.no_header, meta=meta))


def _emit_error(translated: Translated, args: argparse.Namespace) -> None:
    if args.fmt in ("json", "jsonl"):
        payload = {"error": {
            "outcome": translated.outcome, "message": translated.message,
            "retryable": translated.retryable, "reconcile_required": translated.reconcile_required,
        }}
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"오류: {translated.message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """진입점. 종료 코드를 돌려준다(``__main__`` 과 콘솔 스크립트가 ``SystemExit`` 로 감싼다)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        kis = build_client(args)
        result = args.func(kis, args)
    except KeyboardInterrupt:
        print("중단됨.", file=sys.stderr)
        return 130
    except (CliConfigError, CliAborted, KISError) as exc:
        translated = translate(exc)
        _emit_error(translated, args)
        return translated.exit_code
    _emit(result, args, kis)
    return 0
