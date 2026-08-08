"""퇴직연금(IRP/DC) 계좌 조회 DATA -- 예수금·매수가능·잔고·미체결.

일반 위탁계좌(:mod:`~kis_openapi.balance`)와 별개인 퇴직연금 전용 조회 결과다. KIS가 퇴직연금
계좌를 별도 엔드포인트(`/trading/pension/...`)로 두므로 반환 타입도 나눈다. 금액은 KRW Decimal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class PensionDeposit:
    """퇴직연금 예수금 요약(불변).

    ``deposit_total`` 예수금총액, ``next_day_settlement`` 익일정산액, ``next_day_settlement_amount``
    익일결제금액, ``second_day_settlement_amount`` 2익일결제금액.
    """

    deposit_total: Decimal            # 예수금총액(dnca_tota)
    next_day_settlement: Decimal      # 익일정산액(nxdy_excc_amt)
    next_day_settlement_amount: Decimal  # 익일결제금액(nxdy_sttl_amt)
    second_day_settlement_amount: Decimal  # 2익일결제금액(nx2_day_sttl_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class PensionBuyableAmount:
    """퇴직연금 매수가능 여력(불변).

    ``orderable_cash`` 주문가능현금, ``reusable_cash`` 재사용가능금액, ``calc_unit_price``
    가능수량계산단가, ``max_buyable_amount``/``max_buyable_quantity`` 최대 매수금액/수량.
    """

    symbol: str
    orderable_cash: Decimal           # 주문가능현금(ord_psbl_cash)
    reusable_cash: Decimal            # 재사용가능금액(ruse_psbl_amt)
    calc_unit_price: Decimal          # 가능수량계산단가(psbl_qty_calc_unpr)
    max_buyable_amount: Decimal       # 최대매수금액(max_buy_amt)
    max_buyable_quantity: Decimal     # 최대매수수량(max_buy_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
