"""kis_trader MCP 서버 -- :mod:`kis_trader.mcp.handlers` 를 MCP 도구로 등록해 stdio 로 서빙한다.

``mcp`` 패키지(선택 의존성)를 요구한다: ``pip install 'kis-trader[mcp]'``. 실행은 ``kis-mcp`` 콘솔
스크립트 또는 ``python -m kis_trader.mcp.server``. 자격증명은 **환경변수/프로필에서만** 읽고 절대
출력하지 않는다. 기본 환경은 ``paper`` -- ``real`` 은 ``KIS_MCP_ENVIRONMENT=real`` 로 명시할 때만.

노출 도구: 읽기(``quote``·``search``·``ranking_change``·``balance``·``positions``·``open_orders``) +
``order_preview``(dry-run) + ``reconcile`` + **실주문**(``place_order``·``cancel_order``·``modify_order``).
실주문은 **모의 기본**이고, 실전은 이중게이트(``KIS_MCP_ALLOW_REAL`` + ``KIS_MCP_REAL_CONFIRM``) +
RiskLimits(fail-closed 캡) + 종목 allowlist + **사람 확인(elicitation)** + 세션 서킷브레이커를 전부
통과해야 전송된다. 주문 파라미터는 사람이 직접 주고 확인한다(읽기 도구 결과를 인자로 받지 않는다 -- taint 경계).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from ..client import KISClient
from ..errors import KISUsageError
from ..risk import RiskLimits
from . import handlers
from ._elicit import SupportsElicit, confirm_order
from ._guardrails import CircuitBreaker, RealOrderGate, StockOrderPlan


@dataclass(frozen=True, slots=True)
class _OrderDecision:
    """실주문 사전 승인 결과 -- ``approved`` 면 집행, 아니면 ``refusal`` dict 를 그대로 반환(미전송)."""

    approved: bool
    refusal: dict[str, Any] | None


def _build_risk_from_env(environ: dict[str, str]) -> RiskLimits | None:
    """MCP 캡 환경변수로 RiskLimits 를 만든다(하나도 없으면 None). 실전에서 None 이면 실주문은
    fail-closed 로 거부된다(관례: 캡은 사용자 설정, 미설정=거부)."""
    kwargs: dict[str, Any] = {}
    if value := environ.get("KIS_MCP_MAX_ORDER_QTY"):
        kwargs["max_order_quantity"] = int(value)
    if value := environ.get("KIS_MCP_MAX_ORDER_NOTIONAL"):
        kwargs["max_order_notional"] = value
    if value := environ.get("KIS_MCP_PRICE_COLLAR_PCT"):
        kwargs["price_collar_percent"] = value
    return RiskLimits(**kwargs) if kwargs else None


def _build_allowlist_from_env(environ: dict[str, str]) -> frozenset[str] | None:
    """``KIS_MCP_SYMBOL_ALLOWLIST`` (쉼표 구분)에서 실전 종목 allowlist 를 만든다(비면 None)."""
    raw = environ.get("KIS_MCP_SYMBOL_ALLOWLIST", "")
    symbols = frozenset(s.strip() for s in raw.split(",") if s.strip())
    return symbols or None


def _breaker_from_env(environ: dict[str, str]) -> CircuitBreaker:
    raw = environ.get("KIS_MCP_MAX_REAL_ORDERS")
    return CircuitBreaker(max_real_orders=int(raw)) if raw else CircuitBreaker()


def build_client() -> KISClient:
    """환경변수/프로필로 KISClient 를 만든다. 기본 paper; real 은 명시 opt-in. 캡(RiskLimits)은
    ``KIS_MCP_MAX_ORDER_*``/``KIS_MCP_PRICE_COLLAR_PCT`` 로."""
    environment = os.environ.get("KIS_MCP_ENVIRONMENT", "paper")
    risk = _build_risk_from_env(dict(os.environ))
    profile = os.environ.get("KIS_MCP_PROFILE")
    if profile:
        return KISClient(profile=profile, environment=environment, risk=risk)  # type: ignore[arg-type]
    return KISClient(
        app_key=os.environ["KIS_APP_KEY"],
        app_secret=os.environ["KIS_APP_SECRET"],
        account=os.environ["KIS_ACCOUNT"],
        environment=environment,  # type: ignore[arg-type]
        risk=risk,
    )


def _execute_stock_order(kis: KISClient, plan: StockOrderPlan) -> Any:
    """검증된 plan 을 라이브러리 buy/sell 로 집행한다(동기). MVP 는 국내 주식만(plan.venue=domestic)."""
    handle = kis.domestic.stock(plan.symbol)
    method = handle.buy if plan.side == "buy" else handle.sell
    if plan.limit_price is not None:
        return method(quantity=plan.quantity, limit_price=plan.limit_price)
    return method(quantity=plan.quantity)


def _order_ticket(kis: KISClient, **fields: Any) -> dict[str, Any]:
    """elicitation echo + 반환용 티켓(환경·마스킹 계좌 + 주문 필드)."""
    return {"environment": kis.environment, "account": handlers._account_masked(kis), **fields}


async def _authorize_order(ctx: SupportsElicit, ticket: dict[str, Any], gate: RealOrderGate,
                           breaker: CircuitBreaker) -> _OrderDecision:
    """실전 주문 사전 승인 -- 사람 확인(elicitation) + 서킷브레이커. 모의는 즉시 승인(돈 안 나감).
    서킷브레이커 HALT 는 (이미 HALT 든, 이 주문이 한도를 넘겨 trip 하든) **일관되게 refusal** 로 돌려
    준다(예외로 새지 않음)."""
    if not gate.is_real():
        return _OrderDecision(approved=True, refusal=None)
    halted = {"sent": False, "reason": "서킷브레이커 HALT -- 수동 재개 전까지 실주문 차단", "ticket": ticket}
    if breaker.halted:
        return _OrderDecision(False, halted)
    if not await confirm_order(ctx, ticket):
        return _OrderDecision(
            False, {"sent": False, "reason": "사람 확인 거부/미지원(elicitation) -- 전송하지 않음",
                    "ticket": ticket})
    try:
        breaker.record_and_check()
    except KISUsageError:
        return _OrderDecision(False, halted)       # 이 주문이 한도를 넘겨 trip -> 미전송(일관)
    return _OrderDecision(True, None)


async def run_place_order(
    kis: KISClient, gate: RealOrderGate, allowlist: frozenset[str] | None, breaker: CircuitBreaker,
    ctx: SupportsElicit, *, venue: str, symbol: str, side: str, quantity: int,
    limit_price: str | None = None,
) -> dict[str, Any]:
    """place_order 본체(테스트 가능). 가드레일 선검증 -> (실전) 사람 확인 -> 집행. MVP 는 국내 주식만."""
    plan = handlers.plan_place_order(
        gate, allowlist, kis.has_risk_limits, venue=venue, symbol=symbol, side=side,
        quantity=quantity, limit_price=limit_price,
    )
    ticket = _order_ticket(kis, action="place", venue=plan.venue, symbol=plan.symbol, side=plan.side,
                           order_type=plan.order_type, quantity=plan.quantity, limit_price=plan.limit_price)
    decision = await _authorize_order(ctx, ticket, gate, breaker)
    if not decision.approved:
        return decision.refusal                        # type: ignore[return-value]  # approved=False -> refusal 존재
    report = await anyio.to_thread.run_sync(lambda: _execute_stock_order(kis, plan))
    return {"sent": True, "ticket": ticket, "report": handlers._serialize(report)}


async def run_cancel_order(
    kis: KISClient, gate: RealOrderGate, breaker: CircuitBreaker, ctx: SupportsElicit, *,
    client_order_id: str,
) -> dict[str, Any]:
    cid = handlers.plan_cancel_order(gate, client_order_id=client_order_id)
    ticket = _order_ticket(kis, action="cancel", client_order_id=cid)
    decision = await _authorize_order(ctx, ticket, gate, breaker)
    if not decision.approved:
        return decision.refusal                        # type: ignore[return-value]
    report = await anyio.to_thread.run_sync(lambda: kis.orders.cancel(cid))
    return {"sent": True, "ticket": ticket, "report": handlers._serialize(report)}


async def run_modify_order(
    kis: KISClient, gate: RealOrderGate, breaker: CircuitBreaker, ctx: SupportsElicit, *,
    client_order_id: str, limit_price: str, quantity: int | None = None,
) -> dict[str, Any]:
    plan = handlers.plan_modify_order(gate, kis.has_risk_limits, client_order_id=client_order_id,
                                      limit_price=limit_price, quantity=quantity)
    ticket = _order_ticket(kis, action="modify", **plan)
    decision = await _authorize_order(ctx, ticket, gate, breaker)
    if not decision.approved:
        return decision.refusal                        # type: ignore[return-value]
    report = await anyio.to_thread.run_sync(
        lambda: kis.orders.modify(plan["client_order_id"], limit_price=plan["limit_price"],
                                  quantity=plan["quantity"])
    )
    return {"sent": True, "ticket": ticket, "report": handlers._serialize(report)}


def build_server(
    kis: KISClient, *, gate: RealOrderGate, allowlist: frozenset[str] | None, breaker: CircuitBreaker
) -> Any:
    """주어진 KISClient·가드레일을 소비하는 MCP 서버(MCPServer)를 만들어 도구를 등록한다."""
    server = MCPServer(
        "kis-trader",
        instructions=(
            "한국투자증권 계좌 조회·주문 미리보기·실주문 도구. 실주문(place/cancel/modify_order)은 모의"
            " 기본이고 실전은 이중게이트+RiskLimits+allowlist+사람확인(elicitation)을 전부 통과해야 전송된다."
            " 주문 파라미터(종목/방향/수량/가격)는 사람이 직접 주고 확인한다 -- 조회 결과를 그대로 주문에"
            " 넣지 말 것. 도구가 준 숫자를 재계산하지 말 것."
        ),
    )

    @server.tool()
    def quote(symbol: str, market: str = "domestic") -> dict[str, Any]:
        """종목 현재가 스냅샷. market='overseas' 면 해외."""
        return handlers.quote(kis, symbol, market=market)

    @server.tool()
    def search(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """이름/질의로 국내 종목 코드 후보를 찾는다."""
        return handlers.search(kis, query, limit=limit)

    @server.tool()
    def ranking_change(direction: str = "gainers", limit: int = 10) -> list[dict[str, Any]]:
        """국내 등락률 순위(gainers/losers)."""
        return handlers.ranking_change(kis, direction=direction, limit=limit)

    @server.tool()
    def balance() -> dict[str, Any]:
        """계좌 잔고 요약."""
        return handlers.balance(kis)

    @server.tool()
    def positions(limit: int = 100) -> list[dict[str, Any]]:
        """보유 종목."""
        return handlers.positions(kis, limit=limit)

    @server.tool()
    def open_orders(limit: int = 100) -> list[dict[str, Any]]:
        """미체결 주문."""
        return handlers.open_orders(kis, limit=limit)

    @server.tool()
    def order_preview(
        symbol: str, side: str, quantity: int,
        limit_price: str | None = None, division: str | None = None,
    ) -> dict[str, Any]:
        """주문 미리보기(dry-run) -- 전송하지 않고 나갈 티켓만 보여준다."""
        return handlers.order_preview(
            kis, symbol=symbol, side=side, quantity=quantity,
            limit_price=limit_price, division=division,
        )

    @server.tool()
    def reconcile(client_order_id: str) -> dict[str, Any]:
        """결과 불명 주문의 사후 확정."""
        return handlers.reconcile(kis, client_order_id=client_order_id)

    @server.tool()
    async def place_order(
        ctx: Context, venue: str, symbol: str, side: str, quantity: int,
        limit_price: str | None = None,
    ) -> dict[str, Any]:
        """국내 주식 실주문(매수/매도). 모의 기본. 실전은 이중게이트+RiskLimits+allowlist+사람확인을 전부
        통과해야 전송. venue='domestic'(해외는 아직 미지원). 파라미터는 사람이 직접 준다(읽기결과 금지)."""
        return await run_place_order(kis, gate, allowlist, breaker, ctx, venue=venue, symbol=symbol,
                                     side=side, quantity=quantity, limit_price=limit_price)

    @server.tool()
    async def cancel_order(ctx: Context, client_order_id: str) -> dict[str, Any]:
        """접수된 주문 취소(client_order_id 지목). 실전은 사람 확인 필요."""
        return await run_cancel_order(kis, gate, breaker, ctx, client_order_id=client_order_id)

    @server.tool()
    async def modify_order(
        ctx: Context, client_order_id: str, limit_price: str, quantity: int | None = None
    ) -> dict[str, Any]:
        """접수된 주문 가격/수량 정정. 실전은 RiskLimits+사람 확인 필요."""
        return await run_modify_order(kis, gate, breaker, ctx, client_order_id=client_order_id,
                                      limit_price=limit_price, quantity=quantity)

    return server


def main() -> None:
    """stdio MCP 서버 실행 진입점(콘솔 스크립트 ``kis-mcp``)."""
    environment = os.environ.get("KIS_MCP_ENVIRONMENT", "paper")
    kis = build_client()
    gate = RealOrderGate.from_env(dict(os.environ), environment)
    allowlist = _build_allowlist_from_env(dict(os.environ))
    breaker = _breaker_from_env(dict(os.environ))
    build_server(kis, gate=gate, allowlist=allowlist, breaker=breaker).run()


if __name__ == "__main__":
    main()
