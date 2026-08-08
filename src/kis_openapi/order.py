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
from typing import Literal, NamedTuple

from ._wire import format_wire_decimal
from .errors import KISUsageError

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit"]
TimeInForce = Literal["day", "gtc", "ioc", "fok"]
#: 국내 신용주문 유형 코드(원장 코드표). 매수/매도별로 유효 코드가 다르고(아래 상수), 신규/상환
#: 여부로 대출일자(LOAN_DT) 요구가 갈린다.
CreditType = Literal["21", "22", "23", "24", "25", "26", "27", "28"]


class Fingerprint(NamedTuple):
    """주문의 요청 지문(멱등 dedup 키). 필드를 이름으로 읽어 매직 인덱스를 없앤다 -- 특히
    ``exchange`` 로 국내/해외 재조회 경로를 가르므로 위치 이동에 취약하면 안전 라우팅이 깨진다.
    수치 필드는 와이어와 같은 정본 문자열(:func:`format_wire_decimal`)이다.

    ``credit_type`` 은 신용주문 유형(현금주문은 ``""``), ``loan_date`` 는 그 신용주문의 대출일자
    (현금주문·신규신용은 ``""``, 상환신용은 대상 대출일자) -- 같은 종목·수량·가격이라도 현금 vs
    신용, 또 서로 다른 대출을 상환하는 신용주문은 서로 다른 주문이므로 지문으로 구분해야
    replay/conflict 판정이 정확하다."""

    symbol: str
    side: Side
    order_type: OrderType
    quantity: str
    limit_price: str
    stop_price: str
    time_in_force: TimeInForce
    exchange: str
    credit_type: str = ""
    loan_date: str = ""


class WireRequest(NamedTuple):
    """주문 와이어 요청 -- 시장별 빌더가 조립해 안전 코어(place)에 넘긴다. 세 문자열이 서로
    바뀌어도 타입이 못 잡던 것을 이름으로 막는다."""

    method: str
    path: str
    tr_id: str
    body: dict[str, str]

_SIDES = frozenset(("buy", "sell"))
_ORDER_TYPES = frozenset(("market", "limit", "stop", "stop_limit"))
_TIFS = frozenset(("day", "gtc", "ioc", "fok"))
_NEEDS_LIMIT = frozenset(("limit", "stop_limit"))
_NEEDS_STOP = frozenset(("stop", "stop_limit"))

#: 신용주문 유형(국내, 원장 코드표). 매수/매도별로 유효한 코드가 다르다.
_CREDIT_BUY_TYPES = frozenset(("21", "23", "26", "28"))    # 자기융자신규/유통융자신규/유통대주상환/자기대주상환
_CREDIT_SELL_TYPES = frozenset(("22", "24", "25", "27"))   # 유통대주신규/자기대주신규/자기융자상환/유통융자상환
#: 신규(융자/대주 개시) vs 상환. 대출일자(LOAN_DT)는 신규면 개시일(오늘), 상환이면 대상 대출일자다.
_CREDIT_NEW_TYPES = frozenset(("21", "22", "23", "24"))    # 자기융자/유통대주/유통융자/자기대주 신규
_CREDIT_REPAY_TYPES = frozenset(("25", "26", "27", "28"))  # 자기융자/유통대주/유통융자/자기대주 상환

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
        raise KISUsageError(f"{name} 는 숫자여야 한다: {value!r}") from err


def _validate_loan_date(value: str) -> None:
    """대출일자가 실재하는 YYYYMMDD 날짜인지 확인 -- 형식만 맞고 불가능한 날짜(20261399 등)는 거부."""
    try:
        datetime.strptime(value, "%Y%m%d")  # noqa: DTZ007 -- 날짜 유효성만 확인
    except ValueError as err:
        raise KISUsageError(f"loan_date 는 실재하는 YYYYMMDD 날짜여야 한다: {value!r}") from err


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
    credit_type: CreditType | None = None
    loan_date: str | None = None
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
            raise KISUsageError(f"side 는 buy/sell 중 하나여야 한다: {self.side!r}")
        if self.order_type not in _ORDER_TYPES:
            raise KISUsageError(f"지원하지 않는 order_type: {self.order_type!r}")
        if self.time_in_force not in _TIFS:
            raise KISUsageError(f"지원하지 않는 time_in_force: {self.time_in_force!r}")
        if self.quantity <= 0:
            raise KISUsageError(f"quantity 는 0보다 커야 한다: {self.quantity}")

        needs_limit = self.order_type in _NEEDS_LIMIT
        needs_stop = self.order_type in _NEEDS_STOP
        # 타입 -> 가격 의존성(FIX): 필요한 가격은 있어야, 불필요한 가격은 없어야 한다.
        if needs_limit and self.limit_price is None:
            raise KISUsageError(f"{self.order_type} 주문은 limit_price 가 필요하다")
        if not needs_limit and self.limit_price is not None:
            raise KISUsageError(f"{self.order_type} 주문에는 limit_price 를 줄 수 없다")
        if needs_stop and self.stop_price is None:
            raise KISUsageError(f"{self.order_type} 주문은 stop_price 가 필요하다")
        if not needs_stop and self.stop_price is not None:
            raise KISUsageError(f"{self.order_type} 주문에는 stop_price 를 줄 수 없다")
        if self.limit_price is not None and self.limit_price <= 0:
            raise KISUsageError(f"limit_price 는 0보다 커야 한다: {self.limit_price}")
        if self.stop_price is not None and self.stop_price <= 0:
            raise KISUsageError(f"stop_price 는 0보다 커야 한다: {self.stop_price}")

        # loan_date 는 신용주문에서만 의미 있다(현금주문에 대출일자를 주면 표현 불가능한 상태).
        if self.loan_date is not None and self.credit_type is None:
            raise KISUsageError("loan_date 는 신용주문(credit_type)에만 줄 수 있다.")
        if self.loan_date is not None:
            _validate_loan_date(self.loan_date)
        if self.credit_type is not None:
            # 신용주문은 국내(KRX) 마진 전용 -- 해외 거래소와 조합하면 라우팅이 해외 빌더로 새어
            # 신용 의미가 조용히 사라진다. 생성 시점에 fail-closed.
            if self.exchange != "XKRX":
                raise KISUsageError(
                    f"신용주문은 국내(XKRX)만 지원한다 -- credit_type 과 exchange={self.exchange!r} 는 "
                    f"조합할 수 없다."
                )
            # 매수/매도별 유효 코드가 다르다 -- 반대 side 코드를 조용히 통과시키지 않는다(잘못된
            # 신용 종류로 체결되면 상환·이자 구조가 달라진다).
            valid = _CREDIT_BUY_TYPES if self.side == "buy" else _CREDIT_SELL_TYPES
            if self.credit_type not in valid:
                raise KISUsageError(
                    f"{self.side} 신용주문의 credit_type 은 {sorted(valid)} 중 하나여야 한다: "
                    f"{self.credit_type!r}"
                )
            # 대출일자 규칙은 신규/상환으로 갈린다: 상환은 대상 대출을 지정해야 하므로 loan_date 필수,
            # 신규는 개시일(오늘)로 채운다 -- 생성 시점에 확정해 지문·와이어가 순수해지도록 한다.
            if self.credit_type in _CREDIT_REPAY_TYPES:
                if self.loan_date is None:
                    raise KISUsageError(
                        "상환 신용주문(credit_type 25/26/27/28)은 대상 대출의 loan_date(YYYYMMDD)가 "
                        "필요하다."
                    )
            elif self.loan_date is None:  # 신규 신용 -- 개시일 = 오늘(KST)
                object.__setattr__(self, "loan_date", f"{datetime.now(_KST):%Y%m%d}")

    @property
    def fingerprint(self) -> Fingerprint:
        """이 주문의 요청 지문 -- 같은 ``client_order_id`` 를 *다른* 주문에 재사용했는지
        판별하는 데 쓴다(멱등 replay 는 지문이 같을 때만 허용). 수치는 와이어와 **같은**
        정본(:func:`format_wire_decimal`)으로 -- 그래야 ``10`` 과 ``1E1`` 이 같은 지문이 된다.
        """
        return Fingerprint(
            symbol=self.symbol, side=self.side, order_type=self.order_type,
            quantity=format_wire_decimal(self.quantity),
            limit_price="" if self.limit_price is None else format_wire_decimal(self.limit_price),
            stop_price="" if self.stop_price is None else format_wire_decimal(self.stop_price),
            time_in_force=self.time_in_force, exchange=self.exchange,
            credit_type=self.credit_type or "", loan_date=self.loan_date or "",
        )

    # --- 타입별 생성자(권장 진입점) ------------------------------------
    @classmethod
    def _build(
        cls, symbol: str, side: Side, order_type: OrderType, quantity: object, *,
        limit_price: object | None = None, stop_price: object | None = None,
        time_in_force: TimeInForce = "day", exchange: str = "XKRX",
        credit_type: CreditType | None = None, loan_date: str | None = None,
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
                credit_type=credit_type, loan_date=loan_date,
            )
        return cls(
            symbol, side, order_type, quantity_dec,
            limit_price=limit_dec, stop_price=stop_dec,
            time_in_force=time_in_force, exchange=exchange,
            credit_type=credit_type, loan_date=loan_date,
            client_order_id=client_order_id,
        )

    @classmethod
    def credit(
        cls, symbol: str, *, side: Side, quantity: object, credit_type: CreditType,
        price: object | None = None, loan_date: str | None = None,
        time_in_force: TimeInForce = "day", client_order_id: str | None = None,
    ) -> Order:
        """국내 신용(융자/대주) 주문 -- ``price`` 를 주면 지정가, 없으면 시장가. ``credit_type`` 은
        매수/매도별 신용유형(매수 21/23/26/28, 매도 22/24/25/27).

        ``loan_date``(YYYYMMDD)는 대출일자다: **상환**유형(25/26/27/28)은 상환 대상 대출을 지정해야
        하므로 필수, **신규**유형(21/22/23/24)은 개시일이라 생략하면 생성 시점의 오늘(KST)로 채운다.
        신용주문은 국내(XKRX)만 가능하다."""
        order_type: OrderType = "limit" if price is not None else "market"
        return cls._build(
            symbol, side, order_type, quantity, limit_price=price,
            credit_type=credit_type, loan_date=loan_date,
            time_in_force=time_in_force, client_order_id=client_order_id,
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
