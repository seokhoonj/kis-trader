"""세션 계좌 파사드의 뷰 -- ``kis.account`` 가 상품코드에 따라 반환한다.

위탁(01)/연금저축(22)/IRP(29) 는 같은 국내주식 계좌 엔드포인트를 공유하므로 :class:`StockAccount`
로 다룬다(ISA 는 상품코드 자체가 01). 시장별 뷰 ``.domestic`` / ``.overseas`` 는 기존 계좌 조회
클래스를 그대로 재사용한다(경로만 이동, 동작 불변). IRP(29)는 조회전용이며 퇴직연금 전용 조회는
``.pension`` 렌즈로 준다. 단일 도메인 계좌(파생 등)는 별도 뷰다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from .domestic.namespace import DomesticAccount
from .errors import KISError, KISUsageError
from .integrated import CurrencyDeposit, IntegratedBalance
from .overseas.namespace import OverseasAccount
from .pension.account import PensionAccount

if TYPE_CHECKING:
    from .client import KISClient


class StockAccount:
    """``kis.account`` (위탁 01/연금저축 22/IRP 29) -- 국내/해외 주식 계좌의 시장별 뷰. IRP 는 조회전용 + ``.pension`` 렌즈."""

    def __init__(self, client: KISClient) -> None:
        self._client = client
        self._domestic = DomesticAccount(client)
        self._overseas = OverseasAccount(client)

    @property
    def domestic(self) -> DomesticAccount:
        """국내주식 계좌 조회(잔고/손익/예약주문)."""
        return self._domestic

    @property
    def overseas(self) -> OverseasAccount:
        """해외주식 계좌 조회(잔고/미체결/기간손익 등)."""
        return self._overseas

    @property
    def pension(self) -> PensionAccount:
        """IRP(29) 퇴직연금 전용 조회 렌즈 -- 예수금/매수가능/잔고/체결기준잔고/주문내역.

        개인형 퇴직연금(IRP, 상품코드 29) 전용이다. IRP 는 조회전용(KIS APBK1744 가 주문을
        거부)이라 이 렌즈에도 발주 메서드는 없다. 일반 국내주식 잔고(:meth:`domestic`)엔 없는
        퇴직연금 전용 필드(예수금 요약/체결기준잔고/매수가능여력)를 준다 -- 통합잔고
        :meth:`balance` (국내+채권+해외 평가 합산)와는 계약이 다르다.

        연금저축(22)은 법적으로 퇴직연금이 아닌 별개 사적연금이라 주문 가능한 일반 주식계좌
        (:meth:`domestic`/:meth:`overseas`)로 다루며 이 렌즈 대상이 아니다. DC가입자(55)는 API
        세션 자체가 불가하다. 그래서 상품코드가 29 가 아니면 와이어 전에
        :class:`~kis_trader.errors.KISUsageError` 로 fail-closed 한다."""
        _, product_code = self._client._require_account()
        if product_code != "29":
            raise KISUsageError(
                f"kis.account.pension 은 IRP(29) 전용이다 -- 현재 상품코드 {product_code}. "
                "연금저축(22)은 별개 사적연금이라 일반 주식계좌(.domestic/.overseas)로 "
                "다루고, DC가입자(55)는 API 세션 자체가 불가하다."
            )
        return PensionAccount(self._client)

    def balance(self) -> IntegratedBalance:
        """국내주식+채권+해외주식 잔고를 한 :class:`~kis_trader.integrated.IntegratedBalance`
        로 합친다(새 와이어 없이 세 기존 조회의 합성). 통화별 예수금이 진실의 원천이고,
        ``total_evaluation``/``total_unrealized_pnl`` 은 국내·해외 보유 평가의 순수 원화 합이다(서로 다른
        보유라 겹치지 않는다). 현금까지 더한 단일 총자산은 노출하지 않는다 -- 국내 순자산과 해외
        총자산이 같은 위탁계좌의 원화 예수금을 공유해(이중계상) net/gross 기준이 달라 신뢰 있게 합산할
        수 없기 때문이다(필요하면 ``domestic``/``overseas`` 서브잔고를 직접 본다). 채권 잔고는 매입금액
        기준이라 평가 합계에 넣지 않는다(``bonds`` 로 별도 확인). **모의투자 미지원**(채권/해외
        현재잔고가 실전 전용) -- 모의는 와이어 전에 fail-closed."""
        if self._client.environment == "paper":
            raise KISUsageError(
                "통합잔고(kis.account.balance)는 모의투자 미지원 -- 실전에서만"
                "(채권/해외 현재잔고가 실전 전용)."
            )
        dom = self.domestic.balance()
        bonds = tuple(self.domestic.bonds.balance())
        ovs = self.overseas.present_balance()

        deposits = (
            CurrencyDeposit(currency="KRW", cash=dom.deposit, exchange_rate=Decimal(1)),
            *(
                CurrencyDeposit(
                    currency=c.currency,
                    cash=c.deposit.amount,
                    exchange_rate=c.first_exchange_rate,
                    _raw=c._raw,
                )
                for c in ovs.currencies
                if c.currency != "KRW"  # 원화 예수금은 국내(dom.deposit)가 진실의 원천 -- 중복 행 방지
            ),
        )
        if len({d.currency for d in deposits}) != len(deposits):
            raise KISError("통합잔고 통화별 예수금에 중복 통화가 있다.")
        return IntegratedBalance(
            base_currency="KRW",
            deposits=deposits,
            domestic=dom,
            bonds=bonds,
            overseas=ovs,
            total_evaluation=dom.market_value + ovs.total_evaluation_amount,
            total_unrealized_pnl=dom.unrealized_pnl + ovs.total_unrealized_pnl,
        )
