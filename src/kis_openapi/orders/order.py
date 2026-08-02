"""주문 요청(DATA) 타입 -- 불변 :class:`Order`.

주문 타입과 필요한 가격의 관계(지정가는 ``limit_price`` 필수, 스탑은 ``stop_price``
필수)를 **생성 시점에 강제**한다(FIX OrdType 규칙). "두 개의 선택 가격을 가진 한 구조체"가
아니라 타입별 생성자(:meth:`Order.market` / :meth:`Order.limit` / :meth:`Order.stop` /
:meth:`Order.stop_limit`)로 만들어, 잘못된 조합이 애초에 표현 불가능하게 한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal

from ..errors import KisUsageError

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit"]
TimeInForce = Literal["day", "gtc", "ioc", "fok"]

_SIDES = frozenset(("buy", "sell"))
_ORDER_TYPES = frozenset(("market", "limit", "stop", "stop_limit"))
_TIFS = frozenset(("day", "gtc", "ioc", "fok"))
_NEEDS_LIMIT = frozenset(("limit", "stop_limit"))
_NEEDS_STOP = frozenset(("stop", "stop_limit"))

_KST = timezone(timedelta(hours=9))


def mint_client_order_id() -> str:
    """거래일 내 유일한 ``client_order_id`` 를 발행한다(전송 전, 멱등키).

    날짜(KST) + UUID 조각으로 만들어 날짜를 넘겨도 충돌하지 않게 한다(FIX 권고). KIS는
    네이티브 멱등키가 없으므로 이 id가 유일한 중복-방지 수단이다.
    """
    return f"{datetime.now(_KST):%Y%m%d}-{uuid.uuid4().hex[:16]}"


def _as_decimal(value: object, name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as err:
        raise KisUsageError(f"{name} 는 숫자여야 한다: {value!r}") from err


def format_wire_decimal(value: Decimal) -> str:
    """Decimal 을 KIS 와이어 정본 문자열로: 지수표기·컨텍스트 반올림 없이 고정소수점.

    ``format(x, "f")`` 는 ``normalize()`` 와 달리 정밀도로 반올림하지 않고 지수표기만
    펼친다. 주문 전송(단가/수량)과 요청 지문이 **같은** 정본을 쓰도록 여기 한 곳에 둔다
    -- 정본이 갈리면 와이어가 동일한 주문이 지문은 달라져 멱등 판정이 깨진다.
    """
    return format(value, "f")


@dataclass(frozen=True, slots=True)
class Order:
    """한 건의 주문 요청(불변).

    직접 만들기보다 타입별 생성자를 쓰는 것을 권한다. ``client_order_id`` 는 지정하지
    않으면 자동 발행된다.
    """

    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = "day"
    exchange: str = "XKRX"
    client_order_id: str = field(default_factory=mint_client_order_id)

    def __post_init__(self) -> None:
        # 어떤 생성 경로(직접 Order(...) 포함)로도 가격/수량이 Decimal 이 되게 강제한다.
        # frozen 이라 object.__setattr__ 로 다시 쓴다.
        object.__setattr__(self, "quantity", _as_decimal(self.quantity, "quantity"))
        if self.limit_price is not None:
            object.__setattr__(self, "limit_price", _as_decimal(self.limit_price, "limit_price"))
        if self.stop_price is not None:
            object.__setattr__(self, "stop_price", _as_decimal(self.stop_price, "stop_price"))

        if self.side not in _SIDES:
            raise KisUsageError(f"side 는 buy/sell 중 하나여야 한다: {self.side!r}")
        if self.order_type not in _ORDER_TYPES:
            raise KisUsageError(f"지원하지 않는 order_type: {self.order_type!r}")
        if self.time_in_force not in _TIFS:
            raise KisUsageError(f"지원하지 않는 time_in_force: {self.time_in_force!r}")
        if self.quantity <= 0:
            raise KisUsageError(f"quantity 는 0보다 커야 한다: {self.quantity}")

        needs_limit = self.order_type in _NEEDS_LIMIT
        needs_stop = self.order_type in _NEEDS_STOP
        # 타입 -> 가격 의존성(FIX): 필요한 가격은 있어야, 불필요한 가격은 없어야 한다.
        if needs_limit and self.limit_price is None:
            raise KisUsageError(f"{self.order_type} 주문은 limit_price 가 필요하다")
        if not needs_limit and self.limit_price is not None:
            raise KisUsageError(f"{self.order_type} 주문에는 limit_price 를 줄 수 없다")
        if needs_stop and self.stop_price is None:
            raise KisUsageError(f"{self.order_type} 주문은 stop_price 가 필요하다")
        if not needs_stop and self.stop_price is not None:
            raise KisUsageError(f"{self.order_type} 주문에는 stop_price 를 줄 수 없다")
        if self.limit_price is not None and self.limit_price <= 0:
            raise KisUsageError(f"limit_price 는 0보다 커야 한다: {self.limit_price}")
        if self.stop_price is not None and self.stop_price <= 0:
            raise KisUsageError(f"stop_price 는 0보다 커야 한다: {self.stop_price}")

    @property
    def fingerprint(self) -> tuple[str, ...]:
        """이 주문의 요청 지문 -- 같은 ``client_order_id`` 를 *다른* 주문에 재사용했는지
        판별하는 데 쓴다(멱등 replay 는 지문이 같을 때만 허용). 수치는 와이어와 **같은**
        정본(:func:`format_wire_decimal`)으로 -- 그래야 ``10`` 과 ``1E1`` 이 같은 지문이 된다.
        """
        return (
            self.symbol, self.side, self.order_type,
            format_wire_decimal(self.quantity),
            "" if self.limit_price is None else format_wire_decimal(self.limit_price),
            "" if self.stop_price is None else format_wire_decimal(self.stop_price),
            self.time_in_force, self.exchange,
        )

    # --- 타입별 생성자(권장 진입점) ------------------------------------
    @classmethod
    def _build(
        cls, symbol: str, side: Side, order_type: OrderType, quantity: object, *,
        limit_price: object | None = None, stop_price: object | None = None,
        time_in_force: TimeInForce = "day", exchange: str = "XKRX",
        client_order_id: str | None = None,
    ) -> Order:
        quantity_dec = _as_decimal(quantity, "quantity")
        limit_dec = None if limit_price is None else _as_decimal(limit_price, "limit_price")
        stop_dec = None if stop_price is None else _as_decimal(stop_price, "stop_price")
        # client_order_id 를 안 주면 필드의 default_factory 가 발행하도록 아예 넘기지 않는다
        # (None 을 넘기면 factory 를 덮어써 멱등키가 사라진다).
        if client_order_id is None:
            return cls(
                symbol, side, order_type, quantity_dec,
                limit_price=limit_dec, stop_price=stop_dec,
                time_in_force=time_in_force, exchange=exchange,
            )
        return cls(
            symbol, side, order_type, quantity_dec,
            limit_price=limit_dec, stop_price=stop_dec,
            time_in_force=time_in_force, exchange=exchange,
            client_order_id=client_order_id,
        )

    @classmethod
    def market(cls, symbol: str, *, side: Side, quantity: object,
               time_in_force: TimeInForce = "day", exchange: str = "XKRX",
               client_order_id: str | None = None) -> Order:
        """시장가 주문."""
        return cls._build(symbol, side, "market", quantity, time_in_force=time_in_force,
                          exchange=exchange, client_order_id=client_order_id)

    @classmethod
    def limit(cls, symbol: str, *, side: Side, quantity: object, limit_price: object,
              time_in_force: TimeInForce = "day", exchange: str = "XKRX",
              client_order_id: str | None = None) -> Order:
        """지정가 주문."""
        return cls._build(symbol, side, "limit", quantity, limit_price=limit_price,
                          time_in_force=time_in_force, exchange=exchange, client_order_id=client_order_id)

    @classmethod
    def stop(cls, symbol: str, *, side: Side, quantity: object, stop_price: object,
             time_in_force: TimeInForce = "day", exchange: str = "XKRX",
             client_order_id: str | None = None) -> Order:
        """스탑(역지정) 주문 -- ``stop_price`` 도달 시 시장가로 전환."""
        return cls._build(symbol, side, "stop", quantity, stop_price=stop_price,
                          time_in_force=time_in_force, exchange=exchange, client_order_id=client_order_id)

    @classmethod
    def stop_limit(cls, symbol: str, *, side: Side, quantity: object, limit_price: object,
                   stop_price: object, time_in_force: TimeInForce = "day", exchange: str = "XKRX",
                   client_order_id: str | None = None) -> Order:
        """스탑지정가 주문 -- ``stop_price`` 도달 시 ``limit_price`` 지정가로 전환."""
        return cls._build(symbol, side, "stop_limit", quantity, limit_price=limit_price,
                          stop_price=stop_price, time_in_force=time_in_force, exchange=exchange,
                          client_order_id=client_order_id)
