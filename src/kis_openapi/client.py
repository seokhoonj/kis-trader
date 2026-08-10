"""세션 루트 -- :class:`KISClient`.

인증(앱키/시크릿)과 기본 계좌를 쥔 세션이다. 모든 행위가 여기서 시작한다:
``kis.domestic.stock("005930")`` 로 종목 핸들을, ``kis.domestic.account.balance()`` 등으로 계좌를
조회한다. KIS 토큰은 앱키 단위(24h, 재발급 제한)라 세션이 캐시해 재사용한다.

세션은 전송·기본계좌·주문 안전코어(store/risk/place)만 쥐고, 공개 행위 표면은 자산군 네임스페이스
(:mod:`~kis_openapi.namespaces`)가 담당한다. 시세만 볼 거면 ``account`` 없이도 되지만, 주문/잔고엔
계좌 식별정보가 필요하다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from ._domestic import orders as orders_engine
from ._domestic import reserved_orders as reserved_orders_api
from ._masters import (
    Fetch,
    MasterIndex,
    load_overseas_index,
    urlopen_fetch,
)
from ._overseas import orders as overseas_orders_engine
from ._overseas import reserved_orders as overseas_reserved_orders_api
from .errors import KISUsageError
from .namespaces import (
    DomesticNamespace,
    OrdersNamespace,
    OverseasNamespace,
    PensionNamespace,
)
from .order import Order, Side, mint_client_order_id
from .store import OrderStore

if TYPE_CHECKING:
    from ._masters import InstrumentRecord
    from .report import ExecutionReport
    from .risk import RiskLimits
    from .transport import Transport


class KISClient:
    """KIS Open API 세션. ``transport`` 는 주입된 전송 구현(실제 HTTP 또는 테스트용 가짜)이다."""

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        account: str | None = None,
        environment: Literal["real", "demo"] = "real",
        transport: Transport | None = None,
        store: OrderStore | None = None,
        orderable: bool = True,
        risk: RiskLimits | None = None,
        master_index: MasterIndex | None = None,
        master_fetch: Fetch | None = None,
    ) -> None:
        """세션을 연다.

        ``store`` 는 주문 멱등 dedup 저장소 -- 생략하면 세션 인메모리(프로세스 재시작에 dedup
        유지 안 됨). 실거래는 ``store=OrderStore(path=...)`` 로 영속 저장소를 주는 것을 강력히
        권장한다(재시작 후에도 이중체결 장벽 유지). ``orderable=False`` 면 모든 주문을 와이어
        전에 :class:`~kis_openapi.errors.AccountNotOrderableError` 로 막는다(조회전용 계좌 보호).
        ``risk`` 를 주면 모든 buy/sell 이 전송 전에 그 사전 리스크 한도
        (:class:`~kis_openapi.risk.RiskLimits`)를 통과해야 한다(fat-finger 방지).

        해외 심볼 조회(:meth:`instrument`)는 KIS 종목 마스터로 심볼->거래소를 찾는다. ``master_index``
        를 주면 그 인덱스를 쓰고(테스트/고급), 없으면 첫 조회 때 마스터를 받아 캐시한다. ``master_fetch``
        로 다운로더를 바꿀 수 있다(기본은 KIS 배포 서버).
        """
        self._app_key = app_key
        self._app_secret = app_secret
        self._environment = environment
        if transport is None:
            from ._auth import TokenManager
            from ._http import RequestsTransport

            transport = RequestsTransport(
                app_key=app_key,
                app_secret=app_secret,
                environment=environment,
                token_manager=TokenManager(
                    app_key=app_key,
                    app_secret=app_secret,
                    environment=environment,
                ),
            )
        self._transport = transport
        self._cano, self._product_code = _split_account(account)
        # 주문 멱등 dedup 저장소. 기본은 세션 인메모리 -- 프로세스 재시작에도 dedup 을 유지하려면
        # store=OrderStore(path=...) 로 영속 저장소를 주입하라(권장, 이중체결 장벽 지속).
        self._store = store if store is not None else OrderStore()
        self._orderable = orderable
        self._risk = risk
        # 해외 심볼->거래소 해석용 마스터 인덱스. 주입 없으면 첫 instrument() 호출 때 지연 로드.
        self._master_index = master_index
        self._master_fetch = master_fetch if master_fetch is not None else urlopen_fetch
        # 자산군 최상위 네임스페이스(공개 행위 표면). 세션이 쥔 전송/계좌/안전코어로 원장 엔진을 호출한다.
        self.domestic = DomesticNamespace(self)
        self.overseas = OverseasNamespace(self)
        self.pension = PensionNamespace(self)
        self.orders = OrdersNamespace(self)

    @property
    def transport(self) -> Transport:
        """저수준 전송(내부 조회 계층이 사용)."""
        return self._transport

    @property
    def environment(self) -> Literal["real", "demo"]:
        """실전(real) / 모의(demo). 계좌·주문 TR 선택에 쓰인다."""
        return self._environment

    def instrument(self, symbol: str, *, exchange: str | None = None) -> InstrumentRecord:
        """해외 심볼을 KIS 종목 마스터로 조회한다 -- 거래소코드/통화/종목유형/이름을 돌려준다.

        같은 심볼이 여러 거래소에 있으면 ``exchange`` 를 명시해야 한다(:class:`~kis_openapi.errors.
        KISUsageError`). 첫 호출은 마스터를 받아 캐시하므로 느릴 수 있다(이후는 캐시)."""
        if self._master_index is None:
            self._master_index = load_overseas_index(fetch=self._master_fetch)
        return self._master_index.resolve(symbol, exchange=exchange)

    def _reconcile(self, client_order_id: str) -> ExecutionReport | None:
        """미확인 주문(타임아웃 등)의 실제 상태를 브로커에 재조회한다 -- **보수적**. ``kis.orders.reconcile``.

        완료 리포트가 있으면 반환. in-flight 면 저장된 주문 종류에 따라 확인처를 고른다: 국내 즉시
        주문은 일별체결조회, 해외 주문은 해외 체결내역, 예약주문은 예약주문조회. 어느 쪽이든 정확히
        1건이면 확정, 모호(0/다건)하면 미접수로 단정하지 않는다(``None`` 또는 :class:`~kis_openapi.
        errors.KISError`). 모르는 id 는 :class:`~kis_openapi.errors.KISUsageError`. 재조회 자체가
        시간초과면 :class:`~kis_openapi.errors.OrderTimeoutError`(in-flight 유지, 잠시 후 재시도).
        """
        cano, product_code = self._require_account()
        # 해외 주문의 미확인(in-flight) 재조회는 국내 일별체결조회가 아니라 해외 체결내역으로 확인해야
        # 한다(엉뚱한 미접수 판정 방지) -- 지문의 거래소로 국내/해외 경로를 가른다. 완료 리포트가 있으면
        # 어느 엔진이든 그대로 반환한다.
        fingerprint = self._store.fingerprint_for(client_order_id)
        if fingerprint is not None and fingerprint.exchange.startswith("action:"):
            raise KISUsageError(
                "정정·취소 요청은 자동 reconcile을 지원하지 않는다. 원주문 상태를 조회해 확인하라."
            )
        if fingerprint is not None and fingerprint.exchange == "reserved":
            # 예약주문은 일별체결이 아니라 예약주문조회로 확인한다.
            return reserved_orders_api.reconcile_reserved_order(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
            )
        if fingerprint is not None and fingerprint.exchange == "overseas-reserved":
            return overseas_reserved_orders_api.reconcile_overseas_reserved_order(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
            )
        if fingerprint is not None and overseas_orders_engine.is_overseas_exchange(fingerprint.exchange):
            return overseas_orders_engine.reconcile(
                self._transport, self._store, client_order_id,
                cano=cano, product_code=product_code, environment=self._environment,
            )
        return orders_engine.reconcile(
            self._transport, self._store, client_order_id,
            cano=cano, product_code=product_code, environment=self._environment,
        )

    def _change_order(
        self, client_order_id: str, *, action: str, quantity: object | None,
        price: object | None, request_id: str | None,
    ) -> ExecutionReport:
        """접수된 국내·해외 주식 주문의 미체결 수량을 취소/정정한다(``kis.orders.cancel`` / ``.modify``)."""
        cano, product_code = self._require_account()
        fingerprint = self._store.fingerprint_for(client_order_id)
        report = self._store.report_for(client_order_id)
        if fingerprint is None or report is None:
            raise KISUsageError(f"확정된 원주문을 찾을 수 없다: {client_order_id!r}")
        original_quantity = Decimal(fingerprint.quantity)
        remaining_quantity = original_quantity - report.filled_quantity
        change_quantity = remaining_quantity if quantity is None else Decimal(str(quantity))
        change_price = None if price is None else Decimal(str(price))
        builder = None
        if overseas_orders_engine.is_overseas_exchange(fingerprint.exchange):
            builder = (
                overseas_orders_engine.make_daytime_change_request
                if fingerprint.session == "daytime"
                else overseas_orders_engine.make_change_request
            )
        return orders_engine.submit_change(
            self._transport, self._store,
            original_client_order_id=client_order_id,
            request_id=request_id or mint_client_order_id(),
            action=action,
            quantity=change_quantity,
            price=change_price,
            cano=cano,
            product_code=product_code,
            environment=self._environment,
            build_request=builder,
        )

    def _place_order(self, order: Order) -> ExecutionReport:
        """주문을 안전 엔진에 넘겨 전송한다(종목 핸들 buy/sell 이 호출). 계좌 정보 필요.

        국내/해외 모두 같은 안전 코어(이중체결 방지·재시도 금지)를 쓰되, 와이어 요청 조립기만
        시장별로 바꾼다. 해외 주문엔 아직 사전 리스크 게이트가 없어(참조가가 국내 시세 기반),
        ``risk`` 를 켠 세션에서 해외 주문을 내면 명확히 거부한다."""
        cano, product_code = self._require_account()
        build_request = None
        risk = self._risk
        if overseas_orders_engine.is_overseas_exchange(order.exchange):
            if risk is not None:
                raise KISUsageError(
                    "해외 주문엔 사전 리스크 게이트가 아직 미지원이다 -- risk 없는 세션에서 내거나 "
                    "국내 주문에만 risk 를 쓰라."
                )
            build_request = (
                overseas_orders_engine.make_daytime_order_request
                if order.session == "daytime"
                else overseas_orders_engine.make_order_request
            )
        elif order.credit_type is not None:
            # 국내 신용주문 -- 안전 코어(place)는 공유, 와이어 조립기만 신용용으로. risk 는 국내라
            # 그대로 적용된다(참조가=국내 시세).
            build_request = orders_engine._make_credit_order_request
        return orders_engine.place(
            self._transport, self._store, order,
            cano=cano, product_code=product_code, environment=self._environment,
            orderable=self._orderable, risk=risk, build_request=build_request,
        )

    def _place_reserved_order(
        self, *, symbol: str, side: Side, quantity: object, price: object | None,
        end_date: str | None, client_order_id: str | None,
    ) -> ExecutionReport:
        """예약주문을 예약 안전 엔진에 넘긴다(종목 핸들 reserve_buy/sell 이 호출). 계좌 정보 필요.

        즉시주문 안전 코어와 별개 흐름이되 dedup(OrderStore)·재시도 금지·보수적 재조회·주문가능 계좌
        가드는 공유한다. risk 게이트는 예약주문엔 적용하지 않는다(집행이 향후라 현재가 기준 참조가
        의미 없음)."""
        cano, product_code = self._require_account()
        return reserved_orders_api.place_reserved_order(
            self._transport, self._store,
            symbol=symbol, side=side, quantity=quantity, price=price, end_date=end_date,
            client_order_id=client_order_id or mint_client_order_id(), orderable=self._orderable,
            cano=cano, product_code=product_code, environment=self._environment,
        )

    def _place_overseas_reserved_order(
        self, *, symbol: str, side: Side, quantity: object, price: object, exchange: str,
        client_order_id: str | None,
    ) -> ExecutionReport:
        """미국 해외예약주문을 예약 안전 엔진에 넘긴다(종목 핸들 reserve_buy/sell 이 해외 종목일 때 호출)."""
        cano, product_code = self._require_account()
        return overseas_reserved_orders_api.place_overseas_reserved_order(
            self._transport, self._store,
            symbol=symbol, side=side, quantity=quantity, price=price, exchange=exchange,
            client_order_id=client_order_id or mint_client_order_id(), orderable=self._orderable,
            cano=cano, product_code=product_code, environment=self._environment,
        )

    def revoke_token(self) -> None:
        """현재 접근 토큰을 KIS ``/oauth2/revokeP`` 로 폐기한다. 이후 첫 요청 때 새 토큰이 재발급된다.

        실 HTTP transport 를 쓰는 세션에서만 유효하다(주입한 커스텀 transport 가 지원하지 않으면
        :class:`~kis_openapi.errors.KISUsageError`)."""
        revoke = getattr(self._transport, "revoke_token", None)
        if not callable(revoke):
            raise KISUsageError(
                "이 transport 는 토큰 폐기를 지원하지 않는다(실 HTTP 세션에서만 가능)."
            )
        revoke()

    def _require_account(self) -> tuple[str, str]:
        """계좌 식별정보를 돌려주거나, 없으면 :class:`KISUsageError`."""
        if self._cano is None or self._product_code is None:
            raise KISUsageError(
                "계좌 조회/주문에는 계좌 정보가 필요하다 -- "
                "KISClient(..., account='12345678-01') 로 생성하라."
            )
        return self._cano, self._product_code


def _split_account(account: str | None) -> tuple[str, str] | tuple[None, None]:
    """``"12345678-01"`` -> (계좌번호 ``"12345678"``, 상품코드 ``"01"``). ``None`` 은 (None, None)."""
    if account is None:
        return None, None
    cano, _, product_code = account.partition("-")
    if not cano or not product_code or "-" in product_code:
        raise KISUsageError(
            f"account 형식은 '계좌번호-상품코드'여야 한다(예: '12345678-01'): {account!r}"
        )
    return cano, product_code
