"""지수/업종 핸들 -- :class:`Index`.

한 지수(업종)에 대해 조회를 시키는 핸들이다: ``kis.index("0001").quote()`` 처럼(0001=KOSPI 종합).
종목 핸들 :class:`~kis_openapi.ticker.Ticker` 와 대칭이며, 지수는 종목이 아니라 업종코드로 조회한다.

핸들은 :class:`~kis_openapi.client.KisClient` 가 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._domestic import index as index_api
from .index_quote import IndexQuote

if TYPE_CHECKING:
    from .client import KisClient


class Index:
    """한 지수(업종)에 대한 조회 핸들. 세션(:class:`KisClient`)과 업종코드를 안다.

    보통 직접 만들지 않고 :meth:`KisClient.index` 로 얻는다. ``code`` 는 업종코드(예: 0001 KOSPI
    종합, 1001 KOSDAQ 종합, 2001 KOSPI200).
    """

    code: str

    def __init__(self, client: KisClient, code: str) -> None:
        self._client = client
        self.code = code

    def quote(self) -> IndexQuote:
        """지수 현재가 스냅샷(레벨·시고저·등락종목수)."""
        return index_api.fetch_index_quote(self._client.transport, code=self.code)
