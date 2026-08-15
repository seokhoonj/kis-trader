"""세션 루트 -- :class:`KISClient`.

인증(앱키/시크릿)과 기본 계좌를 쥔 세션이다. 모든 행위가 여기서 시작한다:
``kis.domestic.stock("005930")`` 로 종목 핸들을, ``kis.domestic.account.balance()`` 등으로 계좌를
조회한다. KIS 토큰은 앱키 단위(24h, 재발급 제한)라 세션이 캐시해 재사용한다.

세션은 전송·기본계좌·주문 안전코어(store/risk/place)만 쥐고, 공개 행위 표면은 자산군 네임스페이스
(``kis.domestic`` / ``kis.overseas`` / ``kis.pension``)와 주문 lifecycle(``kis.orders``,
:class:`OrdersNamespace`)가 담당한다. 시세만 볼 거면 ``account`` 없이도 되지만, 주문/잔고엔 계좌
식별정보가 필요하다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from .domestic._engine import orders as orders_engine
from .domestic._engine import reserved_orders as reserved_orders_api
from .domestic.namespace import DomesticNamespace
from ._internal._masters import (
    DomesticListingIndex,
    Fetch,
    MasterIndex,
    load_domestic_index,
    load_overseas_index,
    urlopen_fetch,
)
from .overseas._engine import orders as overseas_orders_engine
from .overseas._engine import reserved_orders as overseas_reserved_orders_api
from .overseas.namespace import OverseasNamespace
from .pension.namespace import PensionNamespace
from .config import _fill_credentials, _split_account, environment_for_profile, token_cache_path
from .errors import KISUsageError
from .order import (
    ChangeAction,
    ChangeActionFingerprint,
    ImmediateOrderFingerprint,
    Order,
    ReservedOrderFingerprint,
    Side,
    coerce_decimal,
    mint_client_order_id,
)
from .store import OrderStore

if TYPE_CHECKING:
    from pathlib import Path

    from ._literals import Numeric
    from ._internal._masters import InstrumentRecord
    from .config import Profile
    from .realtime.client import RealtimeClient
    from .report import ExecutionReport
    from .risk import RiskLimits
    from .transport import Environment, Transport


class OrdersNamespace:
    """``kis.orders`` -- client_order_id 로 동작하는 주문 lifecycle(자산 무관). 안전 dedup/reconcile 코어.

    세션의 주문 안전코어(``_reconcile``/``_change_order``)를 자산 무관하게 감싼 공개 표면이라, 그 코어와
    같은 모듈에 둔다."""

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def reconcile(self, client_order_id: str) -> ExecutionReport | None:
        """접수 여부가 불확실한 주문을 KIS 서버에 실제로 조회해 상태를 확정한다(불확실하면 미확정 유지)."""
        return self._c._reconcile(client_order_id)

    def cancel(
        self, client_order_id: str, *, quantity: Numeric | None = None, request_id: str | None = None
    ) -> ExecutionReport:
        """접수된 주문을 취소한다(부분 취소는 ``quantity``)."""
        return self._c._change_order(
            client_order_id, action="cancel", quantity=quantity, limit_price=None, request_id=request_id
        )

    def modify(
        self, client_order_id: str, *, limit_price: Numeric, quantity: Numeric | None = None,
        request_id: str | None = None,
    ) -> ExecutionReport:
        """접수된 주문의 가격(또는 수량)을 정정한다.

        정정이 성공하면 KIS 가 원주문에 새 거래소 주문번호(ODNO)를 부여하므로, 이 정정된 주문을
        같은 ``client_order_id`` 가 계속 가리키도록 재바인딩한다 -- 이후 ``cancel``/``modify`` 는
        정정된 주문을 지목하고, ``report_for(client_order_id)`` 의 ``order_id`` 는 새 ODNO,
        상태는 ``PENDING_REPLACE`` 가 된다(place 시점 ODNO 를 캐시했다면 갱신 필요).

        **주의**: 정정 후 ``report_for(client_order_id).filled_quantity`` 는 **0 으로 리셋된다**
        -- 새 ODNO 는 정정 수량만큼의 신규 대기주문이라서다(이 값이 재바인딩된 지문 수량과 짝을
        이뤄 이후 잔량 계산이 맞는다). 원주문의 누적 체결량을 이 id 로만 읽으면 과소 집계되니,
        정정 이전 체결은 정정이 반환한 리포트/기존 실행에서 확인하라."""
        return self._c._change_order(
            client_order_id, action="modify", quantity=quantity, limit_price=limit_price,
            request_id=request_id,
        )


class KISClient:
    """KIS Open API 세션. ``transport`` 는 주입된 전송 구현(실제 HTTP 또는 테스트용 가짜)이다."""

    def __init__(
        self,
        *,
        profile: Profile = "main",
        app_key: str | None = None,
        app_secret: str | None = None,
        account: str | None = None,
        config_dir: str | Path | None = None,
        transport: Transport | None = None,
        token_cache_dir: str | Path | None = None,
        throttle: bool = True,
        requests_per_second: float | None = None,
        store: OrderStore | None = None,
        orderable: bool = True,
        allow_credit: bool = False,
        risk: RiskLimits | None = None,
        master_index: MasterIndex | None = None,
        master_fetch: Fetch | None = None,
        domestic_index: DomesticListingIndex | None = None,
    ) -> None:
        """세션을 연다.

        ``store`` 는 주문 멱등 dedup 저장소 -- 생략하면 세션 인메모리(프로세스 재시작에 dedup
        유지 안 됨). 실거래는 ``store=OrderStore(path=...)`` 로 영속 저장소를 주는 것을 강력히
        권장한다(재시작 후에도 이중체결 장벽 유지). ``orderable=False`` 면 모든 주문을 와이어
        전에 :class:`~kis_trader.errors.AccountNotOrderableError` 로 막는다(조회전용 계좌 보호).
        계좌 상품코드(ACNT_PRDT_CD)로도 자동 반영한다(공식 FAQ): IRP(29)는 주문불가라 orderable 을
        자동으로 끄고(수동 True 여도 막힘), DC가입자(55)는 API 이용 불가라 생성 시 거부한다.
        연금저축(22)은 주문 가능이라 막지 않는다.
        ``risk`` 를 주면 모든 buy/sell 이 전송 전에 그 사전 리스크 한도
        (:class:`~kis_trader.risk.RiskLimits`)를 통과해야 한다(fat-finger 방지).

        ``throttle`` (기본 True)은 built-in 전송에 sliding-window 유량 제한기를 붙여 KIS 호출
        유량(공식 실전 초당 18건/모의 1건, **앱키 단위 합산**) 아래로 마진을 두고(실전 15/모의 1)
        선제적으로 속도를 조절한다. ``requests_per_second`` 로 초당 한도를 바꾸고, ``throttle=False``
        로 끈다(직접 관리하거나 앱키를 분산 운용할 때). 주입한 ``transport`` 에는 적용되지 않는다.
        유량은 앱키(계좌) 단위라 **같은 앱키를 다른 프로세스/앱이 동시에 쓰면 이 리미터로 조율되지
        않는다** -- 그 경우 앱마다 별도 앱키를 신청해 쓰는 것을 권장한다.

        해외 심볼 조회(:meth:`instrument`)는 KIS 종목 마스터로 심볼->거래소를 찾는다. ``master_index``
        를 주면 그 인덱스를 쓰고(테스트/고급), 없으면 첫 조회 때 마스터를 받아 캐시한다. ``master_fetch``
        로 다운로더를 바꿀 수 있다(기본은 KIS 배포 서버).

        ``profile`` 이 어느 계좌 묶음으로 열지 정한다(``main`` 실전 주계좌·``paper`` 모의·``isa``/``irp``/
        ``pension``). 환경(실전/모의)도 프로필이 정한다(모의계좌만 모의). ``app_key``/``app_secret`` 을
        생략하면 **그 프로필의 저장된 자격증명을 읽는다**(환경변수 -> ``~/.config/kis-trader/credentials.json``;
        `:func:`~kis_trader.config.KISConfig.save` 로 저장). 즉 설정만 해두면 ``KISClient(profile=...)`` 한
        줄로 열린다. 값을 명시하면 파일을 읽지 않는다. ``config_dir`` 로 설정·토큰캐시 위치를 바꾼다
        (테스트/특수 위치). ``token_cache_dir`` 로 토큰 캐시만 따로 바꾼다(기본은 XDG
        ``~/.cache/kis-trader/tokens``, ``config_dir`` 을 주면 그 아래 ``tokens``).
        """
        environment = environment_for_profile(profile)
        if app_key is None or app_secret is None:
            # 실제로 빠진 항목만 저장분에서 채운다 -- 사용자가 app_key 만 넘겼는데 '없다'고
            # 오도하지 않도록(둘 다 넘겼으면 이 블록을 건너뛰어 파일을 아예 안 읽는다).
            resolved = _fill_credentials(
                profile, app_key=app_key, app_secret=app_secret, account=account, config_dir=config_dir
            )
            app_key, app_secret = resolved.app_key, resolved.app_secret
            if account is None:
                account = resolved.account
        if token_cache_dir is None and config_dir is not None:
            token_cache_dir = str(token_cache_path(config_dir))
        elif token_cache_dir is not None:
            token_cache_dir = str(token_cache_dir)   # Path 도 받아 내부는 str 로 통일
        self._app_key = app_key
        self._app_secret = app_secret
        self._environment = environment
        if transport is None:
            from ._internal._auth import TokenManager
            from ._internal._http import RequestsTransport
            from ._internal._ratelimit import (
                DEFAULT_REQUESTS_PER_SECOND_BY_ENVIRONMENT,
                SlidingWindowRateLimiter,
            )

            # 앱키 단위 호출 유량을 선제적으로 지킨다(기본 on). requests_per_second 로 초당 한도를
            # override, 없으면 환경 기본값(실전 15/모의 1 -- 공식 18/1 아래 마진). throttle=False 면
            # 리미터 미장착(사용자가 직접 관리하거나 앱키를 분산 운용). 주입한 transport 엔 미적용.
            rate_limiter = None
            if throttle:
                per_second = (
                    requests_per_second if requests_per_second is not None
                    else DEFAULT_REQUESTS_PER_SECOND_BY_ENVIRONMENT[environment]
                )
                rate_limiter = SlidingWindowRateLimiter.from_rate(per_second)
            transport = RequestsTransport(
                app_key=app_key,
                app_secret=app_secret,
                environment=environment,
                token_manager=TokenManager(
                    app_key=app_key,
                    app_secret=app_secret,
                    environment=environment,
                    cache_dir=token_cache_dir,
                ),
                rate_limiter=rate_limiter,
            )
        self._transport = transport
        self._cano, self._product_code = _split_optional_account(account)
        # 상품계좌종류(ACNT_PRDT_CD)로 이용 가능 범위를 자동 반영한다(공식 FAQ 2026-03-26):
        # DC가입자(55)는 Open API 이용 자체가 불가 -> 생성 거부. IRP(29)는 조회만 가능(주문 불가)
        # -> orderable 을 자동으로 끈다. 수동 orderable 플래그는 더 제약만 가능(주문불가 계좌를
        # 켜지 못한다). 연금저축(22)은 주문 가능이라 막지 않는다(IRP 와 혼동 주의).
        if self._product_code in _API_UNAVAILABLE_PRODUCT_CODES:
            raise KISUsageError(
                f"상품계좌종류 {self._product_code}(DC가입자)는 한국투자 Open API 이용이 불가하다."
            )
        # 주문 멱등 dedup 저장소. 기본은 세션 인메모리 -- 프로세스 재시작에도 dedup 을 유지하려면
        # store=OrderStore(path=...) 로 영속 저장소를 주입하라(권장, 이중체결 장벽 지속).
        self._store = store if store is not None else OrderStore()
        self._orderable = orderable and self._product_code not in _READ_ONLY_PRODUCT_CODES
        # 신용(융자/대주) 주문은 위험이 커 기본 비활성 -- opt-in(allow_credit=True) 해야 credit_buy/sell 이
        # 와이어에 닿는다. 조회(credit_buyable)는 읽기라 게이트하지 않는다.
        self._allow_credit = allow_credit
        self._risk = risk
        # 해외 심볼->거래소 해석용 마스터 인덱스. 주입 없으면 첫 instrument() 호출 때 지연 로드.
        self._master_index = master_index
        self._master_fetch = master_fetch if master_fetch is not None else urlopen_fetch
        # 국내 이름->코드 검색 인덱스. 주입 없으면 첫 domestic.search() 때 지연 로드(같은 배포 서버).
        self._domestic_index = domestic_index
        # 자산군 최상위 네임스페이스(공개 행위 표면). 세션이 쥔 전송/계좌/안전코어로 엔드포인트 엔진을 호출한다.
        self.domestic = DomesticNamespace(self)
        self.overseas = OverseasNamespace(self)
        self.pension = PensionNamespace(self)
        self.orders = OrdersNamespace(self)

    @property
    def transport(self) -> Transport:
        """저수준 전송(내부 조회 계층이 사용)."""
        return self._transport

    @property
    def environment(self) -> Environment:
        """실전(real) / 모의(paper). 계좌·주문 TR 선택에 쓰인다."""
        return self._environment

    @property
    def account(self) -> str | None:
        """세션 기본 계좌번호 ``CANO-ACNT_PRDT_CD`` (계좌 없이 열었으면 ``None``). 생성 시 한 번
        해석된 값이라, 컨슈머(CLI 등)가 자격증명을 다시 읽지 않고 이 값을 재사용한다."""
        if self._cano is None:
            return None
        return f"{self._cano}-{self._product_code}"

    def instrument(self, symbol: str, *, exchange: str | None = None) -> InstrumentRecord:
        """해외 심볼을 KIS 종목 마스터로 조회한다 -- 거래소코드/통화/종목유형/이름을 돌려준다.

        같은 심볼이 여러 거래소에 있으면 ``exchange`` 를 명시해야 한다(:class:`~kis_trader.errors.
        KISUsageError`). 첫 호출은 마스터를 받아 캐시하므로 느릴 수 있다(이후는 캐시)."""
        if self._master_index is None:
            self._master_index = load_overseas_index(fetch=self._master_fetch)
        return self._master_index.resolve(symbol, exchange=exchange)

    def _ensure_domestic_index(self) -> DomesticListingIndex:
        """국내 이름검색 인덱스(지연 로드). 첫 호출 때 KOSPI/KOSDAQ 마스터를 받아 캐시한다."""
        if self._domestic_index is None:
            self._domestic_index = load_domestic_index(fetch=self._master_fetch)
        return self._domestic_index

    def _reconcile(self, client_order_id: str) -> ExecutionReport | None:
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
        return orders_engine.reconcile(
            self._transport, self._store, client_order_id,
            cano=cano, product_code=product_code, environment=self._environment,
        )

    def _change_order(
        self, client_order_id: str, *, action: ChangeAction, quantity: Numeric | None,
        limit_price: Numeric | None, request_id: str | None,
    ) -> ExecutionReport:
        """접수된 국내·해외 주식 주문의 미체결 수량을 취소/정정한다(``kis.orders.cancel`` / ``.modify``)."""
        cano, product_code = self._require_account()
        fingerprint = self._store.fingerprint_for(client_order_id)
        report = self._store.report_for(client_order_id)
        if fingerprint is None or report is None:
            raise KISUsageError(f"확정된 원주문을 찾을 수 없다: {client_order_id!r}")
        original_quantity = Decimal(fingerprint.quantity)
        remaining_quantity = original_quantity - report.filled_quantity
        # 정정 경로도 발주(place)와 같은 수치 강제변환을 거쳐 NaN/Infinity 등 비유한 입력을 fail-closed
        # 로 막는다(raw Decimal(str(...)) 는 "nan"/"inf" 를 통과시켜 와이어에 실릴 수 있다).
        change_quantity = remaining_quantity if quantity is None else coerce_decimal(quantity, "quantity")
        change_limit_price = None if limit_price is None else coerce_decimal(limit_price, "limit_price")
        builder: orders_engine.ChangeRequestBuilder | None = None
        if overseas_orders_engine.is_overseas_exchange(fingerprint.exchange):
            builder = (
                overseas_orders_engine.make_overnight_change_request
                if isinstance(fingerprint, ImmediateOrderFingerprint) and fingerprint.session == "overnight"
                else overseas_orders_engine.make_change_request
            )
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

    def _place_order(self, order: Order) -> ExecutionReport:
        """주문을 안전 엔진에 넘겨 전송한다(종목 핸들 buy/sell 이 호출). 계좌 정보 필요.

        국내/해외 모두 같은 안전 코어(이중체결 방지·재시도 금지)를 쓰되, 와이어 요청 조립기만
        시장별로 바꾼다. 해외 주문엔 아직 사전 리스크 게이트가 없어(참조가가 국내 시세 기반),
        ``risk`` 를 켠 세션에서 해외 주문을 내면 명확히 거부한다."""
        cano, product_code = self._require_account()
        build_request: orders_engine.PlaceRequestBuilder | None = None
        risk = self._risk
        if overseas_orders_engine.is_overseas_exchange(order.exchange):
            if risk is not None:
                raise KISUsageError(
                    "해외 주문엔 사전 리스크 게이트가 아직 미지원이다 -- risk 없는 세션에서 내거나 "
                    "국내 주문에만 risk 를 쓰라."
                )
            build_request = (
                overseas_orders_engine.make_overnight_order_request
                if order.session == "overnight"
                else overseas_orders_engine.make_order_request
            )
        elif order.credit_type is not None:
            # 국내 신용주문 -- 안전 코어(place)는 공유, 와이어 조립기만 신용용으로. risk 는 국내라
            # 그대로 적용된다(참조가=국내 시세).
            build_request = orders_engine.make_credit_order_request
        return orders_engine.place(
            self._transport, self._store, order,
            cano=cano, product_code=product_code, environment=self._environment,
            orderable=self._orderable, risk=risk, build_request=build_request,
        )

    def _place_reserved_order(
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

    def _place_overseas_reserved_order(
        self, *, symbol: str, side: Side, quantity: Numeric, limit_price: Numeric, exchange: str,
        client_order_id: str | None,
    ) -> ExecutionReport:
        """미국 해외예약주문을 예약 안전 엔진에 넘긴다(종목 핸들 reserve_buy/sell 이 해외 종목일 때 호출)."""
        cano, product_code = self._require_account()
        return overseas_reserved_orders_api.place_overseas_reserved_order(
            self._transport, self._store,
            symbol=symbol, side=side, quantity=quantity, limit_price=limit_price, exchange=exchange,
            client_order_id=client_order_id or mint_client_order_id(), orderable=self._orderable,
            cano=cano, product_code=product_code, environment=self._environment,
        )

    def revoke_token(self) -> None:
        """현재 접근 토큰을 KIS ``/oauth2/revokeP`` 로 폐기한다. 이후 첫 요청 때 새 토큰이 재발급된다.

        실 HTTP transport 를 쓰는 세션에서만 유효하다(주입한 커스텀 transport 가 지원하지 않으면
        :class:`~kis_trader.errors.KISUsageError`)."""
        revoke = getattr(self._transport, "revoke_token", None)
        if not callable(revoke):
            raise KISUsageError(
                "이 transport 는 토큰 폐기를 지원하지 않는다(실 HTTP 세션에서만 가능)."
            )
        revoke()

    def realtime(self, *, reconnect: bool = True) -> "RealtimeClient":
        """실시간(웹소켓) 클라이언트를 만든다.

        ``/oauth2/Approval`` 로 접속키를 발급받아 :class:`~kis_trader.realtime.client.RealtimeClient`
        (동기 래퍼)를 돌려준다. ``ws.subscribe(tr_id, tr_key, on=콜백)`` 로 등록하고
        ``ws.start()`` 후 콜백 또는 ``for msg in ws.stream()`` 로 실시간 시세·통보를 받는다.
        async 앱은 코어(:class:`~kis_trader.realtime._connection.RealtimeConnection`)를 직접 쓴다.
        REST 는 그대로 동기다.
        """
        from ._internal._endpoints import websocket_url
        from .realtime._approval import fetch_approval_key
        from .realtime.client import RealtimeClient

        approval_key = fetch_approval_key(self._app_key, self._app_secret, self._environment)
        return RealtimeClient(
            approval_key, websocket_url(self._environment), reconnect=reconnect
        )

    def _require_credit_enabled(self) -> None:
        """신용주문이 opt-in(``allow_credit=True``)됐는지 확인 -- 안 됐으면 와이어 전에 막는다."""
        if not self._allow_credit:
            raise KISUsageError(
                "신용(융자/대주) 주문은 기본 비활성이다 -- KISClient(..., allow_credit=True) 로 "
                "명시적으로 켜야 한다(고위험 주문 보호)."
            )

    def _require_account(self) -> tuple[str, str]:
        """계좌 식별정보를 돌려주거나, 없으면 :class:`KISUsageError`."""
        if self._cano is None or self._product_code is None:
            raise KISUsageError(
                "계좌 조회/주문에는 계좌 정보가 필요하다 -- "
                "KISClient(..., account='12345678-01') 로 생성하라."
            )
        return self._cano, self._product_code


#: Open API 이용 자체가 불가한 상품계좌종류(ACNT_PRDT_CD). 공식 FAQ(2026-03-26): DC가입자(55).
_API_UNAVAILABLE_PRODUCT_CODES = frozenset({"55"})
#: 조회만 가능(주문 불가)한 상품계좌종류. IRP(29) -- KIS 가 주문 엔드포인트를 거부(APBK1744).
#: 연금저축(22)은 주문 가능이므로 여기 없다(IRP 와 혼동 주의).
_READ_ONLY_PRODUCT_CODES = frozenset({"29"})


def _split_optional_account(account: str | None) -> tuple[str, str] | tuple[None, None]:
    """``"12345678-01"`` -> (계좌번호 ``"12345678"``, 상품코드 ``"01"``). ``None`` 은 (None, None).
    계좌를 준 경우의 형식 검증은 저장 경로와 같은 :func:`~kis_trader.config._split_account` 를 쓴다
    -- 쓰기와 읽기가 한 계약을 공유하도록(발산 방지)."""
    if account is None:
        return None, None
    return _split_account(account)
