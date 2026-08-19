"""해외선물옵션(08) 계좌 조회 DATA -- 예수금/보유/주문가능.

``kis.account`` (해외선물옵션 08)의 :class:`~kis_trader.overseas.derivative_account.
OverseasDerivativesAccount` 뷰가 돌려준다. **금액·수량은 조회 통화(``currency``)의 Decimal 이다
-- 원화(KRW)가 아니다.** 해외선물옵션 계좌는 통화별로 조회하므로 각 타입에 ``currency`` 를 함께
담고, 그 밖에 타입화하지 않은 벤더 필드는 ``_raw`` 로 노출한다. 모든 조회는 **실전 전용**이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class OverseasDerivativeDeposit:
    """해외선물옵션 예수금현황 -- 예수금·자산·증거금·손익 요약(불변).

    금액은 ``currency`` 통화의 Decimal(원화 아님). ``cash_balance`` 예수금잔액, ``total_asset``
    총자산평가금액, ``unrealized_pnl`` 선물옵션평가손익금액, ``realized_pnl`` 청산손익금액,
    ``brokerage_margin`` 위탁증거금액, ``maintenance_margin`` 유지증거금액, ``additional_margin``
    추가증거금액, ``risk_rate`` 위험율, ``orderable_amount`` 주문가능금액, ``withdrawable_amount``
    출금가능금액, ``receivable`` 미수금액, ``next_day_deposit`` 익일예수금액, ``option_value``
    옵션평가금액, ``fee`` 수수료. 타입화하지 않은 요약 필드는 ``_raw`` 로 접근한다.
    """

    currency: str                     # 통화코드(crcy_cd)
    cash_balance: Decimal             # 예수금잔액(fm_dnca_rmnd)
    total_asset: Decimal              # 총자산평가금액(fm_tot_asst_evlu_amt)
    unrealized_pnl: Decimal           # 선물옵션평가손익금액(fm_fuop_evlu_pfls_amt)
    realized_pnl: Decimal             # 청산손익금액(fm_lqd_pfls_amt)
    brokerage_margin: Decimal         # 위탁증거금액(fm_brkg_mgn_amt)
    maintenance_margin: Decimal       # 유지증거금액(fm_mntn_mgn_amt)
    additional_margin: Decimal        # 추가증거금액(fm_add_mgn_amt)
    risk_rate: Decimal                # 위험율(fm_risk_rt)
    orderable_amount: Decimal         # 주문가능금액(fm_ord_psbl_amt)
    withdrawable_amount: Decimal      # 출금가능금액(fm_drwg_psbl_amt)
    receivable: Decimal               # 미수금액(fm_rcvb_amt)
    next_day_deposit: Decimal         # 익일예수금액(fm_nxdy_dncl_amt)
    option_value: Decimal             # 옵션평가금액(fm_opt_evlu_amt)
    fee: Decimal                      # 수수료(fm_fee)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
