"""ELW 핸들 -- :class:`ELW`.

한 ELW(주식워런트증권)에 대해 고유 지표를 조회하는 핸들이다: ``kis.domestic.elw("58J297").sensitivity_trend()``
처럼. ELW 는 증권 형태로 상장된 옵션이라 기본 시세(현재가/호가/체결)는 6자리 코드로 종목 핸들
(:class:`~kis_trader.domestic.stock.DomesticStock`)이 그대로 조회한다 -- 이 핸들은 그 위에 옵션 분석 지표
(민감도 그릭스, 변동성, 투자지표 추이)만 얹는다.

핸들은 :class:`~kis_trader.client.KISClient` 가 ``kis.domestic.elw(code)`` 로 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._engine import elw as elw_api

if TYPE_CHECKING:
    from ..client import KISClient
    from ._engine.elw import TrendInterval
    from .entities.elw import (
        ELWIndicatorPoint,
        ELWLPFlow,
        ELWQuote,
        ELWSensitivityPoint,
        ELWVolatilityPoint,
    )


class ELW:
    """한 ELW 에 대한 고유 지표 조회 핸들. 세션(:class:`KISClient`)과 ELW 표준코드를 안다.

    보통 직접 만들지 않고 ``kis.domestic.elw`` 로 얻는다. ``code`` 는 ELW 표준코드(6자리).
    기본 시세는 ``kis.domestic.stock(code)`` 로 조회한다(ELW 는 종목처럼 상장돼 있다).
    """

    code: str

    def __init__(self, client: KISClient, code: str) -> None:
        self._client = client
        self.code = code

    def quote(self) -> ELWQuote:
        """ELW 현재가 스냅샷(기초자산가·내재변동성·이론가·괴리율·행사가·머니니스 포함).

        종목 기본 시세(``kis.domestic.stock(code).quote()``)와 달리 옵션으로서의 맥락(기초자산·그릭스 파생)을
        함께 준다."""
        return elw_api.fetch_quote(self._client.transport, code=self.code)

    def sensitivity_trend(
        self, interval: TrendInterval = "day"
    ) -> list[ELWSensitivityPoint]:
        """민감도(그릭스) 추이. ``interval`` 은 ``"trade"``(체결별)/``"day"``(일별)."""
        return elw_api.fetch_sensitivity_trend(
            self._client.transport, code=self.code, interval=interval
        )

    def volatility_trend(
        self, interval: TrendInterval = "day", *, minutes: int = 1, include_past: bool = False
    ) -> list[ELWVolatilityPoint]:
        """변동성(내재변동성) 추이. ``interval`` 은 체결/일별/분별/틱 모두 지원.

        ``minutes`` 는 ``interval="minute"`` 일 때 봉 간격(1/3/5/10/30/60분), ``include_past`` 는
        분별에서 과거 데이터 포함 여부."""
        return elw_api.fetch_volatility_trend(
            self._client.transport, code=self.code, interval=interval,
            minutes=minutes, include_past=include_past,
        )

    def indicator_trend(
        self, interval: TrendInterval = "day", *, minutes: int = 1, include_past: bool = False
    ) -> list[ELWIndicatorPoint]:
        """투자지표(레버리지·기어링·내재가치·패리티) 추이. ``interval`` 은 체결/일별/분별.

        ``minutes``/``include_past`` 는 분별에서만 쓴다(1/3/5/10/30/60분)."""
        return elw_api.fetch_indicator_trend(
            self._client.transport, code=self.code, interval=interval,
            minutes=minutes, include_past=include_past,
        )

    def lp_flows(self) -> list[ELWLPFlow]:
        """일별 LP(유동성공급자) 매매 흐름(최신순) -- 매수/매도 수량·평균단가·LP 보유비율."""
        return elw_api.fetch_lp_flows(self._client.transport, code=self.code)
