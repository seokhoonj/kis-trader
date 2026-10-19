"""주문 실행 엔진 -- 즉시/예약 발주·정정·취소·재조회의 자산별 라우팅과 정책.

:class:`~kis_trader.client.KISClient` 에서 분리한 안전 주문 코어의 오케스트레이터다. 세션이 전송·저장소·
환경·주문가능·리스크·계좌해석(``require_account``)을 주입해 구성하고, ``KISClient`` 는 이 엔진에 얇게
위임한다(세션은 구성 루트, 주문 정책은 엔진). 이중체결 방지·타임아웃 재시도 금지·보수적 재조회는 각
자산 엔진 함수가 그대로 보장하고, 이 계층은 지문/거래소로 자산별 와이어 빌더를 고르는 라우팅만 한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from decimal import Decimal
from typing import TYPE_CHECKING

from . import _order_core
from .domestic._engine import bond_orders as bond_orders_engine
from .domestic._engine import derivative_orders as derivative_orders_engine
from .domestic._engine import orders as orders_engine
from .domestic._engine import reserved_orders as reserved_orders_api
from .errors import KISUsageError
from .order import (
    AlgoStrategy,
    ChangeAction,
    ChangeActionFingerprint,
    ImmediateOrderFingerprint,
    Order,
    OrderType,
    ReservedOrderFingerprint,
    Side,
    coerce_decimal,
    mint_client_order_id,
)
from .overseas._engine import derivative_orders as overseas_deriv_orders_engine
from .overseas._engine import orders as overseas_orders_engine
from .overseas._engine import reserved_orders as overseas_reserved_orders_api

if TYPE_CHECKING:
    from ._literals import Numeric
    from .report import ExecutionReport
    from .risk import RiskLimits
    from .store import OrderStore
    from .transport import Environment, Transport


def _reject_unsupported_derivative_risk(risk: RiskLimits) -> None:
    """파생(XKFE) 발주에 켜진 리스크 한도 중 지원하지 않는 것을 fail-closed 로 거부한다.

    파생이 지원하는 한도는 ``max_order_quantity`` 하나뿐이다 -- 참조가 없이 순수 수량만 보므로.
    나머지 한도(notional/collar/tick)는 국내 *주식* 시세를 참조가·호가단위로 삼아 파생엔 의미가
    없다. 지원 목록을 blocklist(특정 이름 나열)가 아니라 **allowlist**(``max_order_quantity`` 만
    허용)로 두어, 앞으로 :class:`~kis_trader.risk.RiskLimits` 에 필드가 추가돼도 조용히 국내 주식
    참조가 조회로 새지 않고 기본적으로 fail-closed 되게 한다. 켜진(비-None, 비-False) 다른 한도가
    있으면 조용히 건너뛰지 않고 명확히 :class:`KISUsageError` 로 막는다."""
    active_unsupported = [
        f.name for f in fields(risk)
        if f.name != "max_order_quantity" and getattr(risk, f.name) not in (None, False)
    ]
    if active_unsupported:
        raise KISUsageError(
            "파생(XKFE) 주문엔 max_order_quantity 리스크 한도만 지원한다 -- 지원하지 않는 한도가 "
            f"켜져 있다: {', '.join(active_unsupported)}. 국내 주식 시세 기반 검사(참조가·호가단위)라 "
            "파생엔 의미가 없어 거부한다."
        )


#: 국내 시세 기반 사전 리스크 러너를 쓰는 lane(region, asset_class). 해외주식은 전용 러너(아래),
#: 그 밖(채권·해외선물옵션)은 애초에 risk 세션에서 거부되어 러너를 요청하지 않는다.
_DOMESTIC_RISK_LANES = frozenset({("domestic", "stock"), ("domestic", "derivative")})


def _risk_runner_for_lane(
    lane: tuple[str, str],
) -> Callable[[Transport, Order, RiskLimits], None]:
    """lane(region, asset_class) -> 그 lane 의 사전 리스크 러너. 해외주식은 거래소별 해외 현재가 +
    리스크 통화별 금액 한도 러너, 국내 주식/파생은 국내 시세 러너. 발주(place)와 정정(modify)이 같은
    lane->러너 매핑을 공유하도록 한곳에 둔다(두 경로가 매핑을 따로 적지 않게). 알 수 없는 lane 은
    조용히 국내 러너로 흘려보내지 않고 명확히 거부한다(미래 lane 이 엉뚱한 국내 참조가 검사를 받지
    않도록 fail-closed)."""
    if lane == ("overseas", "stock"):
        return overseas_orders_engine.run_pre_trade_risk
    if lane in _DOMESTIC_RISK_LANES:
        return orders_engine.run_pre_trade_risk
    raise KISUsageError(f"사전 리스크 러너가 없는 주문 lane: {lane}")


class OrderEngine:
    """세션이 쥔 전송·저장소·환경·주문가능·리스크·계좌해석으로 안전 주문 코어를 구동한다.

    ``require_account`` 는 (계좌번호, 상품코드)를 돌려주거나 계좌 미설정이면 :class:`~kis_trader.errors.
    KISUsageError` 를 올리는 세션 콜러블이다(계좌 조회 표면과 한 소스). ``KISClient`` 가 구성해 쥐고
    ``_place_order``/``_change_order``/``_reconcile``/예약 발주를 이 엔진에 위임한다."""

    def __init__(
        self, *, transport: Transport, store: OrderStore, environment: Environment,
        orderable: bool, risk: RiskLimits | None, require_account: Callable[[], tuple[str, str]],
    ) -> None:
        self._transport = transport
        self._store = store
        self._environment = environment
        self._orderable = orderable
        self._risk = risk
        self._require_account = require_account

    def place_order(self, order: Order) -> ExecutionReport:
        """주문을 안전 엔진에 넘겨 전송한다(종목 핸들 buy/sell 이 호출). 계좌 정보 필요.

        국내/해외 모두 같은 안전 코어(이중체결 방지·재시도 금지)를 쓰되, 와이어 요청 조립기만
        시장별로 바꾼다. 사전 리스크 게이트도 lane 별로 주입한다 -- 국내주식은 국내 시세 기반
        runner, 해외주식은 거래소별 해외 현재가와 결제통화별 금액 한도 runner 를 타, 두 lane 모두
        같은 fat-finger 보호(수량·금액·collar)를 받는다. 채권·파생(국내/해외)은 참조가 기반 검사가
        의미 없어 risk 세션에서 거부한다."""
        cano, product_code = self._require_account()
        build_request: _order_core.PlaceRequestBuilder | None = None
        extract_output = None
        risk = self._risk
        match (order.region, order.asset_class):
            case ("overseas", "stock"):
                if order.algo_strategy is not None and self._environment == "paper":
                    raise KISUsageError("미국주식 algo(TWAP/VWAP) 주문은 모의투자 미지원 -- 실전에서만.")
                build_request = (
                    overseas_orders_engine.make_overnight_order_request
                    if order.session == "overnight"
                    else overseas_orders_engine.make_order_request
                )
            case ("domestic", "derivative"):
                if order.session == "night" and self._environment == "paper":
                    raise KISUsageError("파생 야간(STTN)은 모의투자 미지원 -- 실전에서만.")
                if risk is not None:
                    _reject_unsupported_derivative_risk(risk)
                build_request = derivative_orders_engine.make_order_request
                extract_output = derivative_orders_engine.extract_output
            case ("domestic", "bond"):
                if self._environment == "paper":
                    raise KISUsageError("장내채권 주문은 모의투자 미지원 -- 실전에서만.")
                if risk is not None:
                    raise KISUsageError(
                        "장내채권 주문엔 사전 리스크 게이트가 미지원이다(참조가 기반 검사가 채권엔 의미 "
                        "없음) -- risk 없는 세션에서 내거나 국내 주식 주문에만 risk 를 쓰라."
                    )
                build_request = bond_orders_engine.make_order_request
            case ("overseas", "derivative"):
                if self._environment == "paper":
                    raise KISUsageError("해외선물옵션 주문은 모의투자 미지원 -- 실전에서만.")
                if risk is not None:
                    raise KISUsageError(
                        "해외선물옵션 주문엔 사전 리스크 게이트가 미지원이다(참조가 기반 검사가 의미 "
                        "없음) -- risk 없는 세션에서 내거나 국내 주식 주문에만 risk 를 쓰라."
                    )
                build_request = overseas_deriv_orders_engine.make_order_request
                extract_output = overseas_deriv_orders_engine.extract_output
            case ("domestic", "stock"):
                if order.credit_type is not None:
                    build_request = orders_engine.make_credit_order_request
            case _:
                raise KISUsageError(
                    f"지원하지 않는 주문 lane: region={order.region!r}, "
                    f"asset_class={order.asset_class!r}"
                )
        # 안전 코어(_order_core.place)는 지역 중립이라 와이어 빌더를 필수로 주입받는다 -- 국내주식
        # 비신용 lane 은 여기서 명시적으로 현금 빌더를 넘긴다(코어에 국내 기본값을 두지 않는다).
        if build_request is None:
            build_request = orders_engine._make_order_cash_request
        # 사전 리스크 점검을 lane 별 콜러블로 주입한다 -- 국내 lane 은 국내 시세 기반 runner,
        # 해외주식 lane 은 거래소별 해외 현재가 + 결제통화별 한도 runner (그 외 lane 은 위에서 이미 거부).
        if risk is None:
            pre_trade_risk = None
        else:
            runner = _risk_runner_for_lane((order.region, order.asset_class))
            pre_trade_risk = (
                lambda checked_order: runner(self._transport, checked_order, risk)
            )
        return _order_core.place(
            self._transport, self._store, order,
            cano=cano, product_code=product_code, environment=self._environment,
            orderable=self._orderable, build_request=build_request,
            extract_output=extract_output, pre_trade_risk=pre_trade_risk,
        )

    def change_order(
        self, client_order_id: str, *, action: ChangeAction, quantity: Numeric | None,
        limit_price: Numeric | None, request_id: str | None,
    ) -> ExecutionReport:
        """접수된 주문의 미체결 수량을 취소/정정한다(``kis.orders.cancel`` / ``.modify``).

        국내·해외 주식, 국내 파생(XKFE), 국내 장내채권(BOND), 해외선물옵션(OSFO) 즉시주문과 아시아
        해외예약(취소 전용)을 모두 처리한다 -- ``fingerprint`` 의 거래소/세션으로 자산별 정정·취소 와이어
        빌더를 골라 공유 안전 코어(:func:`~kis_trader._order_core.submit_change`)에 넘긴다."""
        cano, product_code = self._require_account()
        # (지문, 리포트)를 한 락으로 원자 스냅샷 -- 잔량 계산이 둘의 세대 일관성에 의존하므로, 따로
        # 읽다 동시 정정/취소의 리바인드가 끼어들면 옛 지문 + 새 리포트로 잘못된 잔량이 나갈 수 있다.
        binding = self._store.binding_for(client_order_id)
        if binding is None:
            raise KISUsageError(f"확정된 원주문을 찾을 수 없다: {client_order_id!r}")
        report, fingerprint = binding
        # 이미 종료된 주문(체결/취소/거부/만료)은 정정·취소 대상이 아니다 -- 와이어/조회 전에 거부한다.
        # 특히 재조회로 확정된 종료 리포트는 쓸 수 있는 ODNO 가 없을 수 있어(order_id=None), 밑에서
        # str(order_id) 로 "None" 을 조회·전송하는 대신 여기서 명확히 막는다.
        if report.is_terminal:
            raise KISUsageError(
                f"이미 종료된 주문({report.status.value})은 정정·취소할 수 없다: {client_order_id!r}."
            )
        # 아시아 해외예약은 전용 정정·취소 엔드포인트가 없어 발주 TR 에 원주문 전체를 재전송하는
        # 취소만 가능하다 -- store 가 쥔 지문/리포트로 엔진이 body 를 복원하므로 여기서 위임한다.
        if isinstance(fingerprint, ReservedOrderFingerprint) and \
                fingerprint.exchange == "overseas-reserved-asia":
            if action != "cancel":
                raise KISUsageError("아시아 해외예약주문은 정정 미지원 -- 취소 후 재발주하라.")
            if quantity is not None:
                raise KISUsageError(
                    "아시아 해외예약 취소는 전량만 가능(부분 취소 미지원) -- quantity 를 생략하라."
                )
            return overseas_reserved_orders_api.cancel_asia_reserved_order(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
            )
        if isinstance(fingerprint, ReservedOrderFingerprint):
            raise KISUsageError(
                "예약주문 정정·취소는 kis order cancel-reserved 로 하라."
            )
        original_quantity = Decimal(fingerprint.quantity)
        remaining_quantity = original_quantity - report.filled_quantity
        # 정정 경로도 발주(place)와 같은 수치 강제변환을 거쳐 NaN/Infinity 등 비유한 입력을 fail-closed
        # 로 막는다(raw Decimal(str(...)) 는 "nan"/"inf" 를 통과시켜 와이어에 실릴 수 있다).
        change_quantity = remaining_quantity if quantity is None else coerce_decimal(quantity, "quantity")
        change_limit_price = None if limit_price is None else coerce_decimal(limit_price, "limit_price")
        # 해외는 부분 취소·정정 메커니즘이 없다(라이브 확인) -- 취소는 부분 수량을 조용히 무시하고
        # 전량 취소하고, 정정은 부분 수량을 거부한다. 잔량 전체가 아닌 변경은 와이어에 닿기 전에
        # fail-closed 로 막아, 사용자가 '부분 취소했다'고 오인하는 일이 없게 한다(전량만 지원).
        if overseas_orders_engine.is_overseas_exchange(fingerprint.exchange) and \
                change_quantity != remaining_quantity:
            raise KISUsageError(
                "해외는 부분 취소·정정을 지원하지 않는다(전량만 가능) -- quantity 를 생략해 "
                "잔량 전체를 취소·정정하라."
            )
        builder: _order_core.ChangeRequestBuilder | None = None
        if overseas_orders_engine.is_overseas_exchange(fingerprint.exchange):
            builder = (
                overseas_orders_engine.make_overnight_change_request
                if isinstance(fingerprint, ImmediateOrderFingerprint) and fingerprint.session == "overnight"
                else overseas_orders_engine.make_change_request
            )
        elif isinstance(fingerprint, ImmediateOrderFingerprint) and \
                (fingerprint.region, fingerprint.asset_class) == ("domestic", "derivative"):
            # 국내 파생(XKFE) 정정·취소는 파생 전용 와이어(order-rvsecncl, ORGN_ODNO only)로 조립한다.
            if fingerprint.session == "night":
                # 야간(STTN)은 실전 전용 -- paper 야간 지문 도달은 손상 신호라 와이어 전에 fail-closed.
                if self._environment == "paper":
                    raise KISUsageError(
                        "파생 야간(STTN) 정정·취소는 모의투자 미지원 -- 실전에서만."
                    )
                # 야간은 부분 정정·취소가 불가(잔량 전체가 대상)라, 호출자가 명시 quantity 를 줬는데
                # 그게 로컬 잔량과 다르면(부분 의도) 조용히 전량을 건드리지 않고 와이어 전에 거부한다
                # -- 해외 슬라이스와 같은 태도(조회조차 하기 전에 막아 와이어에 닿지 않는다).
                if quantity is not None and change_quantity != remaining_quantity:
                    raise KISUsageError(
                        "파생 야간은 부분 정정·취소 미지원 -- quantity 를 생략해 전량으로 하라"
                    )
                # ORD_QTY 에 실잔량이 필수라, 로컬 리포트의 stale 잔량 대신 inquire-ngt-ccnl 로 신선
                # 잔량을 조회해 주입한다(읽기 -- claim 전). 날짜창 앵커는 접수(recorded) 일자다 -- 이
                # 주문은 이미 확정돼 in-flight claim 시각이 없고(claim_time_for -> None -> 뒷방향
                # 폴백창), 야간 주문일자는 T+1 이라 뒷방향 창엔 안 들어와 0행 KISError 로 취소가
                # 와이어에 닿지 못한다. 접수 일자 앵커가 T+1 을 덮는 전방창을 만든다. 조회 실패/0행/
                # 다행이면 여기서 fail-closed 로 올라가 취소 와이어에 닿지 않는다.
                change_quantity = derivative_orders_engine.fetch_night_remaining(
                    self._transport, order_id=str(report.order_id), symbol=fingerprint.symbol,
                    cano=cano, product_code=product_code, environment=self._environment,
                    anchor=report.recorded_at.isoformat(),
                )
                builder = derivative_orders_engine.make_night_change_request
            else:
                # 주간 파생 '취소' 와이어(order-rvsecncl, RVSE_CNCL=02)는 ORD_QTY=0(전량) 고정이라
                # 부분취소 개념이 없다. 호출자가 명시 quantity 를 줬는데 로컬 잔량과 다르면(부분 의도)
                # 조용히 전량취소하지 않고 와이어 전에 거부한다 -- 야간/OSFO/해외와 같은 fail-closed.
                # (정정은 수량 변경을 지원하므로 취소에만 적용한다.)
                if action == "cancel" and quantity is not None and change_quantity != remaining_quantity:
                    raise KISUsageError(
                        "파생 주간 취소는 부분취소 미지원 -- quantity 를 생략해 전량취소하라"
                    )
                builder = derivative_orders_engine.make_change_request
        elif isinstance(fingerprint, ImmediateOrderFingerprint) and \
                (fingerprint.region, fingerprint.asset_class) == ("domestic", "bond"):
            # 국내 장내채권(BOND) 정정·취소는 실전 전용 -- paper 지문 도달은 손상 신호라 와이어 전에
            # fail-closed. 채권은 부분 정정·취소를 지원(ORD_QTY2)하므로 전량 강제는 하지 않는다.
            if self._environment == "paper":
                raise KISUsageError("장내채권 정정·취소는 모의투자 미지원 -- 실전에서만.")
            builder = bond_orders_engine.make_change_request
        elif isinstance(fingerprint, ImmediateOrderFingerprint) and \
                (fingerprint.region, fingerprint.asset_class) == ("overseas", "derivative"):
            # 해외선물옵션(OSFO) 정정·취소는 실전 전용 -- paper 지문 도달은 손상 신호라 와이어 전에
            # fail-closed. 이 와이어는 전량 정정·취소만 지원(부분 수량 개념 없음)하므로, 호출자가 명시
            # quantity 를 줬는데 로컬 잔량과 다르면(부분 의도) 조용히 전량을 건드리지 않고 거부한다
            # -- 파생 야간 슬라이스와 같은 태도(와이어에 닿기 전에 막는다).
            if self._environment == "paper":
                raise KISUsageError("해외선물옵션 정정·취소는 모의투자 미지원 -- 실전에서만.")
            if quantity is not None and change_quantity != remaining_quantity:
                raise KISUsageError(
                    "해외선물옵션은 부분 정정·취소를 지원하지 않는다(전량만 가능) -- quantity 를 생략하라."
                )
            builder = overseas_deriv_orders_engine.make_change_request
        # 정정(modify)도 발주(place)와 같은 사전 리스크 게이트를 통과한다 -- 정정은 수량을 늘리거나
        # 가격을 밴드 밖으로 옮겨 익스포저를 키울 수 있어서다. 취소(cancel)는 익스포저를 줄이므로
        # 게이트하지 않는다(위험한 주문을 못 지우면 오히려 위험). risk 세션은 국내주식(전 한도)·
        # 국내파생(max_order_quantity 만, allowlist)·해외주식(거래소별 현재가 + 결제통화별 금액 한도)
        # 을 지원한다 -- 모두 정정에서 수량·가격으로 익스포저를 키울 수 있으므로 게이트한다. 채권·
        # 해외선물옵션은 같은 세션 place 가 거부하지만, 지속 store 를 공유하면 risk 없는 세션이 낸
        # 주문을 risk 세션이 정정할 수 있으므로, 게이트 불가 lane 은 place 와 동일하게 fail-closed 로
        # 거부한다(조용히 ungated 통과 금지).
        if self._risk is not None and action == "modify" \
                and isinstance(fingerprint, ImmediateOrderFingerprint):
            lane = (fingerprint.region, fingerprint.asset_class)
            effective_price = (
                change_limit_price if change_limit_price is not None
                else (coerce_decimal(fingerprint.limit_price, "limit_price")
                      if fingerprint.limit_price else None)
            )
            # 정정은 가격을 주면 사실상 그 가격의 지정가가 된다. 원주문의 order_type(최유리=market,
            # 스톱 등)을 그대로 베끼면 Order(order_type="market", limit_price=<새 가격>) 이 __post_init__
            # 에서 거부돼(= market 에 limit_price 금지) risk 세션에서 모든 비-지정가 정정이 불가능해진다.
            # 그래서 가격이 있으면 probe 는 "limit"(+그 가격, stop 없음), 가격이 없으면(수량만 정정)
            # 원 order_type 과 원 stop_price 를 그대로 둔다. probe 는 run_pre_trade_risk(수량/명목/collar)용.
            risk_probe_order_type: OrderType
            risk_probe_stop_price: Decimal | None
            if effective_price is not None:
                risk_probe_order_type, risk_probe_stop_price = "limit", None
            else:
                risk_probe_order_type = fingerprint.order_type
                risk_probe_stop_price = (coerce_decimal(fingerprint.stop_price, "stop_price")
                              if fingerprint.stop_price and fingerprint.stop_price != "0" else None)
            risk_probe: Order
            if lane == ("domestic", "stock"):
                # risk.check 는 symbol/quantity/limit_price/stop_price 와 region/asset_class(tick 게이트)를
                # 읽는다. 국내주식 lane 이 의도값(Order 기본값).
                risk_probe = Order(
                    symbol=fingerprint.symbol, side=fingerprint.side,
                    order_type=risk_probe_order_type, quantity=change_quantity,
                    limit_price=effective_price, stop_price=risk_probe_stop_price,
                )
            elif lane == ("domestic", "derivative"):
                # 파생은 max_order_quantity 만 지원(place 와 동일 allowlist) -- 나머지 한도가 켜져 있으면
                # fail-closed 로 거부한다. reject 뒤엔 참조가가 필요한 한도가 없어(needs_reference_price
                # False) 국내주식 시세를 조회하지 않고 수량 캡만 본다. modify 가 캡을 우회하지 못하게.
                _reject_unsupported_derivative_risk(self._risk)
                risk_probe = Order(
                    symbol=fingerprint.symbol, side=fingerprint.side,
                    order_type=risk_probe_order_type, quantity=change_quantity,
                    limit_price=effective_price, stop_price=risk_probe_stop_price,
                    region="domestic", asset_class="derivative",
                )
            elif lane == ("overseas", "stock"):
                # 해외주식 정정도 발주와 동일 게이트 -- 거래소별 해외 현재가 + 결제통화별 금액 한도.
                # 지문의 거래소코드(EXCD)로 통화를 판별한다. 타 세션 store 주문의 ungated 통과도 막는다.
                risk_probe = Order(
                    symbol=fingerprint.symbol, side=fingerprint.side,
                    order_type=risk_probe_order_type, quantity=change_quantity,
                    limit_price=effective_price, stop_price=risk_probe_stop_price,
                    region="overseas", asset_class="stock", exchange=fingerprint.exchange,
                )
            else:
                # 채권·해외선물옵션 lane -- place 와 동일하게 risk 세션에선 정정도 거부(참조가 기반
                # 검사가 의미 없어 미지원). 타 세션 store 주문의 ungated 통과를 막는다.
                raise KISUsageError(
                    "채권·해외선물옵션 주문엔 사전 리스크 게이트가 미지원이다 -- risk 없는 세션에서 "
                    "정정하거나 국내 주식/파생·해외 주식 주문에만 risk 를 쓰라."
                )
            # place_order 와 같은 lane->러너 매핑을 공유한다(국내 시세 러너 vs 해외 현재가+통화별 한도
            # 러너) -- 정정도 발주와 동일 게이트를 통과한다.
            _risk_runner_for_lane(lane)(self._transport, risk_probe, self._risk)
        # 지역 중립 코어는 와이어 빌더 필수 -- 국내주식 정정취소 lane(builder 미설정)은 여기서 명시적으로
        # 국내 빌더를 넘긴다(코어에 국내 기본값을 두지 않는다).
        if builder is None:
            builder = orders_engine._make_domestic_change_request
        return _order_core.submit_change(
            self._transport, self._store,
            original_client_order_id=client_order_id,
            request_id=request_id or mint_client_order_id(),
            action=action,
            quantity=change_quantity,
            limit_price=change_limit_price,
            cano=cano,
            product_code=product_code,
            environment=self._environment,
            build_request=builder,
            expected_original=binding,
        )

    def reconcile(self, client_order_id: str, *, refresh: bool = False) -> ExecutionReport | None:
        """미확인 주문(타임아웃 등)의 실제 상태를 브로커에 재조회한다 -- **보수적**. ``kis.orders.reconcile``.

        완료 리포트가 있으면 반환. in-flight 면 저장된 주문 종류에 따라 확인처를 고른다: 국내 즉시
        주문은 일별체결조회, 해외 주문은 해외 체결내역, 예약주문은 예약주문조회. 어느 쪽이든 정확히
        1건이면 확정, 모호(0/다건)하면 미접수로 단정하지 않는다(``None`` 또는 :class:`~kis_trader.
        errors.KISError`). 모르는 id 는 :class:`~kis_trader.errors.KISUsageError`. 재조회 자체가
        시간초과면 :class:`~kis_trader.errors.OrderTimeoutError`(in-flight 유지, 잠시 후 재시도).

        정정·취소(ChangeAction) id 를 주면 원주문의 **현재** 상태를 브로커에서 다시 조회해(캐시가
        아니라) 판단한다: 종료 상태 또는 정정 뒤 새 ODNO 로 확정되면 이 변경의 in-flight 를 해제하고
        원주문의 현재 리포트를 반환한다. 그 외 비종료 리포트, 모호(``None``), 재조회 실패는 해제하지
        않고 그대로 올린다(미확인을 확인으로 둔갑시키지 않는다 -- 이 경우 변경은 in-flight 로 남으니
        브로커에서 수동 확인하라). 원주문
        자산군이 reconcile 미지원(채권·해외선물옵션)이면 :class:`~kis_trader.errors.KISUsageError`.
        ``refresh=True`` 면 저장된 완료 리포트가 있어도 단락하지 않고 브로커를 다시 스캔해 **현재**
        상태를 돌려준다(체결 측정·사후확정용; 아래 ChangeAction 경로도 이 플래그로 원주문을 재스캔한다).
        전송 시간초과로 실패한 정정이 실제로는 성공했더라도 재조회가 대체된 주문을 항상 자동으로 다시
        확인할 수 없어 변경이 처리 중 상태로 남을 수 있으므로, HTS/MTS 또는 계좌 조회에서 확인하고
        그곳에서 주문을 계속 관리할 수 있습니다.
        """
        cano, product_code = self._require_account()
        # 해외 주문의 미확인(in-flight) 재조회는 국내 일별체결조회가 아니라 해외 체결내역으로 확인해야
        # 한다(엉뚱한 미접수 판정 방지) -- 지문의 거래소로 국내/해외 경로를 가른다. 완료 리포트가 있으면
        # 어느 엔진이든 그대로 반환한다.
        fingerprint = self._store.fingerprint_for(client_order_id)
        if isinstance(fingerprint, ChangeActionFingerprint):
            # 정정·취소의 결과는 원주문 상태에 반영된다(취소 성공이면 원주문 취소/소멸, 정정 성공이면
            # 수량·가격 변경 또는 새 ODNO). 그래서 이 변경 request_id 가 아니라 원주문을 재조회한다 --
            # 단, 원주문엔 접수 당시 저장 리포트가 항상 있으므로(submit_change 가 확정 원주문만 허용)
            # 그냥 reconcile(원) 을 부르면 캐시가 단락 반환돼 낡은 상태를 진실로 돌려주고 만다.
            # refresh 로 캐시를 건너뛰고 브로커를 실제로 다시 스캔한다:
            #   - 1건으로 확정(ExecutionReport)되면 변경이 반영된 현재 진실이므로 이 변경의 in-flight
            #     를 해제하고(데드락 해소) 그 리포트를 돌려준다.
            #   - 모호(0건 None: 예를 들어 정정으로 수량·단가가 바뀌어 원 지문과 더 안 맞음)하거나 재조회
            #     가 실패(타임아웃/다건 예외)하면 해제하지 않고 그대로 올린다 -- 확인 안 된 것을 확인된
            #     척하지 않는다(reconcile 이 유일한 사후 진실이고 캐시를 진실로 위장하지 않는다). 변경은
            #     in-flight 로 남고 사용자는 브로커(HTS/MTS)로 수동 확인한다.
            action = fingerprint.action
            pre_change_report = self._store.report_for(fingerprint.original_client_order_id)
            pre_change_order_id = pre_change_report.order_id if pre_change_report is not None else None
            try:
                fresh = self.reconcile(fingerprint.original_client_order_id, refresh=True)
            except KISUsageError:
                # 원주문 자산군(채권·해외선물옵션)이 reconcile 미지원 -> 변경 접수 여부도 자동 확인
                # 불가. 조용히 해제하지 않고(미확인을 확인으로 둔갑 금지) 수동 확인 경로를 안내한다.
                raise KISUsageError(
                    f"변경({client_order_id})의 원주문({fingerprint.original_client_order_id}) 자산군은 "
                    "reconcile 미지원이라 변경 접수 여부를 자동 확인할 수 없다 -- 계좌 조회로 수동 확인하라."
                ) from None
            if fresh is not None:
                confirmed = fresh.is_terminal
                if action == "modify" and not confirmed:
                    # 정정 성공 시 KIS 는 원주문에 새 ODNO 를 부여한다. reconcile 가 그 새 세대를
                    # 관측하면(order_id 변화) 정정 반영이 확정돼 해제한다. 재조회가 새 ODNO 행을
                    # 집지 못하면(원 ODNO 로만 매칭돼 None/옛 행) 확정 불가이므로 보수적으로 유지한다
                    # -- read-only 확인이라 와이어 동작을 바꾸지 않는다.
                    confirmed = bool(fresh.order_id) and fresh.order_id != pre_change_order_id
                if confirmed:
                    self._store.clear_in_flight(client_order_id)
            return fresh
        if isinstance(fingerprint, ReservedOrderFingerprint):
            # 예약주문은 일별체결이 아니라 예약주문조회로 확인한다(국내/해외 예약 경로를 exchange 로 가른다).
            if fingerprint.exchange == "overseas-reserved-asia":
                return overseas_reserved_orders_api.reconcile_asia_reserved_order(
                    self._transport, self._store, client_order_id,
                    cano=cano, product_code=product_code, environment=self._environment,
                    force_refresh=refresh,
                )
            if fingerprint.exchange == "overseas-reserved":  # bare "overseas-reserved" 태그 = 미국 예약
                return overseas_reserved_orders_api.reconcile_us_reserved_order(
                    self._transport, self._store, client_order_id,
                    cano=cano, product_code=product_code, environment=self._environment,
                    force_refresh=refresh,
                )
            return reserved_orders_api.reconcile_reserved_order(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
                force_refresh=refresh,
            )
        if isinstance(fingerprint, ImmediateOrderFingerprint) and \
                overseas_orders_engine.is_overseas_exchange(fingerprint.exchange):
            return overseas_orders_engine.reconcile(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
                force_refresh=refresh,
            )
        if isinstance(fingerprint, ImmediateOrderFingerprint) and \
                (fingerprint.region, fingerprint.asset_class) == ("domestic", "derivative"):
            # 국내 파생(XKFE) 미확인 주문은 국내주식 일별체결조회가 아니라 파생 일별체결내역으로 확인한다.
            return derivative_orders_engine.reconcile(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
                force_refresh=refresh,
            )
        if isinstance(fingerprint, ImmediateOrderFingerprint) and \
                (fingerprint.region, fingerprint.asset_class) == ("domestic", "bond"):
            # 국내 장내채권(BOND) 미확인 주문은 국내주식 일별체결조회로 확인할 수 없다(엉뚱한 테이블을
            # 조회해 체결 여부와 무관하게 None 을 돌려주는 잘못된 복구). 재조회 슬라이스는 미지원이라
            # fail-closed 한다 -- 이중체결 방지 장벽은 그대로다.
            raise KISUsageError(
                "장내채권 주문 재조회(reconcile)는 아직 미지원 -- 체결은 "
                "계좌 조회(kis.account.domestic.bonds.order_history / open_orders)로 수동 확인하라."
            )
        if isinstance(fingerprint, ImmediateOrderFingerprint) and \
                (fingerprint.region, fingerprint.asset_class) == ("overseas", "derivative"):
            # 해외선물옵션(OSFO) 미확인 주문은 국내주식 일별체결조회로 확인할 수 없다(엉뚱한 테이블을
            # 조회해 체결 여부와 무관하게 None 을 돌려주는 잘못된 복구). 재조회 슬라이스는 미지원이라
            # fail-closed 한다(채권 분기와 대칭) -- 이중체결 방지 장벽은 그대로다.
            raise KISUsageError(
                "해외선물옵션 주문 재조회(reconcile)는 아직 미지원 -- 체결은 kis.account(해외파생 "
                "조회)로 수동 확인하라."
            )
        return orders_engine.reconcile(
            self._transport, self._store, client_order_id,
            cano=cano, product_code=product_code, environment=self._environment,
            force_refresh=refresh,
        )

    def place_reserved_order(
        self, *, symbol: str, side: Side, quantity: Numeric, limit_price: Numeric | None,
        end_date: str | None, client_order_id: str | None,
    ) -> ExecutionReport:
        """예약주문을 예약 안전 엔진에 넘긴다(종목 핸들 reserve_buy/sell 이 호출). 계좌 정보 필요.

        즉시주문 안전 코어와 별개 흐름이되 dedup(OrderStore)·재시도 금지·보수적 재조회·주문가능 계좌
        가드는 공유한다. risk 게이트는 예약주문엔 적용하지 않는다(집행이 향후라 현재가 기준 참조가
        의미 없음)."""
        cano, product_code = self._require_account()
        return reserved_orders_api.place_reserved_order(
            self._transport, self._store,
            symbol=symbol, side=side, quantity=quantity, limit_price=limit_price, end_date=end_date,
            client_order_id=client_order_id or mint_client_order_id(), orderable=self._orderable,
            cano=cano, product_code=product_code, environment=self._environment,
        )

    def place_overseas_reserved_order(
        self, *, symbol: str, side: Side, quantity: Numeric, limit_price: Numeric, exchange: str,
        currency: str | None = None, algo_strategy: AlgoStrategy | None = None,
        client_order_id: str | None,
    ) -> ExecutionReport:
        """해외예약주문을 예약 안전 엔진에 넘긴다(종목 핸들 reserve_buy/sell 이 해외 종목일 때 호출).
        ``exchange`` 의 시장이 미국/아시아 와이어를 가르고, ``currency`` 는 홍콩(HKS) 예약의 상품유형
        선택 전용이다. ``algo_strategy``(twap/vwap)는 미국 예약주문의 알고리즘 분할(미국 실전 전용)이다."""
        cano, product_code = self._require_account()
        return overseas_reserved_orders_api.place_overseas_reserved_order(
            self._transport, self._store,
            symbol=symbol, side=side, quantity=quantity, limit_price=limit_price, exchange=exchange,
            currency=currency, algo_strategy=algo_strategy,
            client_order_id=client_order_id or mint_client_order_id(), orderable=self._orderable,
            cano=cano, product_code=product_code, environment=self._environment,
        )
