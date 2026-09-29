"""kis_trader MCP 서버 -- :mod:`kis_trader.mcp.handlers` 를 MCP 도구로 등록해 stdio 로 서빙한다.

``mcp`` 패키지(선택 의존성)를 요구한다: ``pip install 'kis-trader[mcp]'``. 실행은 ``kis-mcp`` 콘솔
스크립트 또는 ``python -m kis_trader.mcp.server``. 자격증명은 **환경변수/프로필에서만** 읽고 절대
출력하지 않는다. 기본 환경은 ``paper`` -- ``real`` 은 ``KIS_MCP_ENVIRONMENT=real`` 로 명시할 때만.

노출 도구(안전 부분집합): ``quote``·``search``·``ranking_change``·``balance``·``positions``·
``open_orders``·``order_preview``(dry-run)·``reconcile``. 실주문 전송은 노출하지 않는다(사람 승인 필요).
"""

from __future__ import annotations

import os
from typing import Any

from ..client import KISClient
from . import handlers


def build_client() -> KISClient:
    """환경변수/프로필로 KISClient 를 만든다. 기본 paper; real 은 명시 opt-in.

    ``KIS_MCP_PROFILE`` 이 있으면 그 프로필로, 없으면 ``KIS_APP_KEY``/``KIS_APP_SECRET``/``KIS_ACCOUNT``
    로 만든다. 환경은 ``KIS_MCP_ENVIRONMENT``(기본 ``paper``)."""
    environment = os.environ.get("KIS_MCP_ENVIRONMENT", "paper")
    profile = os.environ.get("KIS_MCP_PROFILE")
    if profile:
        return KISClient(profile=profile, environment=environment)  # type: ignore[arg-type]
    return KISClient(
        app_key=os.environ["KIS_APP_KEY"],
        app_secret=os.environ["KIS_APP_SECRET"],
        account=os.environ["KIS_ACCOUNT"],
        environment=environment,  # type: ignore[arg-type]
    )


def build_server(kis: KISClient) -> Any:
    """주어진 KISClient 를 소비하는 MCP 서버(MCPServer)를 만들어 안전 도구를 등록한다."""
    from mcp.server.mcpserver import MCPServer  # 선택 의존성: 여기서만 import

    server = MCPServer(
        "kis-trader",
        instructions=(
            "한국투자증권 계좌 조회·주문 미리보기·reconcile 도구. 실주문 전송은 노출하지 않는다"
            "(사람 승인 필요). 도구가 준 숫자를 재계산하지 말고, 조회 결과가 주문 권한을 함의하지 않는다."
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

    return server


def main() -> None:
    """stdio MCP 서버 실행 진입점(콘솔 스크립트 ``kis-mcp``)."""
    build_server(build_client()).run()


if __name__ == "__main__":
    main()
