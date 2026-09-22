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
        시장별로 바꾼다. 해외 주문엔 아직 사전 리스크 게이트가 없어(참조가가 국내 시세 기반),
        ``risk`` 를 켠 세션에서 해외 주문을 내면 명확히 거부한다."""
        cano, product_code = self._require_account()
        build_request: orders_engine.PlaceRequestBuilder | None = None
        extract_output = None
        risk = self._risk
        if overseas_orders_engine.is_overseas_exchange(order.exchange):
            if risk is not None:
                raise KISUsageError(
                    "해외 주문엔 사전 리스크 게이트가 아직 미지원이다 -- risk 없는 세션에서 내거나 "
                    "국내 주문에만 risk 를 쓰라."
                )
            # 미국주식 algo(TWAP/VWAP)는 모의투자 미지원 -- claim/빌드 전에 조기 거부(paper 발주 도달
            # 차단). 와이어 빌더도 같은 거부를 하지만, 라우팅 자리에서 먼저 막아 client_order_id 를
            # 소비하지 않는다(비-algo 해외 주문은 모의 지원이라 이 가드에 걸리지 않는다).
            if order.algo_strategy is not None and self._environment == "paper":
                raise KISUsageError("미국주식 algo(TWAP/VWAP) 주문은 모의투자 미지원 -- 실전에서만.")
            build_request = (
                overseas_orders_engine.make_overnight_order_request
                if order.session == "overnight"
                else overseas_orders_engine.make_order_request
            )
        elif derivative_orders_engine.is_derivative_exchange(order.exchange):
            # 파생 야간(STTN)은 모의투자 미지원 -- claim/빌드 전에 조기 거부(paper 야간 발주 도달 차단).
            # 빌더도 같은 거부를 하지만, 라우팅 자리에서 먼저 막아 client_order_id 를 소비하지 않는다.
            if order.session == "night" and self._environment == "paper":
                raise KISUsageError("파생 야간(STTN)은 모의투자 미지원 -- 실전에서만.")
            # 국내 파생(XKFE) -- 안전 코어(place)는 공유, 와이어 조립기와 엄격 output 파서만 파생용으로.
            # 파생 리스크는 참조가(국내 주식 시세) 기반 검사(notional/collar/tick)가 의미 없어 수량
            # 한도(max_order_quantity)만 허용한다 -- 그 밖의 한도가 켜져 있으면 명확히 거부한다.
            if risk is not None:
                _reject_unsupported_derivative_risk(risk)
            build_request = derivative_orders_engine.make_order_request
            extract_output = derivative_orders_engine._extract_fo_output
        elif bond_orders_engine.is_bond_exchange(order.exchange):
            # 국내 장내채권(BOND)은 모의투자 미지원 -- claim/빌드 전에 조기 거부(paper 발주 도달 차단).
            # 빌더도 같은 거부를 하지만, 라우팅 자리에서 먼저 막아 client_order_id 를 소비하지 않는다.
            if self._environment == "paper":
                raise KISUsageError("장내채권 주문은 모의투자 미지원 -- 실전에서만.")
            # 채권 리스크는 참조가(국내 주식 시세) 기반 검사(notional/collar/tick)가 의미 없어(해외·
            # 파생과 같은 이유), 리스크가 켜진 세션에선 명확히 거부한다.
            if risk is not None:
                raise KISUsageError(
                    "장내채권 주문엔 사전 리스크 게이트가 미지원이다(참조가 기반 검사가 채권엔 의미 "
                    "없음) -- risk 없는 세션에서 내거나 국내 주식 주문에만 risk 를 쓰라."
                )
            # 접수 응답은 국내주식과 같은 표준 형상이라 기본 output 파서를 쓴다(extract_output=None).
            build_request = bond_orders_engine.make_order_request
        elif overseas_deriv_orders_engine.is_overseas_fo_exchange(order.exchange):
            # 해외선물옵션(OTFM3001U)은 모의투자 미지원 -- claim/빌드 전에 조기 거부(paper 발주 도달
            # 차단). 빌더도 같은 거부를 하지만, 라우팅 자리에서 먼저 막아 client_order_id 를
            # 소비하지 않는다.
            if self._environment == "paper":
                raise KISUsageError("해외선물옵션 주문은 모의투자 미지원 -- 실전에서만.")
            # 해외선물옵션 리스크는 참조가(국내 주식 시세) 기반 검사(notional/collar/tick)가 의미
            # 없어(해외·채권과 같은 이유), 리스크가 켜진 세션에선 명확히 거부한다.
            if risk is not None:
                raise KISUsageError(
                    "해외선물옵션 주문엔 사전 리스크 게이트가 미지원이다(참조가 기반 검사가 의미 "
                    "없음) -- risk 없는 세션에서 내거나 국내 주식 주문에만 risk 를 쓰라."
                )
            # 안전 코어(place)는 공유, 와이어 조립기와 엄격 output 파서(ORD_DT->receipt_date /
            # ODNO->order_id)만 해외선물옵션용으로.
            build_request = overseas_deriv_orders_engine.make_order_request
            extract_output = overseas_deriv_orders_engine.extract_output
        elif order.credit_type is not None:
            # 국내 신용주문 -- 안전 코어(place)는 공유, 와이어 조립기만 신용용으로. risk 는 국내라
            # 그대로 적용된다(참조가=국내 시세).
            build_request = orders_engine.make_credit_order_request
        return orders_engine.place(
            self._transport, self._store, order,
            cano=cano, product_code=product_code, environment=self._environment,
            orderable=self._orderable, risk=risk, build_request=build_request,
            extract_output=extract_output,
        )

    def change_order(
        self, client_order_id: str, *, action: ChangeAction, quantity: Numeric | None,
        limit_price: Numeric | None, request_id: str | None,
    ) -> ExecutionReport:
        """접수된 주문의 미체결 수량을 취소/정정한다(``kis.orders.cancel`` / ``.modify``).

        국내·해외 주식, 국내 파생(XKFE), 국내 장내채권(BOND), 해외선물옵션(OSFO) 즉시주문과 아시아
        해외예약(취소 전용)을 모두 처리한다 -- ``fingerprint`` 의 거래소/세션으로 자산별 정정·취소 와이어
        빌더를 골라 공유 안전 코어(:func:`~kis_trader.domestic._engine.orders.submit_change`)에 넘긴다."""
        cano, product_code = self._require_account()
        fingerprint = self._store.fingerprint_for(client_order_id)
        report = self._store.report_for(client_order_id)
        if fingerprint is None or report is None:
            raise KISUsageError(f"확정된 원주문을 찾을 수 없다: {client_order_id!r}")
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
        builder: orders_engine.ChangeRequestBuilder | None = None
        if overseas_orders_engine.is_overseas_exchange(fingerprint.exchange):
            builder = (
                overseas_orders_engine.make_overnight_change_request
                if isinstance(fingerprint, ImmediateOrderFingerprint) and fingerprint.session == "overnight"
                else overseas_orders_engine.make_change_request
            )
        elif derivative_orders_engine.is_derivative_exchange(fingerprint.exchange):
            # 국내 파생(XKFE) 정정·취소는 파생 전용 와이어(order-rvsecncl, ORGN_ODNO only)로 조립한다.
            if isinstance(fingerprint, ImmediateOrderFingerprint) and fingerprint.session == "night":
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
                builder = derivative_orders_engine.make_change_request
        elif bond_orders_engine.is_bond_exchange(fingerprint.exchange):
            # 국내 장내채권(BOND) 정정·취소는 실전 전용 -- paper 지문 도달은 손상 신호라 와이어 전에
            # fail-closed. 채권은 부분 정정·취소를 지원(ORD_QTY2)하므로 전량 강제는 하지 않는다.
            if self._environment == "paper":
                raise KISUsageError("장내채권 정정·취소는 모의투자 미지원 -- 실전에서만.")
            builder = bond_orders_engine.make_change_request
        elif overseas_deriv_orders_engine.is_overseas_fo_exchange(fingerprint.exchange):
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
        # 게이트하지 않는다(위험한 주문을 못 지우면 오히려 위험). risk 는 국내 주식만 지원하고
        # (해외·파생·채권 정정은 발주 때 이미 risk 세션이 거부됨), 그 경로는 builder 가 None 이다.
        if self._risk is not None and action == "modify" and builder is None \
                and isinstance(fingerprint, ImmediateOrderFingerprint):
            effective_price = (
                change_limit_price if change_limit_price is not None
                else (coerce_decimal(fingerprint.limit_price, "limit_price")
                      if fingerprint.limit_price else None)
            )
            # risk.check 는 symbol/quantity/limit_price/stop_price 만 읽는다 -- 나머지 필드는 기본값.
            risk_probe = Order(
                symbol=fingerprint.symbol, side=fingerprint.side,
                order_type=fingerprint.order_type, quantity=change_quantity,
                limit_price=effective_price,
                stop_price=(coerce_decimal(fingerprint.stop_price, "stop_price")
                            if fingerprint.stop_price and fingerprint.stop_price != "0" else None),
            )
            orders_engine.run_pre_trade_risk(self._transport, risk_probe, self._risk)
        return orders_engine.submit_change(
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
        )

    def reconcile(self, client_order_id: str) -> ExecutionReport | None:
        """미확인 주문(타임아웃 등)의 실제 상태를 브로커에 재조회한다 -- **보수적**. ``kis.orders.reconcile``.

        완료 리포트가 있으면 반환. in-flight 면 저장된 주문 종류에 따라 확인처를 고른다: 국내 즉시
        주문은 일별체결조회, 해외 주문은 해외 체결내역, 예약주문은 예약주문조회. 어느 쪽이든 정확히
        1건이면 확정, 모호(0/다건)하면 미접수로 단정하지 않는다(``None`` 또는 :class:`~kis_trader.
        errors.KISError`). 모르는 id 는 :class:`~kis_trader.errors.KISUsageError`. 재조회 자체가
        시간초과면 :class:`~kis_trader.errors.OrderTimeoutError`(in-flight 유지, 잠시 후 재시도).
        """
        cano, product_code = self._require_account()
        # 해외 주문의 미확인(in-flight) 재조회는 국내 일별체결조회가 아니라 해외 체결내역으로 확인해야
        # 한다(엉뚱한 미접수 판정 방지) -- 지문의 거래소로 국내/해외 경로를 가른다. 완료 리포트가 있으면
        # 어느 엔진이든 그대로 반환한다.
        fingerprint = self._store.fingerprint_for(client_order_id)
        if isinstance(fingerprint, ChangeActionFingerprint):
            raise KISUsageError(
                "정정·취소 요청은 자동 reconcile을 지원하지 않는다. 원주문 상태를 조회해 확인하라."
            )
        if isinstance(fingerprint, ReservedOrderFingerprint):
            # 예약주문은 일별체결이 아니라 예약주문조회로 확인한다(국내/해외 예약 경로를 exchange 로 가른다).
            if fingerprint.exchange == "overseas-reserved-asia":
                return overseas_reserved_orders_api.reconcile_asia_reserved_order(
                    self._transport, self._store, client_order_id,
                    cano=cano, product_code=product_code, environment=self._environment,
                )
            if fingerprint.exchange == "overseas-reserved":
                return overseas_reserved_orders_api.reconcile_overseas_reserved_order(
                    self._transport, self._store, client_order_id,
                    cano=cano, product_code=product_code, environment=self._environment,
                )
            return reserved_orders_api.reconcile_reserved_order(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
            )
        if isinstance(fingerprint, ImmediateOrderFingerprint) and \
                overseas_orders_engine.is_overseas_exchange(fingerprint.exchange):
            return overseas_orders_engine.reconcile(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
            )
        if isinstance(fingerprint, ImmediateOrderFingerprint) and \
                derivative_orders_engine.is_derivative_exchange(fingerprint.exchange):
            # 국내 파생(XKFE) 미확인 주문은 국내주식 일별체결조회가 아니라 파생 일별체결내역으로 확인한다.
            return derivative_orders_engine.reconcile(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
            )
        if isinstance(fingerprint, ImmediateOrderFingerprint) and \
                bond_orders_engine.is_bond_exchange(fingerprint.exchange):
            # 국내 장내채권(BOND) 미확인 주문은 국내주식 일별체결조회로 확인할 수 없다(엉뚱한 테이블을
            # 조회해 체결 여부와 무관하게 None 을 돌려주는 잘못된 복구). 재조회 슬라이스는 미지원이라
            # fail-closed 한다 -- 이중체결 방지 장벽은 그대로다.
            raise KISUsageError(
                "장내채권 주문 재조회(reconcile)는 아직 미지원 -- 체결은 "
                "계좌 조회(kis.account.domestic.bonds.fills / open_orders)로 수동 확인하라."
            )
        if isinstance(fingerprint, ImmediateOrderFingerprint) and \
                overseas_deriv_orders_engine.is_overseas_fo_exchange(fingerprint.exchange):
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
