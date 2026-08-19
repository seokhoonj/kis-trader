"""해외 계좌 잔고 DATA -- 보유 종목·통화별 잔고·현재/결제 기준 잔고.

해외 잔고 조회(``kis.account.overseas.positions`` / ``.balance`` / ``.present_balance`` /
``.settlement_balance``)가 돌려준다. 금액은 종목/조회 통화라 :class:`~kis_trader.money.Money`
로 통화를 함께 담는다(다통화)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload
from ...money import Money


@dataclass(frozen=True, slots=True)
class OverseasPosition:
    """해외 보유 종목 한 건(불변). 금액은 종목 통화의 :class:`Money`.

    ``quantity`` 는 보유 수량, ``sellable_quantity`` 는 매도가능 수량. ``unrealized_pnl`` 은 외화
    평가손익, ``pnl_percent`` 는 평가손익률(%).
    """

    symbol: str
    name: str
    exchange: str                     # 조회한 해외거래소코드(OVRS_EXCG_CD)
    quantity: int                     # 보유 수량
    sellable_quantity: int            # 매도가능 수량
    average_price: Money              # 매입 평균가
    current_price: Money              # 현재가
    purchase_amount: Money            # 외화 매입금액
    market_value: Money               # 평가금액
    unrealized_pnl: Money             # 외화 평가손익
    pnl_percent: Decimal              # 평가손익률(%)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasBalance:
    """해외 계좌 손익 요약(불변). 조회한 거래소 그룹+통화 기준. 금액은 :class:`Money`.

    ``purchase_amount`` 보유분 매입금액, ``unrealized_pnl`` 평가손익, ``realized_pnl`` 실현손익,
    ``total_pnl`` 총손익(실현+평가), ``return_percent`` 총수익률(%). 예수금(현금)은 별도 조회다.
    """

    exchange: str                     # 조회한 해외거래소코드(OVRS_EXCG_CD)
    purchase_amount: Money            # 외화 매입금액
    unrealized_pnl: Money             # 평가손익
    realized_pnl: Money               # 실현손익
    total_pnl: Money                  # 총손익(실현+평가)
    return_percent: Decimal           # 총수익률(%)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasBalancePosition:
    """해외 잔고 리포트(체결기준/결제기준)의 보유 종목 한 줄(불변). 금액은 매수통화(``currency``)
    :class:`~kis_trader.money.Money`. ``collateral_quantity`` 는 결제기준잔고에만 채워진다(체결기준은 0).

    .. note:: 결제기준잔고(:class:`OverseasSettlementBalance`)는 KIS 응답예시로 확증됐고, 체결기준
       (:class:`OverseasPresentBalance`)은 예시가 output1 까지만 있어 output2/3 요약이 레이아웃 기준이다.
    """

    symbol: str                        # 상품번호(pdno)
    name: str                          # 상품명(prdt_name)
    balance_quantity: Decimal          # 잔고수량(cblc_qty13)
    orderable_quantity: Decimal        # 주문가능수량(ord_psbl_qty1)
    average_price: Money               # 평균단가(avg_unpr3)
    current_price: Money               # 해외현재가격(ovrs_now_pric1)
    purchase_amount: Money             # 외화매입금액(frcr_pchs_amt)
    market_value: Money                # 외화평가금액(frcr_evlu_amt2)
    unrealized_pnl: Money              # 평가손익금액(evlu_pfls_amt2)
    unrealized_pnl_rate: Decimal       # 평가손익율(evlu_pfls_rt1)
    loan_balance: Money                # 대출잔액(loan_rmnd)
    collateral_quantity: Decimal       # 담보수량(mgge_qty; 결제기준만)
    exchange: str                      # 해외거래소코드(ovrs_excg_cd)
    market_name: str                   # 거래시장명(tr_mket_name)
    country_name: str                  # 국가한글명(natn_kor_name)
    currency: str                      # 매수통화코드(buy_crcy_cd)
    exchange_rate: Decimal             # 기준환율(bass_exrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasCurrencyBalance:
    """해외 잔고 리포트의 통화별 예수금 한 줄(불변). ``deposit`` 은 외화예수금(``currency`` Money)."""

    currency: str                      # 통화코드(crcy_cd)
    currency_name: str                 # 통화코드명(crcy_cd_name)
    deposit: Money                     # 외화예수금(frcr_dncl_amt_2)
    first_exchange_rate: Decimal       # 최초고시환율(frst_bltn_exrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasPresentBalance:
    """해외주식 체결기준현재잔고(불변) -- 보유 종목·통화별 예수금·계좌 요약. 실전은 3블록 전부,
    모의(VTRP6504R)는 요약(``_raw``)만 온다.

    .. note:: 요약(output3) 필드는 KIS 예시가 output1 에서 잘려 레이아웃 기준이다 -- 전체 원본은 ``_raw``.
    """

    positions: tuple[OverseasBalancePosition, ...]
    currencies: tuple[OverseasCurrencyBalance, ...]
    total_purchase_amount: Decimal     # 매입금액합계금액(pchs_amt_smtl_amt), 원화
    total_evaluation_amount: Decimal   # 평가금액합계금액(evlu_amt_smtl_amt), 원화
    total_unrealized_pnl: Decimal      # 총평가손익금액(tot_evlu_pfls_amt), 원화
    total_asset: Decimal               # 총자산금액(tot_asst_amt), 원화
    eval_return_rate: Decimal          # 평가수익율(evlu_erng_rt1)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "currencies", tuple(self.currencies))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasSettlementBalance:
    """해외주식 결제기준잔고(불변) -- 기준일자(``BASS_DT``) 결제 기준의 보유 종목·통화별 예수금·계좌
    요약. **모의투자 미지원**. KIS 응답예시로 필드 전량 확증됨(단 ``_raw`` 는 대여평가 등 추가 필드 포함)."""

    positions: tuple[OverseasBalancePosition, ...]
    currencies: tuple[OverseasCurrencyBalance, ...]
    total_purchase_amount: Decimal     # 매입금액합계금액(pchs_amt_smtl_amt), 원화
    total_unrealized_pnl: Decimal      # 총평가손익금액(tot_evlu_pfls_amt), 원화
    eval_return_rate: Decimal          # 평가수익율(evlu_erng_rt1)
    total_deposit: Decimal             # 총예수금액(tot_dncl_amt), 원화
    total_won_evaluation: Decimal      # 원화평가금액합계(wcrc_evlu_amt_smtl)
    total_asset: Decimal               # 총자산금액(tot_asst_amt2), 원화
    total_loan: Decimal                # 총대출금액(tot_loan_amt), 원화
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "currencies", tuple(self.currencies))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
