"""MCP 도구의 순수 핸들러 -- ``kis_trader`` 공개 API 를 호출해 JSON 안전 dict 를 돌려준다.

``mcp`` 에 의존하지 않아 단독으로 테스트된다. :mod:`kis_trader.mcp.server` 가 이 함수들을 MCP 도구로
등록한다. 각 함수는 도메인 계산을 하지 않고 패키지 결과를 필드 선택·직렬화만 한다(안전커널 규율).

직렬화는 frozen dataclass 를 공개 필드 dict 로 바꾸되 ``_raw`` 와 밑줄 필드를 제외하고 ``Decimal`` 을
문자열로 바꾼다 -- 원본 응답·자격증명이 새어 나가지 않게 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, cast

from .._literals import Direction
from ..account import StockAccount
from ..client import KISClient
from ..errors import KISUsageError
from ..order import DomesticDivision, Side
from ._guardrails import (
    ModifyOrderPlan,
    RealOrderGate,
    StockOrderPlan,
    check_allowlist,
    make_stock_order_plan,
)

#: 결과에 **절대** 실려선 안 되는 자격증명 키(방어적 denylist). 현재 직렬화 대상은 엔티티뿐이라
#: 이 키들이 나올 일이 없지만, 미래에 raw 매핑을 돌려주는 핸들러가 생겨도 시크릿이 새지 않게 한다.
#: (``account``/``cano`` 같은 값은 정당한 마스킹 대상이라 여기 넣지 않는다 -- 계좌는 _account_masked 로 처리.)
_SECRET_KEYS = frozenset({
    "app_key", "appkey", "app_secret", "appsecret", "secret", "secret_key",
    "access_token", "token", "approval_key", "authorization",
})


def _is_secret_key(key: object) -> bool:
    return isinstance(key, str) and key.lower().replace("-", "_") in _SECRET_KEYS


def _serialize(value: Any) -> Any:
    """frozen dataclass/Decimal/list/dict 를 JSON 안전 값으로. ``_raw``·밑줄 필드/키와 자격증명 키
    (``_SECRET_KEYS``)는 제외한다(원본 응답·자격증명 누출 방지)."""
    if is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {}
        for dc_field in fields(value):
            if dc_field.name.startswith("_") or _is_secret_key(dc_field.name):  # _raw·내부·시크릿 제외
                continue
            out[dc_field.name] = _serialize(getattr(value, dc_field.name))
        return out
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        # 엔티티의 date/datetime/time 필드(Quote.as_of, ExecutionReport.recorded_at 등)를 ISO 문자열로.
        # 안 바꾸면 strict json.dumps 소비자가 크래시하고 SDK 폴백은 비-ISO str() 포맷을 내보낸다.
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, Mapping):
        # 매핑 분기도 밑줄 키를 제외 -- dataclass 분기와 같은 규율(중첩 매핑의 _raw/내부키 누출 방지).
        # ``Mapping`` 으로 받아 ``MappingProxyType`` (freeze_vendor_payload 결과)도 재귀 처리한다.
        return {key: _serialize(item) for key, item in value.items()
                if not (isinstance(key, str) and key.startswith("_")) and not _is_secret_key(key)}
    return value


def _account_masked(kis: KISClient) -> str:
    """계좌번호를 마지막 4자리만 남기고 마스킹(전체 계좌번호 비노출)."""
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


def quote(kis: KISClient, symbol: str, *, region: str = "domestic") -> dict[str, Any]:
    """종목 현재가 스냅샷. ``region="overseas"`` 면 해외(거래소 자동 판별)."""
    if region not in ("domestic", "overseas"):
        raise KISUsageError(f"region 은 'domestic'/'overseas' 만 (받은 값: {region!r}).")
    handle = kis.overseas.stock(symbol) if region == "overseas" else kis.domestic.stock(symbol)
    out: dict[str, Any] = _serialize(handle.quote())
    return out


def _require_positive_limit(limit: int) -> None:
    """리스트 핸들러의 행 상한 검증 -- bool(int 서브클래스)·비int·1 미만을 거부한다. 음수 limit 은
    ``[:limit]`` 에서 마지막 행만 빼고 돌려주는(에러 아닌 오답) 함정이라 fail-closed 로 막는다."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise KISUsageError(f"limit 은 1 이상의 정수여야 한다: {limit!r}")


def search(kis: KISClient, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """이름/질의로 국내 종목 후보를 찾는다(코드 추측 금지 -- 실제 코드는 여기서만 온다)."""
    _require_positive_limit(limit)
    hits = kis.domestic.search_stocks(query)
    out: list[dict[str, Any]] = [_serialize(h) for h in list(hits)[:limit]]
    return out


def ranking_change(kis: KISClient, *, direction: str = "gainers", limit: int = 10) -> list[dict[str, Any]]:
    """국내 등락률 순위. ``direction="gainers"`` 상승 / ``"losers"`` 하락."""
    if direction not in ("gainers", "losers"):
        raise KISUsageError(f"direction 은 'gainers'/'losers' 만 (받은 값: {direction!r}).")
    _require_positive_limit(limit)
    rows = kis.domestic.ranking.by_change(direction=cast(Direction, direction))
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def balance(kis: KISClient) -> dict[str, Any]:
    """계좌 잔고 요약(주식 계좌 뷰). 도메인 수치는 패키지가 만든다."""
    out: dict[str, Any] = _serialize(_stock_account(kis).balance())
    return out


def positions(kis: KISClient, *, limit: int = 100) -> list[dict[str, Any]]:
    """보유 종목(국내 주식 계좌)."""
    _require_positive_limit(limit)
    rows = _stock_account(kis).domestic.positions()
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def open_orders(kis: KISClient, *, limit: int = 100) -> list[dict[str, Any]]:
    """미체결(정정/취소 가능) 주문(국내 주식 계좌)."""
    _require_positive_limit(limit)
    rows = _stock_account(kis).domestic.open_orders()
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def order_preview(
    kis: KISClient, *, symbol: str, side: Side, quantity: int,
    limit_price: str | None = None, division: DomesticDivision | None = None,
) -> dict[str, Any]:
    """주문 **미리보기**(dry-run) -- 전송하지 않고 나갈 티켓만 되읽어 보여준다. 실제 전송은 ``place_order``
    가 가드레일(이중게이트+RiskLimits+allowlist+사람확인)을 거쳐 한다. 이 도구는 무엇이 나갈지 미리
    확인하는 용도다(``sent: false``). 티켓은 ``place_order`` 와 **같은** 형상 검증(``make_stock_order_plan``:
    symbol/side/quantity/division 형상 + taint 경계)을 거쳐 만든다 -- preview 가 통과시킨 주문은
    place_order 도 같은 형상으로 받는다(형상 divergence 방지; 가격·구분 결합 등 깊은 검증은 집행 시
    :class:`~kis_trader.order.Order` 가 한다)."""
    plan = make_stock_order_plan(
        region="domestic", symbol=symbol, side=side, quantity=quantity,
        limit_price=limit_price, division=division,
    )
    return {
        "sent": False,
        "note": "미리보기 -- 전송되지 않았다. 실주문은 place_order 가 가드레일·사람확인을 거쳐 전송한다.",
        "environment": kis.environment,
        "account": _account_masked(kis),
        "symbol": plan.symbol,
        "side": plan.side,
        "quantity": plan.quantity,
        "order_type": plan.order_type,
        "limit_price": plan.limit_price,
        "division": plan.division,
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
    region: str, symbol: str, side: str, quantity: int, limit_price: str | None = None,
    division: str | None = None,
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
        region=region, symbol=symbol, side=side, quantity=quantity, limit_price=limit_price,
        division=division,
    )


def plan_cancel_order(gate: RealOrderGate, *, client_order_id: str) -> str:
    """취소 계획 -- 이중게이트만(취소는 리스크를 늘리지 않음). 기존 ``client_order_id`` 를 지목한다."""
    gate.require_executable()
    if not isinstance(client_order_id, str) or not client_order_id:
        raise KISUsageError(f"client_order_id 는 비어 있지 않은 문자열이어야 한다: {client_order_id!r}")
    return client_order_id


def plan_modify_order(
    gate: RealOrderGate, has_risk: bool, allowlist: frozenset[str] | None = None, *,
    client_order_id: str, limit_price: str, quantity: int | None = None,
    symbol: str | None = None, side: Side | None = None,
) -> ModifyOrderPlan:
    """정정 계획 -- 새 가격을 거는 변경이라 신규 주문처럼 (실전) RiskLimits 를 요구한다(fail-closed).
    원주문 종목(``symbol``)이 저장소에서 해석되면 신규 주문과 같이 실전 allowlist 도 확인한다 -- 정정은
    노출을 늘릴 수 있어(재가격·수량) place 와 같은 종목 통제를 받아야 한다. 종목을 모르면(저장소 미보유)
    allowlist 는 건너뛴다(그 외 게이트/사람확인은 그대로)."""
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
    if symbol is not None:
        check_allowlist(symbol, allowlist, is_real=gate.is_real())
    return ModifyOrderPlan(client_order_id=client_order_id, limit_price=str(limit_price),
                           quantity=quantity, symbol=symbol, side=side)
