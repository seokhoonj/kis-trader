"""MCP 실주문 가드레일(순수) -- 이중게이트·allowlist·서킷브레이커·주문티켓 빌드.

``mcp`` 에 의존하지 않아 단독 테스트된다(:mod:`kis_trader.mcp.server` 가 조립·집행). 전부 **fail-closed**:
확인할 수 없거나 조건 미충족이면 와이어 전에 거부한다. 실주문 안전은 이 모듈의 결정론적 검사(사람/AI 에
의존하지 않음)와 server 의 elicitation 확인이 함께 만든다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast, get_args

from ..errors import KISUsageError
from ..order import _GTP_DIVISIONS, DomesticDivision
from ..overseas._engine.orders import is_overseas_exchange

#: NXT 프리마켓 전용(GTP) 주문구분. MCP 실주문은 symbol 기본 거래소(KRX)로만 라우팅하고 거래소를
#: 고를 수단이 없어 NXT 전용 ``gtp_*`` 는 와이어에서 항상 거부된다 -- taint 경계에서 선제 제외해
#: 깊은 라이브러리 에러 대신 명확한 사유로 막는다(docs/mcp.md 의 "gtp_* 제외"와 일치). 단일 출처
#: :data:`kis_trader.order._GTP_DIVISIONS` 를 그대로 쓴다 -- 리터럴 재선언은 멤버 추가 시 드리프트.
_MCP_EXCLUDED_DIVISIONS = _GTP_DIVISIONS

#: 국내 주문구분(최유리지정가 immediate_limit 등). taint 경계에서 하드코딩 choices 대신 라이브러리
#: Literal 에서 파생한다(동기화 드리프트 방지). None 은 기본(지정/시장가). NXT 전용 gtp_* 는 제외.
_DIVISIONS = frozenset(get_args(DomesticDivision)) - _MCP_EXCLUDED_DIVISIONS

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_REAL_CONFIRM_PHRASE = "i-understand-real-money"
_SIDES = frozenset({"buy", "sell"})


@dataclass(frozen=True, slots=True)
class RealOrderGate:
    """실전 주문 집행의 이중게이트. 모의(paper)는 항상 집행 가능(돈이 안 나감). 실전은 명시 언락
    (``KIS_MCP_ALLOW_REAL``)과 확인 문구(``KIS_MCP_REAL_CONFIRM``)가 **둘 다** 있어야 한다."""

    environment: str
    allow_real: bool
    confirmed: bool

    @classmethod
    def from_env(cls, environ: Mapping[str, str], environment: str) -> RealOrderGate:
        """환경변수에서 이중게이트를 해석한다. ``environment`` 는 세션 환경(real/paper)."""
        return cls(
            environment=environment,
            allow_real=environ.get("KIS_MCP_ALLOW_REAL", "").strip().lower() in _TRUTHY,
            confirmed=environ.get("KIS_MCP_REAL_CONFIRM", "").strip() == _REAL_CONFIRM_PHRASE,
        )

    def is_real(self) -> bool:
        return self.environment == "real"

    def require_executable(self) -> None:
        """실전인데 게이트가 안 열렸으면 :class:`~kis_trader.errors.KISUsageError`. 모의는 통과."""
        if not self.is_real():
            return
        if not self.allow_real:
            raise KISUsageError(
                "실전 주문은 환경변수 KIS_MCP_ALLOW_REAL=1 로 명시 언락해야 한다(현재 미설정)."
            )
        if not self.confirmed:
            raise KISUsageError(
                f"실전 주문은 KIS_MCP_REAL_CONFIRM={_REAL_CONFIRM_PHRASE} 확인이 필요하다(현재 미설정)."
            )


def check_allowlist(symbol: str, allowlist: frozenset[str] | None, *, is_real: bool) -> None:
    """실전 주문은 **명시 종목 allowlist** 를 통과해야 한다 -- 비었거나(None/빈) 미포함이면 거부
    (freqtrade StaticPairList 관례: allowlist 없으면 거래 0, allow-all 없음). 모의는 무관."""
    if not is_real:
        return
    if not allowlist:
        raise KISUsageError(
            "실전 주문은 종목 allowlist 가 필요하다(KIS_MCP_SYMBOL_ALLOWLIST 미설정 -- 실거래 차단)."
        )
    if symbol not in allowlist:
        raise KISUsageError(f"{symbol!r} 은 실전 allowlist 에 없다({sorted(allowlist)}).")


class CircuitBreaker:
    """세션 반복집행 서킷브레이커 -- 한 MCP 세션에서 실주문이 ``max_real_orders`` 를 넘으면 HALT 되고
    수동 재개(:meth:`reset`) 전까지 실주문을 거부한다(FIA 반복집행 한도). 모의는 세지 않는다
    (server 가 실주문에만 :meth:`record_and_check` 를 부른다). 기본 10 은 방어적 기본이다(한 세션에 보편
    합의된 한도 숫자는 없어 보수적으로 둔다 -- 사용자가 ``KIS_MCP_MAX_REAL_ORDERS`` 로 조정)."""

    def __init__(self, max_real_orders: int = 10) -> None:
        if isinstance(max_real_orders, bool) or max_real_orders <= 0:
            raise KISUsageError(f"max_real_orders 는 양의 정수여야 한다: {max_real_orders!r}")
        self._max = max_real_orders
        self._count = 0
        self.halted = False

    def record_and_check(self) -> None:
        """실주문 1건을 세고, 한도를 넘으면 HALT 하고 거부한다. 이미 HALT 면 즉시 거부."""
        if self.halted:
            raise KISUsageError("서킷브레이커 HALT -- 세션 실주문 한도 초과. 수동 재개 전까지 차단.")
        self._count += 1
        if self._count > self._max:
            self.halted = True
            raise KISUsageError(
                f"서킷브레이커 HALT -- 세션 실주문 {self._max} 건 한도 초과. 수동 재개 필요."
            )

    def reset(self) -> None:
        """수동 재개 -- 카운터와 HALT 를 초기화한다."""
        self._count = 0
        self.halted = False


@dataclass(frozen=True, slots=True)
class StockOrderPlan:
    """검증된 주식 주문 티켓(elicitation echo + 집행용). 스칼라 입력만으로 만들어진다(taint 경계).

    국내(domestic)와 해외(overseas) 주식을 모두 담는다. 해외는 **지정가 전용**(``limit_price`` 필수)·
    ``exchange`` 필수(NAS/NYS/...)·국내 주문구분(``division``) 금지다. 국내는 종목 기본 거래소로
    라우팅하므로 ``exchange`` 를 받지 않는다(``None`` 고정). 해외 실주문의 통화별 금액 한도는 안전
    엔진이 거래소->결제통화로 판별해 fail-closed 로 건다(:class:`~kis_trader.risk.RiskLimits`
    ``max_order_notional_by_currency``)."""

    region: Literal["domestic", "overseas"]
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    quantity: int
    limit_price: str | None
    division: DomesticDivision | None = None
    exchange: str | None = None


@dataclass(frozen=True, slots=True)
class ModifyOrderPlan:
    """검증된 정정 티켓(elicitation echo + 집행용). :class:`StockOrderPlan` 과 대칭(dict 대신 dataclass).
    ``symbol``/``side`` 는 저장소에서 원주문 지문으로 해석한 값(사람 확인 티켓에 '어느 종목·매수/매도'를
    보이게 한다). 해석 불가(저장소 미보유)면 None."""

    client_order_id: str
    limit_price: str
    quantity: int | None
    symbol: str | None = None
    side: Literal["buy", "sell"] | None = None


def make_stock_order_plan(
    *, region: str, symbol: str, side: str, quantity: int, limit_price: str | None = None,
    division: str | None = None, exchange: str | None = None,
) -> StockOrderPlan:
    """스칼라 입력만으로 주문 티켓을 만든다(taint 경계 -- 읽기 도구의 dict/객체를 인자로 받지 않는다).
    깊은 검증(가격/수량 규칙)은 라이브러리 :class:`~kis_trader.order.Order` 가 집행 시 하고, 여기선 taint
    경계(스칼라만)와 표면 형상만 본다.

    국내(domestic)와 해외(overseas) 주식을 지원한다. 해외는 **지정가 전용**(``limit_price`` 필수)·
    ``exchange`` 필수(지원 거래소코드 NAS/NYS/...)·국내 주문구분(``division``) 금지다. 국내는 종목 기본
    거래소(KRX)로 라우팅하므로 ``exchange`` 를 받지 않는다(주면 거부). 해외 실주문의 통화별 금액 한도는
    안전 엔진이 거래소->결제통화로 판별해 fail-closed 로 건다."""
    if region not in ("domestic", "overseas"):
        raise KISUsageError(f"region 은 domestic/overseas 중 하나여야 한다: {region!r}")
    if side not in _SIDES:
        raise KISUsageError(f"side 는 buy/sell 중 하나여야 한다: {side!r}")
    if not isinstance(symbol, str) or not symbol:
        raise KISUsageError(f"symbol 은 비어 있지 않은 문자열이어야 한다: {symbol!r}")
    # bool 은 int 서브클래스라 True 가 수량 1 로 새어 든다 -- 먼저 거부.
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise KISUsageError(f"quantity 는 양의 정수여야 한다: {quantity!r}")
    if limit_price is not None and not isinstance(limit_price, (str, int)):
        raise KISUsageError(f"limit_price 는 문자열/정수 스칼라여야 한다: {limit_price!r}")
    if region == "overseas":
        if limit_price is None:
            raise KISUsageError("해외 주문은 지정가 전용이다 -- limit_price 를 지정하라(시장가 미지원).")
        if division is not None:
            raise KISUsageError("해외 주문은 국내 주문구분(division)을 받지 않는다(지정가 전용).")
        if not isinstance(exchange, str) or not exchange:
            raise KISUsageError("해외 주문은 거래소코드(exchange)가 필요하다(NAS/NYS/AMS/HKS/TSE/...).")
        if not is_overseas_exchange(exchange):
            raise KISUsageError(f"해외 주문을 지원하지 않는 거래소코드: {exchange!r}.")
    else:  # domestic -- 종목 기본 거래소(KRX)로만 보낸다
        if exchange is not None:
            raise KISUsageError(
                f"국내 주문은 exchange 를 받지 않는다(종목 기본 거래소로 라우팅): {exchange!r}."
            )
        if division is not None and division not in _DIVISIONS:
            if division in _MCP_EXCLUDED_DIVISIONS:
                raise KISUsageError(
                    f"division={division!r} 은 NXT 프리마켓(GTP) 전용이라 MCP 실주문에서 지원하지 않는다"
                    " -- MCP 는 종목 기본 거래소(KRX)로만 보낸다. 라이브러리 API 를 직접 쓰라."
                )
            raise KISUsageError(f"division 은 {sorted(_DIVISIONS)} 중 하나여야 한다: {division!r}")
    order_type: Literal["market", "limit"] = "limit" if limit_price is not None else "market"
    normalized_price = str(limit_price) if limit_price is not None else None
    return StockOrderPlan(
        region=cast("Literal['domestic', 'overseas']", region),  # 위에서 두 값으로 좁힘
        symbol=symbol,
        side=side,  # type: ignore[arg-type]  # 위에서 _SIDES 로 좁힘
        order_type=order_type,
        quantity=quantity,
        limit_price=normalized_price,
        division=cast("DomesticDivision | None", division),  # 위에서 _DIVISIONS 로 좁힘(해외는 None)
        exchange=exchange if region == "overseas" else None,
    )
