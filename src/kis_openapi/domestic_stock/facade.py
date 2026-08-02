"""국내주식 자산군 세그먼트 파사드 -- :class:`DomesticStock`.

KIS URL 구조(``/uapi/domestic-stock/v1/{quotations,trading}/...``)를 그대로 반영해, 자산군
아래에 기능(capability) 세그먼트를 둔다. 시세(``quotations``)는 자격증명 없이 쓸 수 있고,
매매/잔고(``trading``)는 계좌 식별정보가 있어야 한다(없이 생성하면 ``.trading`` 접근 시 안내
예외). 주문 실행(buy/sell 등)은 주문 안전 코어(``orders``)가 맡고 추후 ``trading`` 으로 합류한다.

사용례: ``client.domestic_stock.quotations.quote("005930")`` /
``client.domestic_stock.trading.balance()``.
"""

from __future__ import annotations

from typing import Literal

from ..errors import KisUsageError
from ..transport import Transport
from .quotations.facade import Quotations
from .trading.facade import Trading


class DomesticStock:
    """국내주식 세그먼트 루트. 하나의 :class:`Transport` 를 기능 파사드들과 공유한다.

    ``cano`` / ``product_code`` 를 주면 :attr:`trading` 을 쓸 수 있다(잔고 등 계좌 필요 기능).
    시세만 볼 거면 생략해도 된다.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        cano: str | None = None,
        product_code: str | None = None,
        environment: Literal["real", "demo"] = "real",
    ) -> None:
        self.quotations = Quotations(transport)
        # 계좌 식별정보는 둘 다 있거나 둘 다 없어야 한다 -- 한쪽만 주면 반쪽짜리 구성이
        # .trading 접근 시점까지 실패가 미뤄진다. 생성 시점에 바로 거른다.
        if (cano is None) != (product_code is None):
            raise KisUsageError(
                "cano 와 product_code 는 함께 주거나 함께 생략해야 한다(한쪽만 줄 수 없다)."
            )
        self._trading: Trading | None = None
        if cano is not None and product_code is not None:
            self._trading = Trading(
                transport, cano=cano, product_code=product_code, environment=environment
            )

    @property
    def trading(self) -> Trading:
        """매매/잔고 파사드. 계좌 식별정보 없이 생성했으면 :class:`KisUsageError`."""
        if self._trading is None:
            raise KisUsageError(
                "trading 은 계좌 식별정보가 필요하다 -- "
                "DomesticStock(transport, cano=..., product_code=...) 로 생성하라."
            )
        return self._trading
