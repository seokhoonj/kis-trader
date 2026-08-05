"""ELW 핸들 -- :class:`Elw`.

한 ELW(주식워런트증권)에 대해 고유 지표를 조회하는 핸들이다: ``kis.elw("58J297").sensitivity_trend()``
처럼. ELW 는 증권 형태로 상장된 옵션이라 기본 시세(현재가/호가/체결)는 6자리 코드로 종목 핸들
(:class:`~kis_openapi.ticker.Ticker`)이 그대로 조회한다 -- 이 핸들은 그 위에 옵션 분석 지표
(민감도 그릭스, 변동성, 투자지표 추이)만 얹는다.

핸들은 :class:`~kis_openapi.client.KisClient` 가 ``kis.elw(code)`` 로 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._domestic import elw as elw_api

if TYPE_CHECKING:
    from ._domestic.elw import TrendInterval
    from .client import KisClient
    from .elw_items import ElwSensitivityPoint


class Elw:
    """한 ELW 에 대한 고유 지표 조회 핸들. 세션(:class:`KisClient`)과 ELW 표준코드를 안다.

    보통 직접 만들지 않고 :meth:`KisClient.elw` 로 얻는다. ``code`` 는 ELW 표준코드(6자리).
    기본 시세는 ``kis.ticker(code)`` 로 조회한다(ELW 는 종목처럼 상장돼 있다).
    """

    code: str

    def __init__(self, client: KisClient, code: str) -> None:
        self._client = client
        self.code = code

    def sensitivity_trend(
        self, interval: TrendInterval = "day"
    ) -> list[ElwSensitivityPoint]:
        """민감도(그릭스) 추이. ``interval`` 은 ``"trade"``(체결별)/``"day"``(일별)."""
        return elw_api.fetch_sensitivity_trend(
            self._client.transport, code=self.code, interval=interval
        )
