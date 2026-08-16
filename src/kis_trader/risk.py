"""주문 전 리스크 한도(pre-trade risk) -- :class:`RiskLimits`.

``KISClient(risk=RiskLimits(...))`` 로 주입하면 모든 :meth:`~kis_trader.domestic.stock.DomesticStock.buy` / :meth:`~kis_trader.domestic.stock.DomesticStock.sell`
가 와이어에 닿기 전에 이 한도를 통과해야 한다. 어기면 :class:`~kis_trader.errors.PreTradeRiskError`
로 막혀 주문은 전송되지 않는다(fat-finger 방지).

**왜 이 한도만 두는가.** KIS 서버가 이미 막는 것(상하한가 +/-30%, 주문가능금액, 매도가능수량)은
여기서 다시 구현하지 않는다 -- 클라이언트 재구현은 (a) 경합(조회 시점과 체결 시점의 값이 다름),
(b) 이중 구현, (c) 오확신(서버 규칙과 어긋난 로컬 규칙)을 부른다. 그런 거부는 명확한 에러로
올라온다. 여기 한도는 KIS가 *막지 않는* 진짜 오주문을 잡는다: 상하한가 안이라도 자리 하나 잘못
누른 수량/금액/가격.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TypeAlias, cast

from .errors import KISUsageError, PreTradeRiskError
from .order import Order

#: 금액/비율 한도의 입력 허용형. 생성자는 ``int``/``str``/``Decimal`` 로 받아 __post_init__
#: 에서 ``Decimal`` 로 정규화해 저장한다(저장값은 항상 ``Decimal | None``). 이 별칭은 *입력*
#: 계약만 이름 붙인 것이고, 필드 주석이 곧 생성자 인자 주석이라 여기까지가 정직하게 좁힐 수
#: 있는 한계다(저장형까지 좁히려면 ``init=False``/property 로 생성 동작이 바뀐다).
DecimalInput: TypeAlias = Decimal | int | str

# KRX 주식 호가가격단위 (2023-01-25 개정, KOSPI/KOSDAQ 통일). (가격 하한, 호가단위) 오름차순.
# 가격 p 의 단위 = p 이상인 마지막 하한의 단위(경계는 [하한, 다음 하한)). 이 표는 규정 개정으로
# 바뀔 수 있어 -- 틀린 표는 정상 주문을 오거부하므로 -- tick 검증은 enforce_tick_size 로 명시
# opt-in 일 때만 적용한다.
_KRX_TICK_TABLE: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal(0),       Decimal(1)),
    (Decimal(2_000),   Decimal(5)),
    (Decimal(5_000),   Decimal(10)),
    (Decimal(20_000),  Decimal(50)),
    (Decimal(50_000),  Decimal(100)),
    (Decimal(200_000), Decimal(500)),
    (Decimal(500_000), Decimal(1_000)),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskLimits:
    """buy/sell 전에 자동 점검할 사전 리스크 한도(설정형). 지정한 항목만 검사한다.

    금액/비율 필드는 ``int``/``str``/``Decimal`` 로 줄 수 있고(예: ``10``, ``"0.5"``) 생성 시
    ``Decimal`` 로 정규화되어 저장된다 -- 생성 후 읽으면 항상 ``Decimal | None`` 이다. 키워드
    전용 생성자라 같은 타입 인자를 자리로 바꿔 넣는 실수를 막는다.

    - ``max_order_quantity``: 1주문 최대 수량(초과 시 거부).
    - ``max_order_notional``: 1주문 최대 금액(단가 x 수량). 지정가는 지정가로, 시장가는
      현재가를 참조로 계산한다 -- 참조가를 얻지 못하거나 0이면 확인 불가로 보고 주문을 막는다
      (fail-closed).
    - ``price_collar_percent``: 지정가가 현재가에서 벗어날 수 있는 최대 %(예: ``10`` 이면
      +/-10%). KIS 상하한가(+/-30%) 안이라도 오타를 잡는 진짜 fat-finger 가드다. 지정가에만
      적용(시장가는 지정 가격이 없어 대상 아님).
    - ``enforce_tick_size``: 지정가/스탑가가 KRX 호가단위(원)의 배수인지 검사(기본 off --
      호가단위 표가 규정으로 바뀔 수 있어 명시 opt-in).
    """

    max_order_quantity: int | None = None
    max_order_notional: DecimalInput | None = None
    price_collar_percent: DecimalInput | None = None
    enforce_tick_size: bool = False

    def __post_init__(self) -> None:
        # 한도 자체를 검증/정규화한다(양수, Decimal). frozen 이라 object.__setattr__ 로 다시 쓴다.
        if self.max_order_quantity is not None:
            # bool 은 int 서브클래스라 True 가 수량 1 로 새어 든다 -- int 검사보다 먼저 명시 거부.
            if isinstance(self.max_order_quantity, bool):
                raise KISUsageError(f"max_order_quantity 는 정수여야 한다(bool 불가): {self.max_order_quantity!r}")
            if self.max_order_quantity <= 0:
                raise KISUsageError(f"max_order_quantity 는 양의 정수여야 한다: {self.max_order_quantity}")
        if self.max_order_notional is not None:
            object.__setattr__(
                self, "max_order_notional",
                _as_positive_decimal(self.max_order_notional, "max_order_notional"),
            )
        if self.price_collar_percent is not None:
            object.__setattr__(
                self, "price_collar_percent",
                _as_positive_decimal(self.price_collar_percent, "price_collar_percent"),
            )

    def _needs_reference_price(self, order: Order) -> bool:
        """이 주문+한도 조합이 현재가 참조를 필요로 하는가 -- 지정가 collar, 또는 자체 가격이
        없는 주문(시장가)의 notional 한도. 필요할 때만 :meth:`check` 전에 시세를 조회하게 한다."""
        return (
            (self.price_collar_percent is not None and order.limit_price is not None)
            or (self.max_order_notional is not None and _own_price(order) is None)
        )

    def check(self, order: Order, *, reference_price: Decimal | None = None) -> None:
        """주문이 한도를 지키는지 확인한다. 어기면 :class:`~kis_trader.errors.PreTradeRiskError`.

        ``reference_price`` 는 collar 비교와 자체 가격 없는 주문의 notional 계산에 쓰는 현재가다
        (:meth:`needs_reference_price` 가 True 면 호출자가 채워 준다). 검사에 참조가 필요한데
        없으면 확인 불가로 보고 거부한다(fail-closed -- 못 지킨 한도를 지킨 척하지 않는다).
        """
        if self.max_order_quantity is not None and order.quantity > self.max_order_quantity:
            raise PreTradeRiskError(
                f"수량 {order.quantity} 가 1주문 한도 {self.max_order_quantity} 를 초과한다."
            )
        if self.max_order_notional is not None:
            own_price = _own_price(order)
            price = own_price if own_price is not None else reference_price
            if price is None or price <= 0:
                raise PreTradeRiskError(
                    "notional 한도를 확인할 유효한 가격이 없다(시장가인데 현재가 참조가 없거나 0). 주문 중단."
                )
            notional = price * order.quantity
            # __post_init__ 이 Decimal 로 정규화한 값(입력형 DecimalInput 는 생성자 표면일 뿐).
            if notional > cast(Decimal, self.max_order_notional):
                raise PreTradeRiskError(
                    f"주문금액 {notional} 가 1주문 한도 {self.max_order_notional} 를 초과한다."
                )
        if self.price_collar_percent is not None and order.limit_price is not None:
            if reference_price is None or reference_price <= 0:
                raise PreTradeRiskError(
                    "가격 collar 를 확인할 현재가가 없다(시세 조회 실패/0). 주문 중단."
                )
            deviation = abs(order.limit_price - reference_price) / reference_price * 100
            if deviation > cast(Decimal, self.price_collar_percent):
                raise PreTradeRiskError(
                    f"지정가 {order.limit_price} 가 현재가 {reference_price} 에서 {deviation:.1f}% "
                    f"벗어나 collar {self.price_collar_percent}% 를 초과한다."
                )
        if self.enforce_tick_size:
            for label, price in (("limit_price", order.limit_price), ("stop_price", order.stop_price)):
                if price is None:
                    continue
                tick = _krx_equity_tick(price)
                if price % tick != 0:
                    raise PreTradeRiskError(
                        f"{label} {price} 가 KRX 호가단위 {tick} 의 배수가 아니다."
                    )


def _own_price(order: Order) -> Decimal | None:
    """주문 자체가 지닌 가격 -- 지정가는 ``limit_price``, 스탑은 ``stop_price``, 시장가는 없음."""
    if order.limit_price is not None:
        return order.limit_price
    if order.stop_price is not None:
        return order.stop_price
    return None


def _krx_equity_tick(price: Decimal) -> Decimal:
    """가격대별 KRX 주식 호가단위(원). 표 경계는 [하한, 다음 하한)."""
    tick = _KRX_TICK_TABLE[0][1]
    for lower, unit in _KRX_TICK_TABLE:
        if price >= lower:
            tick = unit
        else:
            break
    return tick


def _as_positive_decimal(value: object, name: str) -> Decimal:
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as err:
        raise KISUsageError(f"{name} 는 숫자여야 한다: {value!r}") from err
    # 비유한값(Infinity/NaN)은 그 한도가 켜는 검사 자체를 무력화한다("inf"/"nan" 문자열도
    # Decimal('Infinity')/NaN 으로 coerce 되므로 여기서 걸러야 한다). NaN 은 dec <= 0 이 False 라
    # 아래 양수 검사도 통과해 버리므로 반드시 그 앞에서 막는다.
    if not dec.is_finite():
        raise KISUsageError(f"{name} 는 유한한 값이어야 한다(무한/NaN 불가): {value!r}")
    if dec <= 0:
        raise KISUsageError(f"{name} 는 양수여야 한다: {dec}")
    return dec
