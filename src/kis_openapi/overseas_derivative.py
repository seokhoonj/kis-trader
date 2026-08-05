"""해외 선물/옵션 핸들 -- :class:`OverseasDerivative`.

한 해외 파생 계약을 조회하는 핸들이다: ``kis.overseas_futures("ESZ25").quote()`` 처럼. 국내 파생
핸들(:class:`~kis_openapi.derivative.Derivative`)과 대칭이며, 해외 계약은 시장구분(F/O) 대신
시리즈코드(``srs_cd``)로 식별하고 계약 통화·거래소가 시세에 함께 온다.

핸들은 :class:`~kis_openapi.client.KISClient` 가 ``kis.overseas_futures(srs_cd)`` /
``kis.overseas_option(srs_cd)`` 로 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._overseas import derivatives as overseas_derivatives_api

if TYPE_CHECKING:
    from .client import KISClient
    from .overseas_derivative_items import (
        OverseasDerivativeDetail,
        OverseasDerivativeQuote,
    )


class OverseasDerivative:
    """한 해외 선물/옵션 계약에 대한 조회 핸들. 세션과 시리즈코드·시장을 안다.

    보통 직접 만들지 않고 :meth:`KISClient.overseas_futures` / :meth:`KISClient.overseas_option`
    으로 얻는다. ``market`` 은 ``"future"``(선물) 또는 ``"option"``(옵션)이고, ``symbol`` 은
    시리즈코드(예: ESZ25).
    """

    symbol: str
    market: str

    def __init__(self, client: KISClient, symbol: str, *, market: str) -> None:
        self._client = client
        self.symbol = symbol
        self.market = market

    def quote(self) -> OverseasDerivativeQuote:
        """계약 현재가 스냅샷(가격·정산가·전일대비·호가·통화·거래소·만기·틱사이즈·증거금)."""
        return overseas_derivatives_api.fetch_quote(
            self._client.transport, srs_cd=self.symbol, market=self.market
        )

    def detail(self) -> OverseasDerivativeDetail:
        """계약 명세(거래소·통화·틱사이즈/틱가치·계약크기·증거금·만기·결제구분)."""
        return overseas_derivatives_api.fetch_detail(
            self._client.transport, srs_cd=self.symbol, market=self.market
        )
