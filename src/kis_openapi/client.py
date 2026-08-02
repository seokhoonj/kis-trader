"""세션 루트 -- :class:`KisClient`.

인증(앱키/시크릿)과 기본 계좌를 쥔 세션이다. 모든 행위가 여기서 시작한다:
``kis.ticker("005930")`` 로 종목 핸들을, ``kis.balance()`` 등으로 계좌 조회를(후속 슬라이스).
KIS 토큰은 앱키 단위(24h, 재발급 제한)라 세션이 캐시해 재사용한다(실제 HTTP 배선은 후속).

시세만 볼 거면 ``account`` 없이도 되지만, 주문/잔고엔 계좌 식별정보가 필요하다.
"""

from __future__ import annotations

from typing import Literal

from ._domestic import account as account_api
from .balance import Balance, Portfolio, Position
from .errors import KisUsageError
from .instrument import DomesticBoard
from .ticker import Ticker
from .transport import Transport


class KisClient:
    """KIS Open API 세션. ``transport`` 는 실제 HTTP 세션(후속) 또는 테스트용 주입 전송이다."""

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        account: str | None = None,
        environment: Literal["real", "demo"] = "real",
        transport: Transport | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._environment = environment
        if transport is None:
            raise NotImplementedError(
                "실제 HTTP transport 는 아직 미구현이다 -- 지금은 transport= 로 주입하라"
                "(추후 앱키/시크릿으로 자동 생성)."
            )
        self._transport = transport
        self._cano, self._product_code = _split_account(account)

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
