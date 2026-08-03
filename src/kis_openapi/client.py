"""세션 루트 -- :class:`KisClient`.

인증(앱키/시크릿)과 기본 계좌를 쥔 세션이다. 모든 행위가 여기서 시작한다:
``kis.ticker("005930")`` 로 종목 핸들을, ``kis.balance()`` 등으로 계좌를 조회한다.
KIS 토큰은 앱키 단위(24h, 재발급 제한)라 세션이 캐시해 재사용한다.

시세만 볼 거면 ``account`` 없이도 되지만, 주문/잔고엔 계좌 식별정보가 필요하다.
"""

from __future__ import annotations

from typing import Literal

from ._domestic import account as account_api
from ._domestic import orders as orders_engine
from .balance import Balance, Portfolio, Position
from .errors import KisUsageError
from .instrument import DomesticBoard
from .order import Order
from .ranking import RankingQueries
from .report import ExecutionReport
from .risk import RiskLimits
from .store import OrderStore
from .ticker import Ticker
from .transport import Transport


class KisClient:
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
    ) -> None:
        """세션을 연다.

        ``store`` 는 주문 멱등 dedup 저장소 -- 생략하면 세션 인메모리(프로세스 재시작에 dedup
        유지 안 됨). 실거래는 ``store=OrderStore(path=...)`` 로 영속 저장소를 주는 것을 강력히
        권장한다(재시작 후에도 이중체결 장벽 유지). ``orderable=False`` 면 모든 주문을 와이어
        전에 :class:`~kis_openapi.errors.AccountNotOrderable` 로 막는다(조회전용 계좌 보호).
        ``risk`` 를 주면 모든 buy/sell 이 전송 전에 그 사전 리스크 한도
        (:class:`~kis_openapi.risk.RiskLimits`)를 통과해야 한다(fat-finger 방지).
        """
        self._app_key = app_key
        self._app_secret = app_secret
        self._environment = environment
        if transport is None:
            raise NotImplementedError(
                "실제 HTTP transport 는 아직 미구현이다 -- transport= 로 전송 구현을 주입하라."
            )
        self._transport = transport
        self._cano, self._product_code = _split_account(account)
        # 주문 멱등 dedup 저장소. 기본은 세션 인메모리 -- 프로세스 재시작에도 dedup 을 유지하려면
        # store=OrderStore(path=...) 로 영속 저장소를 주입하라(권장, 이중체결 장벽 지속).
        self._store = store if store is not None else OrderStore()
        self._orderable = orderable
        self._risk = risk

    @property
    def transport(self) -> Transport:
        """저수준 전송(내부 조회 계층이 사용)."""
        return self._transport

    @property
    def environment(self) -> Literal["real", "demo"]:
        """실전(real) / 모의(demo). 계좌·주문 TR 선택에 쓰인다."""
        return self._environment

    def ticker(self, symbol: str, *, market: DomesticBoard | None = None) -> Ticker:
        """종목 핸들을 만든다. 시장은 심볼로 자동 판별(6자리 숫자 -> 국내 KRX)."""
        return Ticker(self, symbol, market=market)

    @property
    def ranking(self) -> RankingQueries:
        """시장 전체 순위 네임스페이스 -- ``kis.ranking.by_change()`` / ``by_volume()`` 등."""
        return RankingQueries(self)

    # --- 계좌 단위 조회(계좌 정보 필요) ------------------------------
    # 계좌 미설정이면 :class:`~kis_openapi.errors.KisUsageError`, 실패/응답 부재/파싱 실패는
    # :class:`~kis_openapi.errors.KisError`.
    def balance(self) -> Balance:
        """계좌의 현금·자산 요약."""
        cano, product_code = self._require_account()
        return account_api.fetch_balance(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def positions(self) -> list[Position]:
        """보유 종목 전체(0수량 잔여 lot 포함)."""
        cano, product_code = self._require_account()
        return account_api.fetch_positions(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def portfolio(self) -> Portfolio:
        """현금·자산 요약과 보유 종목을 한 번의 조회로 함께."""
        cano, product_code = self._require_account()
        return account_api.fetch_portfolio(
            self._transport, cano=cano, product_code=product_code, environment=self._environment
        )

    def reconcile(self, client_order_id: str) -> ExecutionReport | None:
        """미확인 주문(타임아웃 등)의 실제 상태를 브로커에 재조회한다 -- **보수적**.

        완료 리포트가 있으면 반환. in-flight 면 일별체결조회로 확인해 정확히 1건이면 확정,
        모호(0/다건)하면 미접수로 단정하지 않는다(``None`` 또는 :class:`~kis_openapi.errors.KisError`).
        모르는 id 는 :class:`~kis_openapi.errors.KisUsageError`. 재조회 자체가 시간초과면
        :class:`~kis_openapi.errors.OrderTimeoutError`(in-flight 유지, 잠시 후 재시도).
        """
        cano, product_code = self._require_account()
        return orders_engine.reconcile(
            self._transport, self._store, client_order_id,
            cano=cano, product_code=product_code, environment=self._environment,
        )

    def _place_order(self, order: Order) -> ExecutionReport:
        """주문을 안전 엔진에 넘겨 전송한다(Ticker.buy/sell 이 호출). 계좌 정보 필요."""
        cano, product_code = self._require_account()
        return orders_engine.place(
            self._transport, self._store, order,
            cano=cano, product_code=product_code, environment=self._environment,
            orderable=self._orderable, risk=self._risk,
        )

    def _require_account(self) -> tuple[str, str]:
        """계좌 식별정보를 돌려주거나, 없으면 :class:`KisUsageError`."""
        if self._cano is None or self._product_code is None:
            raise KisUsageError(
                "계좌 조회/주문에는 계좌 정보가 필요하다 -- "
                "KisClient(..., account='12345678-01') 로 생성하라."
            )
        return self._cano, self._product_code


def _split_account(account: str | None) -> tuple[str, str] | tuple[None, None]:
    """``"12345678-01"`` -> (계좌번호 ``"12345678"``, 상품코드 ``"01"``). ``None`` 은 (None, None)."""
    if account is None:
        return None, None
    cano, _, product_code = account.partition("-")
    if not cano or not product_code or "-" in product_code:
        raise KisUsageError(
            f"account 형식은 '계좌번호-상품코드'여야 한다(예: '12345678-01'): {account!r}"
        )
    return cano, product_code
