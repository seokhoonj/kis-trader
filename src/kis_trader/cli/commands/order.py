"""주문 명령 -- 매수/매도/reconcile/정정/취소.

안전 모델(안전커널 정신 그대로):
- ``--execute`` 없으면 **dry-run**: 파싱된 주문 티켓만 되읽어 보여주고 아무것도 전송하지 않는다.
  CLI 는 리스크·주문가능성을 스스로 계산하지 않는다(그 판정은 패키지 소유) -- 입력을 그대로
  되비출 뿐이며 "비권위적"임을 명시한다.
- ``--execute {paper,real}`` 는 실행 권한이자 환경 선언이다. 세션 환경(프로필에 저장된 실전/모의)과
  다르면 거부한다(남은 셸 히스토리의 플래그가 다른 환경에서 오작동하지 못하게).
- 확인: 대화형이면 paper 는 y/N, real 은 계좌 끝 4자리 입력. 비대화형이면 ``--yes`` 필수이고
  real 은 ``--confirm-account`` 가 계좌 끝 4자리와 일치해야 한다.
- 타임아웃/결과불명은 재전송하지 않는다 -- ``kis order reconcile`` 만이 사후 진실이다(errors 참조).
"""
from __future__ import annotations

import sys
from argparse import Namespace
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from ..context import account_suffix, resolve_bond, resolve_stock
from ..errors import CliAborted, CliConfigError

if TYPE_CHECKING:
    from ...client import KISClient

Side = Literal["buy", "sell"]

#: 파생 자산군(선물/옵션) -- CLI 라우팅·검증에서 stock/bond 와 구분한다.
_DERIVATIVE_ASSETS = ("futures", "option")


def _ticket(args: Namespace, *, side: Side, account: str | None, environment: str) -> dict[str, Any]:
    # 입력을 그대로 되읽는 티켓. 시장가/지정가 같은 주문유형 분류는 CLI 가 만들지 않는다
    # (limit_price 유무는 사용자가 이미 준 값이라 그대로 노출). 환경은 세션(kis)이 이미 해석한 값.
    asset = args.asset
    ticket: dict[str, Any] = {
        "environment": environment,
        "account_suffix": account_suffix(account),
        "venue": args.venue,
        "symbol": args.identifier,
        "side": side,
        "quantity": args.quantity,
        "limit_price": args.limit_price,
        "asset": asset,
        # 채권은 KRX 주문구분(division)을 쓰지 않는다 -- 항상 None 으로 노출한다.
        "division": None if asset == "bond" else getattr(args, "division", None),
    }
    if asset == "bond" and side == "sell":
        ticket["buy_date"] = getattr(args, "buy_date", None)
        ticket["buy_seq"] = getattr(args, "buy_seq", None)
    if asset in _DERIVATIVE_ASSETS:
        if args.venue == "overseas":
            ticket["stop_price"] = args.stop_price
        else:
            ticket["night"] = args.night
            if asset == "option":
                ticket["right"] = args.right
    return ticket


def _validate_asset_args(args: Namespace, side: Side) -> None:
    """자산별 발주 인자의 CLI 선제 검증 -- 와이어 전에 문제 플래그를 지목해 fail-closed 한다.
    라이브러리도 막지만, CLI 가 먼저 거부해 어떤 플래그가 왜 안 되는지 명확히 알려준다."""
    asset = args.asset
    domestic = args.venue != "overseas"
    is_deriv = asset in _DERIVATIVE_ASSETS

    # 자산에 무의미한 특수 플래그 거부(어느 플래그가 문제인지 지목).
    if (args.buy_date or args.buy_seq) and not (asset == "bond" and side == "sell"):
        raise CliConfigError("--buy-date/--buy-seq 는 채권 매도(--asset bond, sell) 전용입니다.")
    if args.right is not None and not (asset == "option" and domestic):
        raise CliConfigError("--right 는 국내 옵션(--asset option) 전용입니다.")
    if args.night and not (is_deriv and domestic):
        raise CliConfigError("--night 는 국내 파생(--asset futures/option) 전용입니다.")
    if args.stop_price is not None and not (is_deriv and not domestic):
        raise CliConfigError(
            "--stop-price 는 해외 파생(--asset futures/option, --venue overseas) 전용입니다.")
    if args.division is not None:
        if not (asset == "stock" or (is_deriv and domestic)):
            raise CliConfigError("--division 은 국내 현금/파생 주문 전용입니다.")
        if is_deriv and args.division == "priority_limit":
            raise CliConfigError(
                "최우선지정가(priority_limit)는 파생 주문에 없습니다(조건부/최유리지정가만 가능).")

    # 자산별 필수 조건 + 실전전용 게이트.
    if asset == "bond":
        _validate_bond_required(args, side)
    elif is_deriv and not domestic and args.execute == "paper":
        raise CliConfigError("해외 파생은 실전전용입니다(모의투자 미지원) -- --execute paper 불가.")
    if args.night and args.execute == "paper":
        raise CliConfigError("야간 파생(--night)은 실전전용입니다 -- --execute paper 불가.")


def _validate_bond_required(args: Namespace, side: Side) -> None:
    """장내채권 전용 필수 조건 -- 지정가 필수, 국내 전용, 실전전용, 매도는 lot 필수."""
    if args.limit_price is None or not str(args.limit_price).strip():
        raise CliConfigError("장내채권은 지정가 전용입니다 -- --limit-price 가 필요합니다.")
    if args.venue == "overseas" or args.exchange is not None:
        raise CliConfigError("장내채권은 국내 전용입니다(--venue overseas/--exchange 불가).")
    if args.execute == "paper":
        raise CliConfigError("장내채권은 실전전용입니다(모의투자 미지원) -- --execute paper 불가.")
    # 매수에 실린 lot 인자는 상위 _validate_asset_args 의 공용 가드가 먼저 거부하므로
    # 여기서는 매도의 lot 필수만 확인한다.
    if side == "sell" and not (args.buy_date and args.buy_seq):
        raise CliConfigError(
            "채권 매도는 매수 lot(--buy-date/--buy-seq)이 모두 필요합니다 -- "
            "`kis account balance --asset bond` 로 lot 을 확인하세요."
        )


def _authorize(args: Namespace, *, account: str | None, environment: str, is_tty: bool, prompt: Callable[[str], str]) -> None:
    """전송해도 되는지 판정 -- 안 되면 예외. ``--execute`` 가 있을 때만 호출된다. ``environment`` 는
    세션(kis)이 이미 해석한 실전/모의."""
    if args.execute != environment:
        raise CliConfigError(
            f"--execute {args.execute} 가 세션 환경({environment})과 다릅니다 "
            "(두 값이 같아야 전송합니다)."
        )
    suffix = account_suffix(account)
    # fail-closed: 확인할 계좌가 없으면(빈 suffix) real 주문을 막는다. 빈 입력이 빈 suffix 와
    # 우연히 같아져 확인을 통과하는 우회를 원천 차단한다.
    if environment == "real" and not suffix:
        raise CliConfigError(
            "실전 주문에는 계좌번호가 필요합니다(--account 또는 프로필 계좌). "
            "확인할 계좌가 없어 전송하지 않았습니다."
        )
    if is_tty:
        if environment == "real":
            typed = prompt("실전 주문입니다. 확인하려면 계좌 끝 4자리를 입력하세요: ")
            if typed.strip() != suffix:
                raise CliAborted("계좌 확인 실패 -- 전송하지 않았습니다.")
        else:
            answer = prompt("모의(paper) 주문을 전송합니다. 계속하시겠습니까? [y/N]: ")
            if answer.strip().lower() not in ("y", "yes"):
                raise CliAborted("취소 -- 전송하지 않았습니다.")
    else:
        if not args.yes:
            raise CliConfigError("비대화형 환경에서 주문 전송에는 --yes 가 필요합니다.")
        if environment == "real" and (args.confirm_account or "").strip() != suffix:
            raise CliConfigError("real 주문: --confirm-account 가 계좌 끝 4자리와 일치해야 합니다.")


_DRY_RUN_NOTE = (
    "비권위적 사전 점검입니다 -- 서버 접수·체결을 보증하지 않습니다. "
    "실제 전송하려면 --execute <paper|real> 를 주세요."
)


def _preview_or_submit_order(kis: KISClient, args: Namespace, *, side: Side, is_tty: bool | None, prompt: Callable[[str], str]) -> Any:
    account = kis._account  # 세션 생성 시 한 번 해석된 계좌(자격증명 재조회 없음)
    division = getattr(args, "division", None)
    # division(KRX 주문구분)은 국내 현금 전용 -- 해외 핸들엔 그 파라미터가 없다. fail-closed 로 막는다.
    if division is not None and args.venue == "overseas":
        raise CliConfigError("--division 은 국내(domestic) 현금주문 전용입니다.")
    _validate_asset_args(args, side)
    if args.execute is None:
        return {**_ticket(args, side=side, account=account, environment=kis.environment), "note": _DRY_RUN_NOTE}
    if is_tty is None:
        is_tty = sys.stdin.isatty()
    _authorize(args, account=account, environment=kis.environment, is_tty=is_tty, prompt=prompt)
    if args.asset == "bond":
        bond = resolve_bond(kis, args)
        if side == "buy":
            return bond.buy(quantity=args.quantity, limit_price=args.limit_price)
        return bond.sell(quantity=args.quantity, limit_price=args.limit_price,
                         buy_date=args.buy_date, buy_seq=args.buy_seq)
    if args.asset in _DERIVATIVE_ASSETS:
        # venue 별로 전송 kwargs 가 달라(국내 division/night, 해외 stop_price) 각각 인라인 해석한다
        # -- 공용 변수로 묶으면 두 핸들의 buy 시그니처가 달라 정적 타입이 좁혀지지 않는다.
        if args.venue == "overseas":
            ovs = (kis.overseas.option(args.identifier) if args.asset == "option"
                   else kis.overseas.futures(args.identifier))
            place_ovs = ovs.buy if side == "buy" else ovs.sell
            return place_ovs(quantity=args.quantity, limit_price=args.limit_price,
                             stop_price=args.stop_price)
        dom = (kis.domestic.option(args.identifier, right=args.right) if args.asset == "option"
               else kis.domestic.futures(args.identifier))
        place_dom = dom.buy if side == "buy" else dom.sell
        return place_dom(quantity=args.quantity, limit_price=args.limit_price,
                         division=args.division, night=args.night)
    handle = resolve_stock(kis, args)
    place = handle.buy if side == "buy" else handle.sell
    extra: dict[str, Any] = {} if args.venue == "overseas" else {"division": division}
    return place(quantity=args.quantity, limit_price=args.limit_price, **extra)


def cmd_buy(kis: KISClient, args: Namespace, *, is_tty: bool | None = None, prompt: Callable[[str], str] = input) -> Any:
    return _preview_or_submit_order(kis, args, side="buy", is_tty=is_tty, prompt=prompt)


def cmd_sell(kis: KISClient, args: Namespace, *, is_tty: bool | None = None, prompt: Callable[[str], str] = input) -> Any:
    return _preview_or_submit_order(kis, args, side="sell", is_tty=is_tty, prompt=prompt)


def cmd_reconcile(kis: KISClient, args: Namespace) -> Any:
    """접수 여부가 불확실한 주문의 실제 상태를 확정한다. 재전송하지 않는다."""
    return kis.orders.reconcile(args.client_order_id)


def cmd_modify(kis: KISClient, args: Namespace, *, is_tty: bool | None = None, prompt: Callable[[str], str] = input) -> Any:
    if args.execute is None:
        return {
            "client_order_id": args.client_order_id,
            "limit_price": args.limit_price,
            "quantity": args.quantity,
            "note": _DRY_RUN_NOTE,
        }
    if is_tty is None:
        is_tty = sys.stdin.isatty()
    _authorize(args, account=kis._account, environment=kis.environment, is_tty=is_tty, prompt=prompt)
    return kis.orders.modify(args.client_order_id, limit_price=args.limit_price, quantity=args.quantity)


def cmd_cancel(kis: KISClient, args: Namespace, *, is_tty: bool | None = None, prompt: Callable[[str], str] = input) -> Any:
    if args.execute is None:
        return {"client_order_id": args.client_order_id, "quantity": args.quantity, "note": _DRY_RUN_NOTE}
    if is_tty is None:
        is_tty = sys.stdin.isatty()
    _authorize(args, account=kis._account, environment=kis.environment, is_tty=is_tty, prompt=prompt)
    return kis.orders.cancel(args.client_order_id, quantity=args.quantity)
