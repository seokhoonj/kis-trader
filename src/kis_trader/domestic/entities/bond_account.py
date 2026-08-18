"""장내채권 계좌 잔고/체결(DATA) -- :class:`BondPosition`, :class:`BondBuyable`,
:class:`BondOpenOrder`, :class:`BondFill`, :class:`BondFillHistory`.

``kis.account.domestic.bonds`` 뷰가 돌려주는 장내채권 계좌 조회 결과다. 채권은 위탁(01) 계좌를
주식과 함께 쓰되 ``domestic-bond`` 전용 엔드포인트로 조회한다. 금액·수량은 KRW Decimal, 날짜는
``date``(공백/형식오류면 ``None``). 시세 DATA(:mod:`~kis_trader.domestic.entities.bond`)와 달리
여기는 보유·매수가능·미체결·체결 같은 계좌 관점의 값이다. 타입화하지 않은 벤더 필드는 ``_raw``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class BondPosition:
    """한 장내채권 보유 lot 의 잔고 현황(불변).

    채권 잔고는 종목이 아니라 매수 단위(``buy_date`` + ``buy_sequence``)로 쪼개져 오므로 같은
    종목이 여러 lot 으로 나뉠 수 있다. ``comprehensive_tax_quantity`` 종합과세수량,
    ``separate_tax_quantity`` 분리과세수량은 세제 구분 수량이다.
    """

    symbol: str                       # 상품번호(pdno)
    name: str                         # 상품명(prdt_name)
    buy_date: date | None             # 매수일자(buy_dt)
    buy_sequence: str                 # 매수순번(buy_sqno)
    quantity: Decimal                 # 잔고수량(cblc_qty)
    comprehensive_tax_quantity: Decimal  # 종합과세수량(agrx_qty)
    separate_tax_quantity: Decimal    # 분리과세수량(sprx_qty)
    maturity_date: date | None        # 만기일자(exdt)
    buy_yield: Decimal                # 매수수익율(buy_erng_rt)
    buy_price: Decimal                # 매수단가(buy_unpr)
    buy_amount: Decimal               # 매수금액(buy_amt)
    orderable_quantity: Decimal       # 주문가능수량(ord_psbl_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
