"""MCP 도구의 순수 핸들러 -- ``kis_trader`` 공개 API 를 호출해 JSON 안전 dict 를 돌려준다.

``mcp`` 에 의존하지 않아 단독으로 테스트된다. :mod:`kis_trader.mcp.server` 가 이 함수들을 MCP 도구로
등록한다. 각 함수는 도메인 계산을 하지 않고 패키지 결과를 필드 선택·직렬화만 한다(안전커널 규율).

직렬화는 frozen dataclass 를 공개 필드 dict 로 바꾸되 ``_raw`` 와 밑줄 필드를 제외하고 ``Decimal`` 을
문자열로 바꾼다 -- 원본 응답·자격증명이 새어 나가지 않게 한다.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from typing import Any, Literal, cast

from ..account import StockAccount
from ..client import KISClient
from ..errors import KISUsageError
from ._guardrails import RealOrderGate, StockOrderPlan, check_allowlist, make_stock_order_plan

_Direction = Literal["gainers", "losers"]


def _serialize(obj: Any) -> Any:
    """frozen dataclass/Decimal/list/dict 를 JSON 안전 값으로. ``_raw`` 와 밑줄 필드는 제외한다."""
    if is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {}
        for f in fields(obj):
            if f.name.startswith("_"):  # _raw 및 내부 필드 제외 -- 원본/자격증명 누출 방지
                continue
            out[f.name] = _serialize(getattr(obj, f.name))
        return out
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _account_masked(kis: KISClient) -> str:
    """계좌번호를 끝 4자리만 남기고 마스킹(전체 계좌번호 비노출)."""
    acct = str(getattr(kis, "_account", "") or "")
    digits = "".join(ch for ch in acct if ch.isdigit())
    return f"****{digits[-4:]}" if len(digits) >= 4 else "****"


def _stock_account(kis: KISClient) -> StockAccount:
    """잔고/보유/미체결 도구는 주식 계좌 뷰 전용 -- 파생 계좌면 명확히 거부(계좌 파사드는 다형)."""
    account = kis.account
    if not isinstance(account, StockAccount):
        raise KISUsageError(
            f"이 도구는 주식 계좌 전용이다(현재 계좌 뷰: {type(account).__name__})."
        )
    return account


def quote(kis: KISClient, symbol: str, *, market: str = "domestic") -> dict[str, Any]:
    """종목 현재가 스냅샷. ``market="overseas"`` 면 해외(거래소 자동 해석)."""
    handle = kis.overseas.stock(symbol) if market == "overseas" else kis.domestic.stock(symbol)
    out: dict[str, Any] = _serialize(handle.quote())
    return out


def search(kis: KISClient, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """이름/질의로 국내 종목 후보를 찾는다(코드 추측 금지 -- 실제 코드는 여기서만 온다)."""
    hits = kis.domestic.search(query)
    out: list[dict[str, Any]] = [_serialize(h) for h in list(hits)[:limit]]
    return out


def ranking_change(kis: KISClient, *, direction: str = "gainers", limit: int = 10) -> list[dict[str, Any]]:
    """국내 등락률 순위. ``direction="gainers"`` 상승 / ``"losers"`` 하락."""
    if direction not in ("gainers", "losers"):
        raise KISUsageError(f"direction 은 'gainers'/'losers' 만 (받은 값: {direction!r}).")
    rows = kis.domestic.ranking.by_change(direction=cast(_Direction, direction))
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def balance(kis: KISClient) -> dict[str, Any]:
    """계좌 잔고 요약(주식 계좌 뷰). 도메인 수치는 패키지가 만든다."""
    out: dict[str, Any] = _serialize(_stock_account(kis).balance())
    return out


def positions(kis: KISClient, *, limit: int = 100) -> list[dict[str, Any]]:
    """보유 종목(주식 계좌)."""
    rows = _stock_account(kis).domestic.positions()
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def open_orders(kis: KISClient, *, limit: int = 100) -> list[dict[str, Any]]:
    """미체결(정정/취소 가능) 주문(주식 계좌)."""
    rows = _stock_account(kis).domestic.open_orders()
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def order_preview(
    kis: KISClient, *, symbol: str, side: str, quantity: int,
    limit_price: str | None = None, division: str | None = None,
) -> dict[str, Any]:
    """주문 **미리보기**(dry-run) -- 전송하지 않고 나갈 티켓만 되읽어 보여준다. 실제 전송은 ``place_order``
    가 가드레일(이중게이트+RiskLimits+allowlist+사람확인)을 거쳐 한다. 이 도구는 무엇이 나갈지 미리
    확인하는 용도다(``sent: false``)."""
    if side not in ("buy", "sell"):
        raise ValueError(f"side 는 'buy'/'sell' 만 (받은 값: {side!r}).")
    if quantity <= 0:
        raise ValueError("quantity 는 1 이상이어야 한다.")
    return {
        "sent": False,
        "note": "미리보기 -- 전송되지 않았다. 실주문은 사람 승인 하에 kis_trader 파이썬 API 로 낸다.",
        "environment": kis.environment,
        "account": _account_masked(kis),
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "order_type": "limit" if limit_price is not None else "market",
        "limit_price": limit_price,
        "division": division,
    }


def reconcile(kis: KISClient, *, client_order_id: str) -> dict[str, Any]:
    """결과 불명 주문의 사후 확정(유일한 사후 진실). 미해소면 in-flight 로 남긴다."""
    report = kis.orders.reconcile(client_order_id)
    return {
        "client_order_id": client_order_id,
        "resolved": report is not None,
        "report": _serialize(report) if report is not None else None,
    }


# --- 실주문 plan 핸들러(순수: 가드레일 선검증, 와이어 전) --------------------
# server 가 이 plan 으로 elicitation 확인 뒤 라이브러리(buy/sell/cancel/modify)를 호출한다. 어떤 검사든
# 어기면 여기서 KISUsageError 로 거부되어 주문은 전송되지 않는다(fail-closed, 사람/AI 에 의존 안 함).
def plan_place_order(
    gate: RealOrderGate, allowlist: frozenset[str] | None, has_risk: bool, *,
    venue: str, symbol: str, side: str, quantity: int, limit_price: str | None = None,
) -> StockOrderPlan:
    """매수/매도 주문 계획 -- 이중게이트 -> (실전) RiskLimits 필수 -> allowlist -> 티켓 빌드 순으로
    검증한다. 실전에서 RiskLimits(fail-closed 캡)가 없으면 거부한다(관례: 캡은 사용자 설정, 미설정=거부)."""
    gate.require_executable()
    if gate.is_real() and not has_risk:
        raise KISUsageError(
            "실전 주문은 RiskLimits(fat-finger 캡) 설정이 필요하다 -- 미설정이면 전송하지 않는다"
            "(fail-closed). KISClient(risk=RiskLimits(...)) 또는 MCP 캡 환경변수로 설정."
        )
    check_allowlist(symbol, allowlist, is_real=gate.is_real())
    return make_stock_order_plan(
        venue=venue, symbol=symbol, side=side, quantity=quantity, limit_price=limit_price
    )


def plan_cancel_order(gate: RealOrderGate, *, client_order_id: str) -> str:
    """취소 계획 -- 이중게이트만(취소는 리스크를 늘리지 않음). 기존 ``client_order_id`` 를 지목한다."""
    gate.require_executable()
    if not isinstance(client_order_id, str) or not client_order_id:
        raise KISUsageError(f"client_order_id 는 비어 있지 않은 문자열이어야 한다: {client_order_id!r}")
    return client_order_id


def plan_modify_order(
    gate: RealOrderGate, has_risk: bool, *, client_order_id: str, limit_price: str, quantity: int | None = None
) -> dict[str, Any]:
    """정정 계획 -- 새 가격을 거는 변경이라 신규 주문처럼 (실전) RiskLimits 를 요구한다(fail-closed)."""
    gate.require_executable()
    if gate.is_real() and not has_risk:
        raise KISUsageError(
            "실전 정정은 RiskLimits 설정이 필요하다(fail-closed) -- 새 가격이 캡을 통과해야 한다."
        )
    if not isinstance(client_order_id, str) or not client_order_id:
        raise KISUsageError(f"client_order_id 는 비어 있지 않은 문자열이어야 한다: {client_order_id!r}")
    if not isinstance(limit_price, (str, int)):
        raise KISUsageError(f"limit_price 는 문자열/정수 스칼라여야 한다: {limit_price!r}")
    if quantity is not None and (isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0):
        raise KISUsageError(f"quantity 는 양의 정수여야 한다: {quantity!r}")
    return {"client_order_id": client_order_id, "limit_price": str(limit_price), "quantity": quantity}
