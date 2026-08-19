"""채권 핸들 -- :class:`Bond`.

한 장내채권에 대해 조회를 시키는 핸들이다: ``kis.domestic.bond("KR2033022D33").quote()`` 처럼. 종목 핸들
:class:`~kis_trader.domestic.stock.DomesticStock` 와 대칭이며, 채권은 표준코드(ISIN)로 조회한다.

핸들은 :class:`~kis_trader.client.KISClient` 가 ``kis.domestic.bond(code)`` 로 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from ..bar import Bar, Interval
from ..order import _BOND_EXCHANGE, Order, coerce_decimal, mint_client_order_id
from ._engine import bonds as bonds_api
from .entities.bond import (
    BondDailyPrice,
    BondIssuance,
    BondProfile,
    BondQuote,
    BondValuation,
)

if TYPE_CHECKING:
    from .._literals import Numeric
    from ..client import KISClient
    from ..order_book import OrderBook
    from ..report import ExecutionReport
    from ..trade import Trade


class Bond:
    """한 채권에 대한 조회 핸들. 세션(:class:`KISClient`)과 표준코드를 안다.

    보통 직접 만들지 않고 ``kis.domestic.bond`` 로 얻는다. ``code`` 는 표준코드(ISIN).
    """

    code: str

    def __init__(self, client: KISClient, code: str) -> None:
        self._client = client
        self.code = code

    def profile(self) -> BondProfile:
        """채권 기본/발행 정보(발행일·만기·표면금리·만기수익률·통화)."""
        return bonds_api.fetch_profile(self._client.transport, code=self.code)

    def issuance(self) -> BondIssuance:
        """채권의 상세 발행 조건·발행기관·신용등급·거래 상태."""
        return bonds_api.fetch_issuance(self._client.transport, code=self.code)

    def quote(self) -> BondQuote:
        """채권 현재가 스냅샷(가격·시고저·전일대비·수익률)."""
        return bonds_api.fetch_quote(self._client.transport, code=self.code)

    def bars(self, interval: Interval = "1d") -> list[Bar]:
        """채권 일별 OHLCV를 과거->현재 오름차순으로. ``interval="1d"`` 만 지원한다."""
        return bonds_api.fetch_bars(self._client.transport, code=self.code, interval=interval)

    def daily_prices(self) -> list[BondDailyPrice]:
        """날짜별 채권 현재가·등락·OHLCV를 과거->현재 순으로."""
        return bonds_api.fetch_daily_prices(self._client.transport, code=self.code)

    def valuations(self, *, start: str | date, end: str | date) -> list[BondValuation]:
        """평가기관별 채권 단가·수익률의 일별 시계열을 과거->현재 순으로."""
        return bonds_api.fetch_valuations(
            self._client.transport, code=self.code, start=start, end=end
        )

    def order_book(self) -> OrderBook:
        """채권 호가창(5단계 매수/매도 심도)."""
        return bonds_api.fetch_order_book(self._client.transport, code=self.code)

    def trades(self) -> list[Trade]:
        """채권의 최근 체결 목록(최신순)."""
        return bonds_api.fetch_trades(self._client.transport, code=self.code)

    # --- 발주(장내채권 매수; 계좌 + 안전 엔진 -- 종목 핸들 buy 와 대칭) ---
    def buy(
        self, *, quantity: Numeric, limit_price: Numeric,
        client_order_id: str | None = None,
    ) -> ExecutionReport:
        """이 채권을 매수한다 -- 장내채권은 지정가(채권단가) 전용이라 ``limit_price`` 는 필수다.
        ``quantity`` 는 액면(face) 단위, ``limit_price`` 는 채권단가, ``client_order_id`` 는 멱등키
        (생략 시 자동 발행)다. 채권 주문은 당일(day) 전용이라 ``time_in_force`` 는 받지 않는다 --
        엔드포인트에 TIF 필드가 없어서 ioc/fok 를 받으면 지문에는 남고 와이어에는 안 실려 조용한
        불일치가 된다.

        **실전투자 전용**(모의투자 미지원)이라 ``environment="paper"`` 세션에선
        :class:`~kis_trader.errors.KISUsageError` 로 fail-closed 한다. 실주문이라 이 경로는 라이브로
        검증하기 전까지 프로덕션 사용에 앞서 실계좌 확인이 필요하다. 이중체결 방지·타임아웃 재시도
        금지는 국내주식·파생과 같은 안전 엔진에서 자동 적용된다. 계좌 미설정은
        :class:`~kis_trader.errors.KISUsageError`, 접수 거부는 ``OrderRejectedError``, 타임아웃(체결
        불명)은 ``OrderTimeoutError`` 다. 채권 타임아웃은 ``kis.orders.reconcile`` 로 확인되지 않으니
        (미지원, fail-closed) ``kis.account.domestic.bonds`` 의 체결/미체결(fills/open_orders) 조회로
        직접 확인한다.

        일반시장(``SAMT_MKET_PTCI_YN="N"``)에서는 주문 수량이 액면 10단위의 배수여야 할 수 있다
        (KIS 명세는 10단위 규칙을 적으면서도 자체 예시가 이를 어겨서, 라이브 검증 전까지 문서로만
        남기고 강제하지 않는다).

        KIS URL/TR-ID: ``POST /uapi/domestic-bond/v1/trading/buy`` (실전 ``TTTC0952U``, 모의 미지원)."""
        order = Order(
            symbol=self.code, side="buy", order_type="limit",
            quantity=coerce_decimal(quantity, "quantity"),
            limit_price=coerce_decimal(limit_price, "limit_price"),
            time_in_force="day", exchange=_BOND_EXCHANGE,
            client_order_id=client_order_id or mint_client_order_id(),
        )
        return self._client._place_order(order)
