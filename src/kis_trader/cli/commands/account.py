"""계좌 조회 명령 -- 잔고/보유/미체결/체결/손익/증거금. ``kis.account`` 는 프로필 상품코드로
계좌 뷰(주식 01 / 국내선물옵션 03 / 해외선물옵션 08)를 정하므로, 각 명령은 그 뷰 타입에 맞게
디스패치한다. 통화가 다른 해외 시장을 CLI 가 임의 합산하지 않는다."""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING, Any

from ...account import StockAccount
from ...domestic.derivative_account import DomesticDerivativesAccount
from ...overseas.derivative_account import OverseasDerivativesAccount
from ..errors import CliConfigError

if TYPE_CHECKING:
    from ...client import KISClient


def _view(kis: KISClient) -> StockAccount | DomesticDerivativesAccount | OverseasDerivativesAccount:
    """세션 프로필의 계좌 뷰(상품코드로 결정). 명령이 뷰 타입으로 디스패치하는 단일 접근점."""
    return kis.account


def _stock_account(kis: KISClient) -> StockAccount:
    """주식(위탁 01) 계좌 뷰가 필요한 조회에서만 호출된다 -- 선물옵션(03/08) 계좌면 명확히 거부한다."""
    view = _view(kis)
    if not isinstance(view, StockAccount):
        raise CliConfigError("이 명령은 주식(위탁 01) 계좌에서만 사용할 수 있습니다.")
    return view


def _require_range(args: Namespace, label: str) -> None:
    if not args.start or not args.end:
        raise CliConfigError(f"{label} 조회는 기간이 필요합니다(--start/--end YYYYMMDD).")


# 계좌 조회 옵션 플래그의 "미지정" 센티널 -- 값이 이와 다르면 사용자가 명시한 것으로 본다.
# (dest, unset-sentinel, CLI 표기). argparse 는 실행 시점의 계좌 뷰 타입을 모르므로 한 subcommand 가
# 여러 뷰용 플래그를 함께 등록한다 -- 그 중 이 분기가 실제로 안 읽는 플래그를 사용자가 주면, 조용히
# 무시해 '필터된 줄 알지만 아닌' 결과를 내는 대신 거부한다.
_ACCOUNT_FLAGS: tuple[tuple[str, object, str], ...] = (
    ("venue", "domestic", "--venue"), ("market", None, "--market"),
    ("asset", "stock", "--asset"), ("date", None, "--date"),
    ("start", None, "--start"), ("end", None, "--end"),
    ("side", None, "--side"), ("symbol", None, "--symbol"),
    ("unfilled_only", False, "--unfilled-only"), ("process", None, "--process"),
    ("by", "symbol", "--by"), ("sort", None, "--sort"),
    ("currency", None, "--currency"), ("won_basis", False, "--won-basis"),
)


def _reject_foreign_flags(args: Namespace, *, allow: set[str]) -> None:
    """이 (계좌 뷰·명령) 분기가 실제로 쓰는 플래그(``allow``) 밖의 계좌-조회 플래그를 사용자가
    명시했으면 거부한다 -- 조용한 무시로 인한 오조회를 막는다(플래그를 안 준 기본 상태는 통과)."""
    for dest, unset, cli in _ACCOUNT_FLAGS:
        if dest in allow:
            continue
        if getattr(args, dest, unset) != unset:
            raise CliConfigError(f"{cli} 는 이 계좌·명령에서 지원되지 않습니다.")


def cmd_balance(kis: KISClient, args: Namespace) -> Any:
    view = _view(kis)
    if isinstance(view, DomesticDerivativesAccount):
        _reject_foreign_flags(args, allow=set())
        return view.balance()
    if isinstance(view, OverseasDerivativesAccount):
        _reject_foreign_flags(args, allow=set())
        return view.deposit()  # 해외파생은 예수금현황이 자산·증거금 요약이다
    account = _stock_account(kis)
    if getattr(args, "asset", "stock") == "bond":
        # 장내채권 lot 목록(buy_date/buy_sequence/잔량/매수단가) -- 채권 매도의 lot 지목에 필요하다.
        if args.venue == "overseas":
            raise CliConfigError("장내채권은 국내 전용입니다(--venue overseas 불가).")
        return account.domestic.bonds.balance()
    if args.venue == "overseas":
        if not args.market:
            raise CliConfigError("해외 잔고는 시장을 지정해야 합니다(--market US/HK/CN_SH/...).")
        return account.overseas.balance(market=args.market)
    return account.domestic.balance()


def cmd_positions(kis: KISClient, args: Namespace) -> Any:
    view = _view(kis)
    if isinstance(view, OverseasDerivativesAccount):
        _reject_foreign_flags(args, allow=set())
        return view.positions()
    if isinstance(view, DomesticDerivativesAccount):
        raise CliConfigError("국내선물옵션 보유내역은 balance 에 포함됩니다(kis account balance).")
    account = _stock_account(kis)
    if args.venue == "overseas":
        return account.overseas.positions(market=args.market)
    return account.domestic.positions()


def cmd_orders(kis: KISClient, args: Namespace) -> Any:
    view = _view(kis)
    if isinstance(view, DomesticDerivativesAccount):
        # 미체결(정정취소가능). --date 미지정 시 라이브러리 기본에 맡긴다.
        _reject_foreign_flags(args, allow={"date"})
        return view.open_orders(order_date=args.date) if args.date else view.open_orders()
    if isinstance(view, OverseasDerivativesAccount):
        # 기간을 주면 일별 주문내역, 없으면 당일 주문내역.
        _reject_foreign_flags(args, allow={"start", "end"})
        if args.start and args.end:
            return view.daily_orders(start=args.start, end=args.end)
        if args.start or args.end:
            raise CliConfigError("해외선물옵션 기간 주문내역은 --start/--end 를 함께 줘야 합니다.")
        return view.today_orders()
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
    view = _view(kis)
    if isinstance(view, DomesticDerivativesAccount):
        # 선물옵션 기준일체결내역 -- 주문일자(단일일) 기준.
        _reject_foreign_flags(args, allow={"date"})
        if not args.date:
            raise CliConfigError("국내선물옵션 체결내역은 주문일자가 필요합니다(--date YYYYMMDD).")
        return view.base_date_fills(order_date=args.date)
    if isinstance(view, OverseasDerivativesAccount):
        _reject_foreign_flags(args, allow={"start", "end"})
        _require_range(args, "해외선물옵션 체결내역")
        return view.daily_fills(start=args.start, end=args.end)
    account = _stock_account(kis)
    asset = getattr(args, "asset", "stock")
    if args.venue == "overseas":
        raise CliConfigError("체결내역 조회는 국내 전용입니다(--venue overseas 불가).")
    _require_range(args, "체결내역")
    # 사용자가 준 필터만 전달한다 -- 기본값은 라이브러리가 정한다(경계: 소비자가 패키지 기본값을 재기술하지 않는다).
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
    _require_range(args, "예약주문")
    if args.venue == "overseas":
        # 해외 예약주문 조회(미국+아시아 합산). process 는 국내 전용 개념이라 거부. 실전전용은
        # 라이브러리가 소유(조회 TR 이 모의 미지원 -- CLI 중복검증 없음).
        if args.process is not None:
            raise CliConfigError("--process 는 국내 예약주문 조회 전용입니다.")
        return account.overseas.reserved_orders(start=args.start, end=args.end)
    # 사용자가 준 필터만 전달한다 -- process 기본값은 라이브러리가 정한다(경계).
    filters: dict[str, Any] = {}
    if args.process is not None:
        filters["process"] = args.process
    return account.domestic.reserved_orders(start=args.start, end=args.end, **filters)


def cmd_profits(kis: KISClient, args: Namespace) -> Any:
    view = _view(kis)
    if isinstance(view, OverseasDerivativesAccount):
        _reject_foreign_flags(args, allow={"start", "end"})
        _require_range(args, "해외선물옵션 손익")
        return view.period_pnl(start=args.start, end=args.end)
    if isinstance(view, DomesticDerivativesAccount):
        raise CliConfigError(
            "국내선물옵션 손익은 valuation(평가손익)/settlement(정산손익)/commissions 를 쓰세요."
        )
    account = _stock_account(kis)
    _require_range(args, "손익")
    # 사용자가 준 필터만 전달한다 -- 기본값은 라이브러리가 정한다(경계).
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
    view = _view(kis)
    if isinstance(view, OverseasDerivativesAccount):
        _reject_foreign_flags(args, allow={"start", "end"})
        _require_range(args, "해외선물옵션 거래내역")
        return view.transactions(start=args.start, end=args.end)
    if isinstance(view, DomesticDerivativesAccount):
        raise CliConfigError("국내선물옵션은 거래·입출금내역 조회를 제공하지 않습니다.")
    account = _stock_account(kis)
    if args.venue != "overseas":
        raise CliConfigError("거래·입출금내역 조회는 해외주식/해외선물옵션만 지원합니다(--venue overseas).")
    _require_range(args, "거래내역")
    # 사용자가 준 필터만 전달한다 -- side/symbol 기본값은 라이브러리가 정한다(경계).
    extra: dict[str, Any] = {}
    if args.symbol is not None:
        extra["symbol"] = args.symbol
    if args.side is not None:
        extra["side"] = args.side
    return account.overseas.transactions(start=args.start, end=args.end, **extra)


def cmd_deposit(kis: KISClient, args: Namespace) -> Any:
    view = _view(kis)
    if isinstance(view, DomesticDerivativesAccount):
        _reject_foreign_flags(args, allow=set())  # 국내파생 예수금은 통화·일자 축이 없다
        return view.deposit()
    if isinstance(view, OverseasDerivativesAccount):
        _reject_foreign_flags(args, allow={"currency", "date"})
        # 사용자가 준 것만 전달 -- currency/date 기본값은 라이브러리 소유(경계).
        extra: dict[str, Any] = {}
        if args.currency is not None:
            extra["currency"] = args.currency
        if args.date is not None:
            extra["date"] = args.date
        return view.deposit(**extra)
    raise CliConfigError("예수금현황 조회는 선물옵션(03/08) 계좌 전용입니다.")


def cmd_margin(kis: KISClient, args: Namespace) -> Any:
    view = _view(kis)
    if isinstance(view, DomesticDerivativesAccount):
        _reject_foreign_flags(args, allow=set())  # 국내파생은 (야간)증거금상세, 통화·일자 축 없음
        return view.night_margin()
    if isinstance(view, OverseasDerivativesAccount):
        _reject_foreign_flags(args, allow={"currency", "date"})
        extra: dict[str, Any] = {}
        if args.currency is not None:
            extra["currency"] = args.currency
        if args.date is not None:
            extra["date"] = args.date
        return view.margin_detail(**extra)
    raise CliConfigError("증거금상세 조회는 선물옵션(03/08) 계좌 전용입니다.")


def cmd_valuation(kis: KISClient, args: Namespace) -> Any:
    view = _view(kis)
    if isinstance(view, DomesticDerivativesAccount):
        _reject_foreign_flags(args, allow=set())
        return view.valuation_pl()
    raise CliConfigError("평가손익내역 조회는 국내선물옵션(03) 계좌 전용입니다.")


def cmd_settlement(kis: KISClient, args: Namespace) -> Any:
    view = _view(kis)
    if isinstance(view, DomesticDerivativesAccount):
        _reject_foreign_flags(args, allow={"date"})
        if not args.date:
            raise CliConfigError("국내선물옵션 정산손익은 기준일자가 필요합니다(--date YYYYMMDD).")
        return view.settlement_pl(base_date=args.date)
    if isinstance(view, OverseasDerivativesAccount):
        raise CliConfigError("해외선물옵션은 정산손익 조회를 제공하지 않습니다 -- deposit/margin/profits 를 쓰세요.")
    account = _stock_account(kis)
    if args.venue != "overseas":
        raise CliConfigError("정산잔고 조회는 해외주식/국내선물옵션 계좌만 지원합니다.")
    if not args.date:
        raise CliConfigError("해외주식 정산잔고는 기준일자가 필요합니다(--date YYYYMMDD).")
    return account.overseas.settlement_balance(basis_date=args.date)


def cmd_commissions(kis: KISClient, args: Namespace) -> Any:
    view = _view(kis)
    if isinstance(view, DomesticDerivativesAccount):
        _reject_foreign_flags(args, allow={"start", "end"})
        _require_range(args, "약정수수료")
        return view.commissions(start=args.start, end=args.end)
    raise CliConfigError("약정수수료 조회는 국내선물옵션(03) 계좌 전용입니다.")


def cmd_present(kis: KISClient, args: Namespace) -> Any:
    # 해외주식 체결기준현재잔고 전용 -- venue 축이 없다(국내/파생계좌면 아래에서 거부).
    account = _stock_account(kis)
    return account.overseas.present_balance()


def cmd_foreign_margin(kis: KISClient, args: Namespace) -> Any:
    # 해외주식 외화증거금 전용 -- venue 축이 없다(국내/파생계좌면 아래에서 거부).
    account = _stock_account(kis)
    return account.overseas.foreign_margin()
