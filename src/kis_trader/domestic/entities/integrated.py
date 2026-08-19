"""통합잔고(DATA) -- :class:`CurrencyDeposit`, :class:`IntegratedBalance`.

세션 계좌(위탁 01)의 국내주식+채권+해외주식 잔고를 한 뷰로 합친 결과다
(``kis.account.balance()`` 가 돌려준다). 새 와이어는 없다 -- 기존 세 조회의 합성이다.
통화별 예수금(:class:`CurrencyDeposit`)이 진실의 원천이고, 원화 headline 롤업은 KIS가 준
원화 집계의 순수 합으로만 산출한다(환율 임의 적용 없음). **실전 전용**.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload
from ...overseas.entities.balance import OverseasPresentBalance
from .balance import Balance
from .bond_account import BondPosition


@dataclass(frozen=True, slots=True)
class CurrencyDeposit:
    """통합잔고의 통화별 예수금 한 줄(불변). 원화 환율(``exchange_rate``)을 함께 실어 환산이 재현 가능하다."""

    currency: str            # 통화코드 (KRW/USD/...)
    cash: Decimal            # 예수금(해당 통화)
    exchange_rate: Decimal   # 원화 환율(KRW=1)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class IntegratedBalance:
    """세션 계좌(위탁 01)의 국내주식+채권+해외주식 통합 잔고(불변). **실전전용**.

    ``deposits`` 통화별 예수금(진실의 원천, 환율 동반), ``domestic``/``bonds``/``overseas`` 도메인별
    서브잔고(원본 스키마 유지). headline 롤업(``net_liquidation`` 등)은 KIS가 준 원화 집계의 합으로만
    산출한다(환율 임의 적용 없음). 채권 잔고(CTSC8407R)는 시장가가 없어 매입금액 기준이며 ``net_liquidation``
    엔 미포함(``bonds`` 로 별도 확인).
    """

    base_currency: str                             # "KRW"
    deposits: tuple[CurrencyDeposit, ...]          # KRW(국내) + 외화(overseas.currencies)
    domestic: Balance                              # 국내주식 잔고 서브
    bonds: tuple[BondPosition, ...]                # 채권 보유(매입금액 기준)
    overseas: OverseasPresentBalance               # 해외 체결기준현재잔고(통화별+원화집계)
    net_liquidation: Decimal      # 원화 총자산 = domestic.net_asset + overseas.total_asset (주식 도메인, 채권 제외)
    total_evaluation: Decimal     # 원화 총평가 = domestic.total_evaluation + overseas.total_evaluation_amount
    total_unrealized_pnl: Decimal # 원화 총평가손익 = domestic.unrealized_pnl + overseas.total_eval_pnl
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "deposits", tuple(self.deposits))
        object.__setattr__(self, "bonds", tuple(self.bonds))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
