"""세션 조립 -- 프로필과 플래그를 :class:`~kis_trader.client.KISClient` 로 해석.

자격증명은 :func:`~kis_trader.config.resolve_credentials` 가 프로필별로 해석한다(환경변수 ->
``credentials.json``). 자격증명을 플래그로 받지 않는 이유: 셸 히스토리·``ps``
출력도 노출 경로이기 때문이다. 값은 어디에도 echo 하지 않는다(누락 여부만 확인).
"""
from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING, cast, get_args

from ..client import KISClient
from ..config import order_store_path, resolve_credentials
from ..errors import KISUsageError
from ..instrument import DomesticOrderExchange, DomesticQuoteExchange, quote_exchange_for_order
from ..store import OrderStore
from .errors import CliConfigError

# CLI 는 소비자층이라 패키지 비공개(frozenset)에 손대지 않고 공개 Literal 에서 멤버십 집합을 파생한다
# (명령들이 공유). 패키지 내부 _DOMESTIC_*_EXCHANGES 와 같은 Literal 을 단일 출처로 쓴다.
_DOMESTIC_ORDER_EXCHANGES = frozenset(get_args(DomesticOrderExchange))
_DOMESTIC_QUOTE_EXCHANGES = frozenset(get_args(DomesticQuoteExchange))

if TYPE_CHECKING:
    from ..domestic.bond import Bond
    from ..domestic.stock import DomesticStock
    from ..overseas.stock import OverseasStock


def account_suffix(account: str | None) -> str:
    """계좌 마지막 4자리(하이픈 제거 후) -- real 주문 확인용. 마스킹된 식별자라 노출해도 안전하다."""
    if not account:
        return ""
    digits = account.replace("-", "")
    return digits[-4:]


def resolve_stock(kis: KISClient, args: Namespace) -> DomesticStock | OverseasStock:
    """``--region`` 으로 국내/해외 종목 핸들을 만든다(해외는 ``--exchange``, 생략 시 자동 판별).
    반환은 DomesticStock 또는 OverseasStock -- 둘의 공통 시세·주문 메서드를 CLI 가 쓴다."""
    if args.region == "overseas":
        return kis.overseas.stock(args.identifier, exchange=args.exchange)
    exchange = getattr(args, "exchange", None)
    if getattr(args, "group", None) == "order":
        if exchange is not None and exchange not in _DOMESTIC_ORDER_EXCHANGES:
            raise CliConfigError(
                f"지원하지 않는 국내 주문 거래소: {exchange!r} (KRX/NXT/SOR)."
            )
        # 주문 거래소 인자로 시세 핸들을 만든다. SOR->UN 대응은 패키지(instrument)가 소유하고
        # CLI 는 호출만 한다 -- 역방향 분류표를 CLI 가 들면 거래소 추가 시 양쪽이 드리프트한다.
        handle_exchange = (
            quote_exchange_for_order(cast(DomesticOrderExchange, exchange))
            if exchange is not None
            else None
        )
    else:
        if exchange is not None and exchange not in _DOMESTIC_QUOTE_EXCHANGES:
            raise CliConfigError(
                f"지원하지 않는 국내 시세 거래소: {exchange!r} (KRX/NXT/UN)."
            )
        handle_exchange = exchange
    return kis.domestic.stock(
        args.identifier,
        exchange=handle_exchange,
    )


def resolve_bond(kis: KISClient, args: Namespace) -> Bond:
    """장내채권 종목 핸들을 만든다(국내 전용). 채권은 지역/거래소 축이 없다."""
    return kis.domestic.bond(args.identifier)


def _is_read_only_handler(args: Namespace) -> bool:
    """검토한 조회 함수만 메모리 세션을 쓴다. 새 함수·누락·동명 함수는 영속 경로를 유지한다."""
    # stock 명령은 이 모듈의 resolve_stock 을 쓰므로 순환 import 를 피한다.
    from .commands import account, index, market, stock

    handler = getattr(args, "func", None)
    return any(handler is allowed for allowed in (
        stock.cmd_quote, stock.cmd_bars, stock.cmd_book, stock.cmd_trades,
        stock.cmd_status, stock.cmd_investors,
        index.cmd_quote, index.cmd_bars,
        market.cmd_search, market.cmd_ranking_change, market.cmd_ranking_volume,
        market.cmd_ranking_market_cap, market.cmd_market_investors, market.cmd_market_net_buy,
        account.cmd_balance, account.cmd_positions, account.cmd_buyable, account.cmd_orders,
        account.cmd_fills, account.cmd_reserved, account.cmd_profits, account.cmd_transactions,
        account.cmd_deposit, account.cmd_margin, account.cmd_valuation, account.cmd_settlement,
        account.cmd_commissions, account.cmd_present, account.cmd_foreign_margin,
    ))


def build_client(args: Namespace) -> KISClient:
    """프로필로 세션을 연다. 자격증명이 없거나 형상 오류면 :class:`CliConfigError`(종료 코드 3).

    ``--profile`` 이 어느 자격증명 묶음(계좌 종류)을, ``--environment`` 이 어느 환경(실전/모의)
    슬롯을 쓸지 정한다 -- 둘이 짝지어 ``(environment, profile)`` 묶음을 고른다. ``--environment``
    을 생략하면 해석 체인(``KIS_DEFAULT_ENVIRONMENT`` > ``default_environment`` 마커 > ``paper``)
    으로 정한다. ``--account`` 플래그가 있으면 프로필이 해석한 계좌 대신 그것을 쓴다.

    라이브러리 기본 :class:`~kis_trader.store.OrderStore` 는 인메모리라 프로세스가 끝나면 dedup 이
    사라진다. CLI 는 호출마다 새 프로세스라 이전 호출이 낸 주문을 취소·재조회·dedup 하려면 상태가
    디스크에 남아야 한다 -- 계좌·환경별 영속 저장소(:func:`~kis_trader.config.order_store_path`)를
    주입한다. 명시적으로 검토한 조회 함수는 주문 저널을 열지 않고 주문불가 메모리 세션을 쓴다.
    계좌가 없는(시세 전용) 세션도 영속 저장소를 만들지 않는다. 미지 함수는 영속 경로를 유지한다."""
    account = getattr(args, "account", None) or None
    environment = getattr(args, "environment", None)
    read_only = _is_read_only_handler(args)
    try:
        # 자격증명은 한 번만 해석하고 저장소와 세션에 같은 값을 전달한다.
        # 자격증명 누락/형상 오류는 여기서 KISUsageError 로 난다.
        credentials = resolve_credentials(args.profile, environment=environment)
        store_account = account or credentials.account
        store = (
            OrderStore(path=order_store_path(account=store_account, environment=credentials.environment))
            if store_account is not None and not read_only else None
        )
        try:
            return KISClient(
                profile=args.profile, app_key=credentials.app_key, app_secret=credentials.app_secret,
                account=store_account, environment=credentials.environment, hts_id=credentials.hts_id,
                account_password=credentials.account_password, store=store,
                orderable=not read_only,
            )
        except BaseException:
            if store is not None:
                store.close()
            raise
    except KISUsageError as err:
        # 자격증명 누락/형상 오류(변수 이름만 담김) -> CLI 설정 오류로 번역(값은 노출 안 됨).
        raise CliConfigError(str(err)) from err
