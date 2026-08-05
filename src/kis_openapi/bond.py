"""채권 핸들 -- :class:`Bond`.

한 장내채권에 대해 조회를 시키는 핸들이다: ``kis.bond("KR2033022D33").quote()`` 처럼. 종목 핸들
:class:`~kis_openapi.ticker.Ticker` 와 대칭이며, 채권은 표준코드(ISIN)로 조회한다.

핸들은 :class:`~kis_openapi.client.KisClient` 가 ``kis.bond(code)`` 로 만들어 준다 -- 직접 생성하지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._domestic import bonds as bonds_api
from .bond_items import BondQuote

if TYPE_CHECKING:
    from .client import KisClient


class Bond:
    """한 채권에 대한 조회 핸들. 세션(:class:`KisClient`)과 표준코드를 안다.

    보통 직접 만들지 않고 :meth:`KisClient.bond` 로 얻는다. ``code`` 는 표준코드(ISIN).
    """

    code: str

    def __init__(self, client: KisClient, code: str) -> None:
        self._client = client
        self.code = code

    def quote(self) -> BondQuote:
        """채권 현재가 스냅샷(가격·시고저·전일대비·수익률)."""
        return bonds_api.fetch_quote(self._client.transport, code=self.code)
