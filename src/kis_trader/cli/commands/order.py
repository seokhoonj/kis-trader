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

from ..context import account_suffix, resolve_stock
from ..errors import CliAborted, CliConfigError

if TYPE_CHECKING:
    from ...client import KISClient

Side = Literal["buy", "sell"]


def _ticket(args: Namespace, *, side: Side, account: str | None, environment: str) -> dict[str, Any]:
    # 입력을 그대로 되읽는 티켓. 시장가/지정가 같은 주문유형 분류는 CLI 가 만들지 않는다
    # (limit_price 유무는 사용자가 이미 준 값이라 그대로 노출). 환경은 세션(kis)이 이미 해석한 값.
    return {
        "environment": environment,
        "account_suffix": account_suffix(account),
        "venue": args.venue,
        "symbol": args.identifier,
        "side": side,
        "quantity": args.quantity,
        "limit_price": args.limit_price,
        "division": getattr(args, "division", None),
    }


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
    account = kis.account  # 세션 생성 시 한 번 해석된 계좌(자격증명 재조회 없음)
    division = getattr(args, "division", None)
    # division(KRX 주문구분)은 국내 현금 전용 -- 해외 핸들엔 그 파라미터가 없다. fail-closed 로 막는다.
    if division is not None and args.venue == "overseas":
        raise CliConfigError("--division 은 국내(domestic) 현금주문 전용입니다.")
    if args.execute is None:
        return {**_ticket(args, side=side, account=account, environment=kis.environment), "note": _DRY_RUN_NOTE}
    if is_tty is None:
        is_tty = sys.stdin.isatty()
    _authorize(args, account=account, environment=kis.environment, is_tty=is_tty, prompt=prompt)
    handle = resolve_stock(kis, args)
    place = handle.buy if side == "buy" else handle.sell
    extra = {} if args.venue == "overseas" else {"division": division}
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
    _authorize(args, account=kis.account, environment=kis.environment, is_tty=is_tty, prompt=prompt)
    return kis.orders.modify(args.client_order_id, limit_price=args.limit_price, quantity=args.quantity)


def cmd_cancel(kis: KISClient, args: Namespace, *, is_tty: bool | None = None, prompt: Callable[[str], str] = input) -> Any:
    if args.execute is None:
        return {"client_order_id": args.client_order_id, "quantity": args.quantity, "note": _DRY_RUN_NOTE}
    if is_tty is None:
        is_tty = sys.stdin.isatty()
    _authorize(args, account=kis.account, environment=kis.environment, is_tty=is_tty, prompt=prompt)
    return kis.orders.cancel(args.client_order_id, quantity=args.quantity)
