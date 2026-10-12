"""kis_trader MCP 서버 -- :mod:`kis_trader.mcp.handlers` 를 MCP 도구로 등록해 stdio 로 서빙한다.

``mcp`` 패키지(선택 의존성)를 요구한다: ``pip install 'kis-trader[mcp]'``. 실행은 ``kis-mcp`` 콘솔
스크립트 또는 ``python -m kis_trader.mcp.server``. 자격증명은 **환경변수/프로필에서만** 읽고 절대
출력하지 않는다. 기동 환경은 ``paper`` 기본 -- ``real`` 은 ``KIS_MCP_ENVIRONMENT=real`` 로 명시할 때만.

노출 도구: 읽기(``quote``·``search``·``ranking_change``·``balance``·``positions``·``open_orders``·
``reconcile``) + 런타임 전환(``use_environment``·``use_profile``·``current_account``) + ``order_preview``
(dry-run) + **실주문**(``place_order``·``cancel_order``·``modify_order``). 읽기 도구는 활성
``(profile, environment)`` 슬롯을 호출 시점에 쓴다 -- ``use_profile``/``use_environment`` 로 **재시작 없이**
읽는 계좌/환경을 바꾼다(저장 프로필 기동일 때; 직접-자격증명 기동은 고정 슬롯이라 전환 도구가 거부한다).

**안전 불변**: 런타임 전환은 **읽기 맥락만** 바꾼다. 활성 환경을 real 로 바꿔도 실주문은 열리지 않는다
-- 실주문은 변함없이 **기동 시** 이중게이트(``KIS_MCP_ALLOW_REAL`` + ``KIS_MCP_REAL_CONFIRM``) +
RiskLimits(fail-closed 캡) + 종목 allowlist + **사람 확인(elicitation)** + 세션 서킷브레이커를 전부
통과해야 전송된다. 주문은 또한 활성 프로필이 위탁(general·상품코드 01)일 때만 가능하다(연금/ISA/IRP/해외
계좌는 조회만). 주문 파라미터는 사람이 직접 주고 확인한다(읽기 도구 결과를 인자로 받지 않는다 -- taint 경계).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.transport_security import TransportSecuritySettings

from .. import config
from .._literals import Direction
from ..client import KISClient
from ..errors import KISError, KISUsageError
from ..instrument import Region
from ..order import DomesticDivision, ImmediateOrderFingerprint, Side
from ..risk import RiskLimits
from . import handlers
from ._elicit import SupportsElicit, confirm_order
from ._guardrails import CircuitBreaker, RealOrderGate, StockOrderPlan
from ._http_auth import BearerTokenMiddleware
from ._policy import (
    _ORDER_ENABLED_PROFILES,
    SwitchableProfile,
    _account_masked,
    is_order_eligible_account,
)
from ._session import (
    ClientBuilder,
    McpSession,
    OrderSnapshot,
    _StartupAuthorization,
)

_logger = logging.getLogger("kis_trader.mcp")

#: ``quote`` 의 세션 선택자 -- 정규장(regular)/시간외 단일가(after_hours, 국내 전용).
Session = Literal["regular", "after_hours"]


def _int_env(environ: dict[str, str], name: str, default: int) -> int:
    """정수 환경변수를 읽는다. 값이 없으면 default, 숫자가 아니면 KISUsageError(트레이스백 대신 깔끔히)."""
    raw = environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise KISUsageError(f"{name} 는 정수여야 합니다(받은 값: {raw!r}).") from None


def _access_token(environ: dict[str, str]) -> str | None:
    """``KIS_MCP_ACCESS_TOKEN`` 을 읽어 공백을 떼고 돌려준다(빈 값/공백만이면 None = 토큰 없음).
    ``$(cat tokenfile)`` 로 끌어온 값의 trailing newline 이 비밀의 일부가 되는 사고를 막는다."""
    token = (environ.get("KIS_MCP_ACCESS_TOKEN") or "").strip()
    return token or None


#: 해외 통화별 금액 한도 환경변수 접두어 -- ``KIS_MCP_MAX_ORDER_NOTIONAL_USD`` 처럼 뒤에 통화(ISO)를
#: 붙인다. 국내(KRW) 한도 ``KIS_MCP_MAX_ORDER_NOTIONAL`` 은 이 접두어보다 **짧아**(끝 밑줄 없음)
#: ``startswith`` 에 걸리지 않으므로 통화 맵으로 새지 않는다(경계 안전).
_NOTIONAL_CCY_PREFIX = "KIS_MCP_MAX_ORDER_NOTIONAL_"


def _notional_by_currency_from_env(environ: dict[str, str]) -> dict[str, str]:
    """``KIS_MCP_MAX_ORDER_NOTIONAL_<CCY>`` 들을 ``{통화: 값}`` 으로 모은다(해외 주문 통화별 캡).
    빈 값은 건너뛴다(미설정 취급). 통화 정규화(대문자)·양수·중복/빈 키 검증은 단일 출처인
    :meth:`~kis_trader.risk.RiskLimits.__post_init__` 가 한다(여기선 원 맵만 모은다)."""
    out: dict[str, str] = {}
    for key, value in environ.items():
        if key.startswith(_NOTIONAL_CCY_PREFIX) and value.strip():
            out[key[len(_NOTIONAL_CCY_PREFIX):]] = value
    return out


def _build_risk_from_env(environ: dict[str, str]) -> RiskLimits | None:
    """MCP 캡 환경변수로 RiskLimits 를 만든다(하나도 없으면 None). 실전에서 None 이면 실주문은
    fail-closed 로 거부된다(관례: 캡은 사용자 설정, 미설정=거부)."""
    kwargs: dict[str, Any] = {}
    if environ.get("KIS_MCP_MAX_ORDER_QTY"):
        kwargs["max_order_quantity"] = _int_env(environ, "KIS_MCP_MAX_ORDER_QTY", 0)
    if value := environ.get("KIS_MCP_MAX_ORDER_NOTIONAL"):
        kwargs["max_order_notional"] = value
    if by_ccy := _notional_by_currency_from_env(environ):
        kwargs["max_order_notional_by_currency"] = by_ccy
    if value := environ.get("KIS_MCP_PRICE_COLLAR_PCT"):
        kwargs["price_collar_percent"] = value
    return RiskLimits(**kwargs) if kwargs else None


def _build_allowlist_from_env(environ: dict[str, str]) -> frozenset[str] | None:
    """``KIS_MCP_SYMBOL_ALLOWLIST`` (쉼표 구분)에서 실전 종목 allowlist 를 만든다(비면 None)."""
    raw = environ.get("KIS_MCP_SYMBOL_ALLOWLIST", "")
    symbols = frozenset(s.strip() for s in raw.split(",") if s.strip())
    return symbols or None


def _breaker_from_env(environ: dict[str, str]) -> CircuitBreaker:
    # 환경변수가 '설정됐는가'로 분기한다 -- 값으로 분기하면(falsy) 명시적 0 이 조용히 기본 10 으로
    # 바뀌어 사용자가 둔 가장 엄격한 cap 이 사라진다. 명시한 값은 그대로 넘겨 CircuitBreaker 가
    # 판정하게 한다(0/음수는 거기서 "양의 정수여야 한다"로 fail-closed).
    if "KIS_MCP_MAX_REAL_ORDERS" in environ:
        return CircuitBreaker(max_real_orders=_int_env(environ, "KIS_MCP_MAX_REAL_ORDERS", 0))
    return CircuitBreaker()


_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _resolve_transport(environ: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """``KIS_MCP_TRANSPORT`` 로 전송 방식을 고른다 -- 기본 ``stdio``(Claude Desktop/Cursor). ``sse``/
    ``streamable-http`` 는 네트워크 전송이라 **로컬(127.0.0.1) 바인드만** 허용한다(fail-closed) -- 공개
    노출은 TLS·인증을 맡는 역프록시/터널이 127.0.0.1 로 뜬 서버 앞에 서는 방식으로 한다. 돈이 오가는
    서버라 비-로컬 bind 를 조용히 열어 주지 않는다."""
    transport = environ.get("KIS_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        return "stdio", {}
    if transport not in ("sse", "streamable-http"):
        raise KISUsageError(
            f"KIS_MCP_TRANSPORT 는 stdio/sse/streamable-http 중 하나여야 합니다(받은 값: {transport!r})."
        )
    host = environ.get("KIS_MCP_HOST", "127.0.0.1")
    if host not in _LOCAL_HOSTS and not _access_token(environ):
        raise KISUsageError(
            f"비-로컬 bind({host!r})는 KIS_MCP_ACCESS_TOKEN 을 설정해야 허용됩니다(인증 없는 공개 노출 "
            "방지). 공개 노출은 127.0.0.1 로 띄워 TLS·인증을 맡는 역프록시/터널 뒤에 두는 것을 권장합니다."
        )
    return transport, {"host": host, "port": _int_env(environ, "KIS_MCP_PORT", 8000)}


def build_client() -> KISClient:
    """환경변수/프로필로 KISClient 를 만든다. 기본 paper; real 은 명시 opt-in. 캡(RiskLimits)은
    ``KIS_MCP_MAX_ORDER_*``/``KIS_MCP_PRICE_COLLAR_PCT`` 로."""
    environment = os.environ.get("KIS_MCP_ENVIRONMENT", "paper")
    risk = _build_risk_from_env(dict(os.environ))
    profile = os.environ.get("KIS_MCP_PROFILE")
    if profile:
        return KISClient(profile=profile, environment=environment, risk=risk)  # type: ignore[arg-type]
    missing = [k for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT") if not os.environ.get(k)]
    if missing:
        raise KISUsageError(
            "MCP 서버에 KIS 자격증명이 없습니다. 설정 파일의 env 블록에 KIS_MCP_PROFILE(저장된 프로필 "
            f"이름)을 넣거나 KIS_APP_KEY/KIS_APP_SECRET/KIS_ACCOUNT 를 모두 설정하세요. 없는 값: "
            f"{', '.join(missing)}."
        )
    return KISClient(
        app_key=os.environ["KIS_APP_KEY"],
        app_secret=os.environ["KIS_APP_SECRET"],
        account=os.environ["KIS_ACCOUNT"],
        environment=environment,  # type: ignore[arg-type]
        risk=risk,
    )


def _require_order_profile(snapshot: OrderSnapshot) -> None:
    """주문(place/cancel/modify)은 활성 프로필이 위탁(general) + 상품코드 01 주식 계좌일 때만 연다.
    전환해 연 연금/ISA/IRP/해외 맥락에서는 주문 경로가 열리지 않는다(읽기 전용). 직접-자격증명 기동
    (``profile=None``)은 기동 운영자 슬롯이라 이름 게이트는 건너뛰고 상품코드 01 검사만 한다. 파생
    계좌는 ``kis.account`` 가 StockAccount 가 아니라 여기서 거부된다(방어심층)."""
    profile = snapshot.profile
    if profile is not None and profile not in _ORDER_ENABLED_PROFILES:
        raise KISUsageError(
            f"주문은 general(위탁) 계좌에서만 가능하다 -- 현재 프로필: {profile}. "
            "연금/ISA/IRP/해외 계좌는 MCP 에서 조회만 된다(use_profile('general') 로 바꾸라)."
        )
    account = snapshot.client.account  # 파생 뷰면 StockAccount 가 아니다 -- 아래에서 거부(방어심층)
    if not is_order_eligible_account(account):  # 위탁(상품코드 01) 주식 계좌만 -- 해외-조회 게이트와 공유
        product = getattr(account, "product_code", type(account).__name__)
        raise KISUsageError(
            f"주문은 위탁(상품코드 01) 주식 계좌에서만 가능하다 -- 현재 계좌는 주문 대상이 아니다({product})."
        )


def _execute_stock_order(kis: KISClient, plan: StockOrderPlan) -> Any:
    """검증된 plan 을 라이브러리 buy/sell 로 집행한다(동기). 국내는 종목 기본 거래소, 해외는 plan.exchange
    로 라우팅한다(해외는 지정가 전용이라 limit_price 가 항상 있다)."""
    if plan.region == "overseas":
        handle = kis.overseas.stock(plan.symbol, exchange=plan.exchange)
        method = handle.buy if plan.side == "buy" else handle.sell
        return method(quantity=plan.quantity, limit_price=plan.limit_price)
    domestic_handle = kis.domestic.stock(plan.symbol)
    domestic_method = domestic_handle.buy if plan.side == "buy" else domestic_handle.sell
    if plan.limit_price is not None:
        return domestic_method(quantity=plan.quantity, limit_price=plan.limit_price, division=plan.division)
    return domestic_method(quantity=plan.quantity, division=plan.division)


def _order_ticket(kis: KISClient, **fields: Any) -> dict[str, Any]:
    """elicitation echo + 반환용 티켓(환경·마스킹 계좌 + 주문 필드)."""
    return {"environment": kis.environment, "account": _account_masked(kis), **fields}


def _order_identity(kis: KISClient, client_order_id: str) -> tuple[str, Side] | None:
    """저장소의 원주문 지문에서 (symbol, side) 를 찾는다(없거나 변경지문이면 None). 정정/취소 사람
    확인 티켓이 '어느 종목·매수/매도'인지 보이게 하고, 정정의 allowlist 확인에 쓴다."""
    fingerprint = kis._store.fingerprint_for(client_order_id)
    if isinstance(fingerprint, ImmediateOrderFingerprint):
        return fingerprint.symbol, fingerprint.side
    return None


async def _authorize_order(ctx: SupportsElicit, ticket: dict[str, Any], gate: RealOrderGate,
                           breaker: CircuitBreaker) -> dict[str, Any] | None:
    """실전 주문 사전 승인 -- 사람 확인(elicitation) + 서킷브레이커. 모의는 즉시 승인(돈 안 나감).
    승인되면 ``None``, 거부되면 호출자가 그대로 반환할 refusal dict 를 돌려준다(approved/refusal 이
    모순되게 동시에 존재할 수 없어 호출부의 type:ignore 가 사라진다). 서킷브레이커 HALT 는 (이미
    HALT 든, 이 주문이 한도를 넘겨 trip 하든) **일관되게 refusal** 로 돌려준다(예외로 새지 않음)."""
    if not gate.is_real():
        return None
    halted = {"sent": False, "reason": "서킷브레이커 HALT -- 수동 재개 전까지 실주문 차단", "ticket": ticket}
    if breaker.halted:
        return halted
    if not await confirm_order(ctx, ticket):
        return {"sent": False, "reason": "사람 확인 거부/미지원(elicitation) -- 전송하지 않음",
                "ticket": ticket}
    try:
        breaker.record_and_check()
    except KISUsageError:
        return halted                              # 이 주문이 한도를 넘겨 trip -> 미전송(일관)
    return None


async def run_place_order(
    snapshot: OrderSnapshot, allowlist: frozenset[str] | None, breaker: CircuitBreaker,
    ctx: SupportsElicit, capped_currencies: frozenset[str] = frozenset(), *,
    region: str, symbol: str, side: str, quantity: int,
    limit_price: str | None = None, division: str | None = None, exchange: str | None = None,
) -> dict[str, Any]:
    """place_order 본체(테스트 가능). 주문 프로필 게이트 -> 가드레일 선검증(이중게이트·RiskLimits·
    해외 통화캡·allowlist) -> (실전) 사람 확인 -> 집행. 활성 맥락은 호출부가 뜬 ``snapshot`` 에 얼려 들어
    있어, 집행 중 동시 전환이 대상 계좌/환경을 바꾸지 못한다."""
    _require_order_profile(snapshot)
    kis, gate = snapshot.client, snapshot.gate
    plan = handlers.plan_place_order(
        gate, allowlist, kis.has_risk_limits, capped_currencies, region=region, symbol=symbol,
        side=side, quantity=quantity, limit_price=limit_price, division=division, exchange=exchange,
    )
    ticket = _order_ticket(kis, action="place", region=plan.region, symbol=plan.symbol, side=plan.side,
                           order_type=plan.order_type, quantity=plan.quantity, limit_price=plan.limit_price,
                           division=plan.division,
                           **({"exchange": plan.exchange} if plan.region == "overseas" else {}))
    refusal = await _authorize_order(ctx, ticket, gate, breaker)
    if refusal is not None:
        return refusal
    report = await anyio.to_thread.run_sync(lambda: _execute_stock_order(kis, plan))
    return {"sent": True, "ticket": ticket, "report": handlers._serialize(report)}


async def run_cancel_order(
    snapshot: OrderSnapshot, breaker: CircuitBreaker, ctx: SupportsElicit, *,
    client_order_id: str,
) -> dict[str, Any]:
    _require_order_profile(snapshot)
    kis, gate = snapshot.client, snapshot.gate
    cid = handlers.plan_cancel_order(gate, client_order_id=client_order_id)
    identity = _order_identity(kis, cid)
    ticket = _order_ticket(kis, action="cancel", client_order_id=cid,
                           **({"symbol": identity[0], "side": identity[1]} if identity else {}))
    refusal = await _authorize_order(ctx, ticket, gate, breaker)
    if refusal is not None:
        return refusal
    report = await anyio.to_thread.run_sync(lambda: kis.orders.cancel(cid))
    return {"sent": True, "ticket": ticket, "report": handlers._serialize(report)}


async def run_modify_order(
    snapshot: OrderSnapshot, allowlist: frozenset[str] | None, breaker: CircuitBreaker,
    ctx: SupportsElicit, *, client_order_id: str, limit_price: str, quantity: int | None = None,
) -> dict[str, Any]:
    _require_order_profile(snapshot)
    kis, gate = snapshot.client, snapshot.gate
    identity = _order_identity(kis, client_order_id)
    plan = handlers.plan_modify_order(
        gate, kis.has_risk_limits, allowlist, client_order_id=client_order_id,
        limit_price=limit_price, quantity=quantity,
        symbol=identity[0] if identity else None, side=identity[1] if identity else None,
    )
    ticket = _order_ticket(kis, action="modify", client_order_id=plan.client_order_id,
                           limit_price=plan.limit_price, quantity=plan.quantity,
                           **({"symbol": plan.symbol, "side": plan.side} if plan.symbol else {}))
    refusal = await _authorize_order(ctx, ticket, gate, breaker)
    if refusal is not None:
        return refusal
    report = await anyio.to_thread.run_sync(
        lambda: kis.orders.modify(plan.client_order_id, limit_price=plan.limit_price,
                                  quantity=plan.quantity)
    )
    return {"sent": True, "ticket": ticket, "report": handlers._serialize(report)}


def run_order_preview(
    snapshot: OrderSnapshot, capped_currencies: frozenset[str], *,
    symbol: str, side: Side, quantity: int, limit_price: str | None = None,
    division: DomesticDivision | None = None, region: Region = "domestic",
    exchange: str | None = None,
) -> dict[str, Any]:
    """order_preview 본체(테스트 가능). place_order 와 같은 주문 프로필 게이트(general + 상품코드 01)를
    먼저 적용해 -- place_order 라면 프로필 게이트에서 거부됐을 맥락(연금/ISA/IRP/해외/파생)에서는 미리보기도
    성공하지 못하게 한다(preview 의 'place 와 같은 검증' 약속 준수). 전송은 하지 않는다(``sent: false``)."""
    _require_order_profile(snapshot)
    return handlers.order_preview(
        snapshot.client, capped_currencies, symbol=symbol, side=side, quantity=quantity,
        limit_price=limit_price, division=division, region=region, exchange=exchange,
    )


def build_server(
    account_session: McpSession, *, allowlist: frozenset[str] | None, breaker: CircuitBreaker,
    capped_currencies: frozenset[str],
) -> MCPServer:
    """주어진 :class:`~kis_trader.mcp._session.McpSession` 과 가드레일을 소비하는 MCP 서버를 만들어
    도구를 등록한다. 읽기 도구는 호출 시점에 활성 슬롯(``account_session.read_client()``)을 쓰고, 주문
    도구는 await 이전에 ``account_session.order_snapshot()`` 으로 활성 맥락을 한 번 얼려 든다(split-brain
    가드가 그 스냅샷 생성 시점에 환경 일치를 재단언한다). ``capped_currencies`` 는 해외 실전 주문의
    통화별 금액 한도 집합(:class:`~kis_trader.risk.RiskLimits` 에 설정된 통화들)으로, 해외 실전 주문/
    미리보기의 선거부에 쓴다. (내부 변수명은 ``account_session`` -- ``quote`` 도구가 노출하는 ``session``
    파라미터와 이름이 겹치지 않도록.)"""
    server = MCPServer(
        "kis-trader",
        instructions=(
            "한국투자증권 계좌 조회·주문 미리보기·실주문 도구. use_profile/use_environment 로 **읽는**"
            " 계좌/환경을 재시작 없이 바꾼다 -- 이는 조회 맥락만 바꾼다. 실전으로 바꿔도 실주문은 열리지"
            " 않는다: 실주문(place/cancel/modify_order)은 변함없이 기동 시 이중게이트+RiskLimits+allowlist"
            "+사람확인(elicitation)을 전부 통과해야 하고, 활성 프로필이 general(위탁)일 때만 가능하다."
            " 주문 파라미터(종목/방향/수량/가격)는 사람이 직접 주고 확인한다 -- 조회 결과를 그대로 주문에"
            " 넣지 말 것. 도구가 준 숫자를 재계산하지 말 것."
        ),
    )

    @server.tool()
    def quote(symbol: str, region: Region = "domestic", session: Session = "regular") -> dict[str, Any]:
        """종목 현재가 스냅샷. region='overseas' 면 해외. session='after_hours' 면 국내 시간외 단일가
        (정규장 마감 후 시세는 regular 가 아니라 after_hours 를 쓸 것; 시간외는 국내 전용)."""
        return handlers.quote(account_session.read_client(), symbol, region=region, session=session)

    @server.tool()
    def search(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """이름/질의로 국내 종목 코드 후보를 찾는다."""
        # MCP 툴명은 `search` 로 고정한다(에이전트 설정 안정성). 라이브러리 메서드가
        # `search_stocks()`(해외 형제와 구별)인 것과 의도적으로 다르다 -- flat 한 MCP 네임스페이스엔
        # 구별할 형제가 없어 `search` 가 이 경계의 올바른 이름이다. 통일하려고 바꾸지 말 것.
        return handlers.search(account_session.read_client(), query, limit=limit)

    @server.tool()
    def ranking_change(direction: Direction = "gainers", limit: int = 10) -> list[dict[str, Any]]:
        """국내 등락률 순위(gainers/losers)."""
        return handlers.ranking_change(account_session.read_client(), direction=direction, limit=limit)

    @server.tool()
    def balance(region: Region = "domestic", market: str | None = None) -> Any:
        """계좌 잔고 요약. region='domestic' 은 활성 계좌 종류로 분기(위탁/연금저축=국내주식, IRP=퇴직연금).
        region='overseas' 는 해외주식 잔고(general·실전 전용; market 생략 시 시장별 요약 리스트, 반환 shape 상이)."""
        return handlers.balance(account_session.read_client(), region=region, market=market,
                                active_profile=account_session.active_profile)

    @server.tool()
    def positions(region: Region = "domestic", market: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """보유 종목. region='domestic' 은 활성 계좌 종류로 분기, region='overseas' 는 해외주식 보유
        (general·실전 전용; market 생략 시 전체 시장 합산)."""
        return handlers.positions(account_session.read_client(), region=region, market=market, limit=limit,
                                  active_profile=account_session.active_profile)

    @server.tool()
    def open_orders(limit: int = 100) -> list[dict[str, Any]]:
        """미체결 주문(국내 위탁 주식 계좌 전용)."""
        return handlers.open_orders(account_session.read_client(), limit=limit)

    @server.tool()
    def reconcile(client_order_id: str) -> dict[str, Any]:
        """결과 불명 주문의 사후 확정(활성 슬롯의 주문 저장소 기준)."""
        return handlers.reconcile(account_session.read_client(), client_order_id=client_order_id)

    @server.tool()
    def use_environment(environment: Literal["real", "paper"]) -> dict[str, Any]:
        """**읽는** 환경만 바꾼다(real/paper). 이는 조회 맥락만 바꾼다 -- 실전으로 바꿔도 실주문은 열리지
        않는다(실주문 언락은 기동 환경변수에만 묶인다). 저장 프로필 기동일 때만 가능."""
        return account_session.use_environment(environment)

    @server.tool()
    def use_profile(profile: SwitchableProfile) -> dict[str, Any]:
        """**읽는** 계좌 종류만 바꾼다. {general, isa, pension, irp} 로 제한(파생은 선택 불가). 전환은
        조회 맥락만 바꾸며, 주문은 여전히 general(위탁)에서만 가능하다. 저장 프로필 기동일 때만 가능."""
        return account_session.use_profile(profile)

    @server.tool()
    def current_account() -> dict[str, Any]:
        """지금 보는 중인 활성 (profile, environment) + 마스킹 계좌 + 계좌 종류."""
        return account_session.current_account()

    @server.tool()
    def order_preview(
        symbol: str, side: Side, quantity: int, limit_price: str | None = None,
        division: DomesticDivision | None = None, region: Region = "domestic",
        exchange: str | None = None,
    ) -> dict[str, Any]:
        """주문 미리보기(dry-run) -- 전송하지 않고 나갈 티켓만 보여준다(place_order 와 같은 주문 프로필
        게이트 + 형상 검증 + 해외 실전 통화캡 선검사를 거친다). 해외(region='overseas')는 지정가 전용·
        exchange 필수. 활성 프로필이 general(위탁·상품코드 01)이 아니면 place_order 와 똑같이 거부한다."""
        snapshot = account_session.order_snapshot()
        return run_order_preview(
            snapshot, capped_currencies, symbol=symbol, side=side, quantity=quantity,
            limit_price=limit_price, division=division, region=region, exchange=exchange,
        )

    @server.tool()
    async def place_order(
        ctx: Context, *, symbol: str, side: Side, quantity: int, limit_price: str | None = None,
        division: DomesticDivision | None = None, region: Region = "domestic",
        exchange: str | None = None,
    ) -> dict[str, Any]:
        """주식 실주문(매수/매도). 모의 기본. 실전은 이중게이트+RiskLimits+allowlist+사람확인을 전부
        통과해야 전송되고, 활성 프로필이 general(위탁·상품코드 01)일 때만 가능하다. region='overseas' 는
        해외주식 지정가 주문(``exchange`` 필수 NAS/NYS/..., ``division`` 불가, 통화별 금액캡 필수).
        ``division`` 은 국내 주문구분(예: 최유리지정가 ``immediate_limit``). 파라미터는 사람이 직접 준다
        (읽기결과 금지). 활성 맥락은 호출 즉시 스냅샷으로 얼려, 집행 중 전환이 대상을 바꾸지 못한다."""
        snapshot = account_session.order_snapshot()
        return await run_place_order(snapshot, allowlist, breaker, ctx, capped_currencies,
                                     region=region, symbol=symbol, side=side, quantity=quantity,
                                     limit_price=limit_price, division=division, exchange=exchange)

    @server.tool()
    async def cancel_order(ctx: Context, client_order_id: str) -> dict[str, Any]:
        """접수된 주문 취소(client_order_id 지목). 실전은 사람 확인 필요. general(위탁) 계좌에서만."""
        snapshot = account_session.order_snapshot()
        return await run_cancel_order(snapshot, breaker, ctx, client_order_id=client_order_id)

    @server.tool()
    async def modify_order(
        ctx: Context, client_order_id: str, limit_price: str, quantity: int | None = None
    ) -> dict[str, Any]:
        """접수된 주문 가격/수량 정정. 실전은 이중게이트+RiskLimits+(원주문 종목의)allowlist+사람 확인+
        서킷브레이커. general(위탁) 계좌에서만."""
        snapshot = account_session.order_snapshot()
        return await run_modify_order(snapshot, allowlist, breaker, ctx, client_order_id=client_order_id,
                                      limit_price=limit_price, quantity=quantity)

    return server


def _client_for_slot(risk: RiskLimits | None) -> ClientBuilder:
    """``(profile, environment)`` -> KISClient 팩토리 -- 전환 시 새 슬롯을 같은 risk(통화별 캡 포함)로
    연다. ``KISClient(profile=, environment=)`` 가 저장된 자격증명에서 그 슬롯을 열며, 없는 조합은 즉시
    fail-closed 로 거부한다(KISUsageError)."""
    def build(profile: str, environment: str) -> KISClient:
        return KISClient(profile=profile, environment=environment, risk=risk)  # type: ignore[arg-type]
    return build


def _build_session(environ: dict[str, str], environment: str, risk: RiskLimits | None) -> McpSession:
    """기동 슬롯을 열고(실패 시 깔끔한 KISUsageError) :class:`McpSession` 을 만든다. ``KIS_MCP_PROFILE``
    이 있으면 저장 프로필 기동(런타임 전환 가능), 없으면 직접-자격증명 고정 슬롯(전환 도구 거부)이다.
    실주문 언락 비트는 여기서 한 번만 얼린다(:meth:`_StartupAuthorization.from_env`)."""
    auth = _StartupAuthorization.from_env(environ)
    profile = environ.get("KIS_MCP_PROFILE")
    if profile:
        if config.is_legacy_store():
            _logger.warning(
                "credentials store has no schema marker (legacy): runtime account switching "
                "(use_environment/use_profile) is only meaningful on a schema-2 store, and a "
                "switch to the wrong environment may fail only at the wire. Run 'kis config "
                "migrate' to convert the store."
            )
        build = _client_for_slot(risk)
        startup_client = build(profile, environment)   # eager -- 잘못된 조합은 여기서 fail
        return McpSession(profile=profile, environment=environment, startup_client=startup_client,
                          auth=auth, build=build)
    startup_client = build_client()                     # 직접-자격증명(없으면 깔끔한 안내 에러)
    return McpSession(profile=None, environment=environment, startup_client=startup_client,
                      auth=auth, build=None)


def main() -> None:
    """MCP 서버 실행 진입점(콘솔 스크립트 ``kis-mcp``). 기본 stdio; ``KIS_MCP_TRANSPORT`` 로 sse/
    streamable-http(로컬 바인드) 선택."""
    environment = os.environ.get("KIS_MCP_ENVIRONMENT", "paper")
    try:
        environ = dict(os.environ)
        risk = _build_risk_from_env(environ)
        session = _build_session(environ, environment, risk)
        allowlist = _build_allowlist_from_env(environ)
        breaker = _breaker_from_env(environ)
        capped_currencies = (
            frozenset(risk.max_order_notional_by_currency or {}) if risk is not None else frozenset()
        )
        transport, run_kwargs = _resolve_transport(environ)
        server = build_server(session, allowlist=allowlist, breaker=breaker,
                              capped_currencies=capped_currencies)
        if transport == "stdio":
            server.run("stdio")
        else:
            _serve_http(server, transport, _access_token(environ), **run_kwargs)
    except KISError as exc:
        # 설정 오류는 트레이스백 대신 한 줄로. stderr 는 Claude Desktop 의 mcp 로그에 남는다.
        print(f"kis-mcp: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def _serve_http(server: MCPServer, transport: str, token: str | None, *, host: str, port: int) -> None:
    """HTTP 전송 실행. 토큰이 없으면 localhost 전용으로 SDK 의 run 을 그대로, 있으면 ASGI 앱에 bearer
    게이트를 씌워 uvicorn 으로 띄운다(비-로컬 bind 허용)."""
    if not token:
        if transport == "sse":
            server.run("sse", host=host, port=port)
        else:
            server.run("streamable-http", host=host, port=port)
        return
    try:
        import uvicorn
    except ImportError:
        raise KISUsageError("HTTP 전송에는 uvicorn 이 필요합니다: pip install 'kis-trader[mcp]'.") from None
    # host 를 앱 빌더에 넘겨야 SDK 의 Host 검증이 실제 bind 와 맞는다(안 넘기면 기본 127.0.0.1 로 고정돼
    # 원격 클라이언트가 421). stateless_http 는 기본 False 유지 -- 실주문 사람확인(elicitation) 백채널이 그
    # 세션 스트림에 실리기 때문(부수적 load-bearing).
    ts = _transport_security_for(host, dict(os.environ))
    if transport == "streamable-http":
        app = server.streamable_http_app(host=host, transport_security=ts)
    else:
        app = server.sse_app(host=host, transport_security=ts)
    app.add_middleware(BearerTokenMiddleware, token=token)
    uvicorn.run(app, host=host, port=port, log_level="info")


def _transport_security_for(host: str, environ: dict[str, str]) -> TransportSecuritySettings | None:
    """로컬 bind 는 None(SDK 가 localhost 전용 DNS-리바인딩 보호를 자동 적용). 비-로컬 bind 는
    ``KIS_MCP_ALLOWED_HOSTS`` 가 있으면 그 호스트로 보호를 켜고(원하면 ``KIS_MCP_ALLOWED_ORIGINS`` 도),
    없으면 보호를 끈다 -- 프록시/터널이 Host 를 전달하는 공개 배치를 전제하며, 그때 실질 게이트는 bearer
    토큰이다."""
    if host in _LOCAL_HOSTS:
        return None
    hosts = [h.strip() for h in environ.get("KIS_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    origins = [o.strip() for o in environ.get("KIS_MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if hosts:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


if __name__ == "__main__":
    main()
