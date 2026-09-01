"""통합잔고(DATA) -- :class:`CurrencyDeposit`, :class:`IntegratedBalance`.

세션 계좌(위탁 01)의 국내주식+채권+해외주식 잔고를 한 뷰로 합친 결과다
(``kis.account.balance()`` 가 돌려준다). 새 와이어는 없다 -- 기존 세 조회의 합성이다.
통화별 예수금(:class:`CurrencyDeposit`)이 진실의 원천이고, 평가 합계는 겹치지 않는 국내·해외
보유의 순수 원화 합으로만 산출한다. **실전 전용**.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._internal._freeze import freeze_vendor_payload
from .domestic.entities.balance import Balance
from .domestic.entities.bond_account import BondPosition
from .errors import KISError
from .overseas.entities.balance import OverseasPresentBalance


@dataclass(frozen=True, slots=True)
class CurrencyDeposit:
    """통합잔고의 통화별 예수금 한 줄(불변). ``exchange_rate`` 는 참고용 최초고시환율(원화 환산의
    대략치)이지 KIS 원화 집계에 쓰인 환율은 아니다 -- ``cash * exchange_rate`` 가 집계와 정확히
    맞지는 않는다."""

    currency: str            # 통화코드 (KRW/USD/...)
    cash: Decimal            # 예수금(해당 통화)
    exchange_rate: Decimal   # 참고용 원화 환율(KRW=1; 외화는 최초고시환율)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class IntegratedBalance:
    """세션 계좌(위탁 01)의 국내주식+채권+해외주식 통합 잔고(불변). **실전전용**.

    ``deposits`` 통화별 예수금(진실의 원천, 환율 동반), ``domestic``/``bonds``/``overseas`` 도메인별
    서브잔고(원본 스키마 유지). ``total_evaluation``/``total_unrealized_pnl`` 은 겹치지 않는 국내·해외
    보유 평가의 순수 원화 합이다. 현금까지 더한 단일 총자산은 두지 않는다 -- 국내 순자산과 해외 총자산이
    같은 위탁계좌의 원화 예수금을 공유(이중계상)하고 net/gross 기준이 달라 신뢰 있게 못 합치기 때문이다
    (필요하면 서브잔고를 직접 본다). 채권 잔고(CTSC8407R)는 시장가가 없어 매입금액 기준이라 평가 합계에
    넣지 않는다(``bonds`` 로 별도 확인).
    """

    base_currency: str                             # "KRW"
    deposits: tuple[CurrencyDeposit, ...]          # KRW(국내) + 외화(overseas.currencies, KRW 행 제외)
    domestic: Balance                              # 국내주식 잔고 서브
    bonds: tuple[BondPosition, ...]                # 채권 보유(매입금액 기준)
    overseas: OverseasPresentBalance               # 해외 체결기준현재잔고(통화별+원화집계)
    total_evaluation: Decimal     # 원화 총평가 = domestic.market_value + overseas.total_evaluation_amount
    total_unrealized_pnl: Decimal # 원화 총평가손익 = domestic.unrealized_pnl + overseas.total_unrealized_pnl
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "deposits", tuple(self.deposits))
        object.__setattr__(self, "bonds", tuple(self.bonds))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


def make_integrated_balance(
    domestic: Balance,
    bonds: tuple[BondPosition, ...],
    overseas: OverseasPresentBalance,
) -> IntegratedBalance:
    """세 도메인 잔고(국내주식·채권·해외현재잔고)를 한 :class:`IntegratedBalance` 로 합성한다 -- 순수
    (I/O 0, 주입한 값만으로 결정적). 통화별 예수금은 KRW=국내(``domestic.deposit``)를 진실의 원천으로
    두고 해외 통화행을 더하되 KRW 행 중복을 제거하며, ``total_evaluation``/``total_unrealized_pnl`` 은
    겹치지 않는 국내·해외 보유 평가의 순수 원화 합이다. 중복 통화가 생기면
    :class:`~kis_trader.errors.KISError`. I/O(세 조회 + 모의 게이트)는 :meth:`~kis_trader.account.
    StockAccount.balance` 가 맡고 이 함수는 합성만 한다."""
    deposits = (
        CurrencyDeposit(currency="KRW", cash=domestic.deposit, exchange_rate=Decimal(1)),
        *(
            CurrencyDeposit(
                currency=c.currency,
                cash=c.deposit.amount,
                exchange_rate=c.first_exchange_rate,
                _raw=c._raw,
            )
            for c in overseas.currencies
            if c.currency != "KRW"  # 원화 예수금은 국내(domestic.deposit)가 진실의 원천 -- 중복 행 방지
        ),
    )
    if len({d.currency for d in deposits}) != len(deposits):
        raise KISError("통합잔고 통화별 예수금에 중복 통화가 있다.")
    return IntegratedBalance(
        base_currency="KRW",
        deposits=deposits,
        domestic=domestic,
        bonds=bonds,
        overseas=overseas,
        total_evaluation=domestic.market_value + overseas.total_evaluation_amount,
        total_unrealized_pnl=domestic.unrealized_pnl + overseas.total_unrealized_pnl,
    )
