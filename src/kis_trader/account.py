"""세션 계좌 파사드의 뷰 -- ``kis.account`` 가 상품코드에 따라 반환한다.

위탁(01)/연금저축(22)/IRP(29) 는 같은 국내주식 계좌 엔드포인트를 공유하므로 :class:`StockAccount`
로 다룬다(ISA 는 상품코드 자체가 01). 시장별 뷰 ``.domestic`` / ``.overseas`` 는 기존 계좌 조회
클래스를 그대로 재사용한다(경로만 이동, 동작 불변). IRP(29)는 조회전용이며 퇴직연금 전용 조회는
``.pension`` 렌즈로 준다. 단일 도메인 계좌(파생 등)는 별도 뷰다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .domestic.namespace import DomesticAccount
from .errors import KISUsageError
from .integrated import Balance, make_balance
from .overseas.namespace import OverseasAccount
from .pension.account import PensionAccount

if TYPE_CHECKING:
    from .client import KISClient

#: IRP(개인형 퇴직연금) 상품코드 -- ``.pension`` 렌즈가 붙는 유일한 계좌 종류. "퇴직연금 대상"과
#: "조회전용"(_READ_ONLY_PRODUCT_CODES)은 지금 우연히 {"29"} 로 겹칠 뿐 다른 개념이라 결합하면 안
#: 된다(퇴직연금 렌즈 판별 vs 주문 가능 여부 판별).
_IRP_PRODUCT_CODE = "29"

#: 조회만 가능(주문 불가)한 상품계좌종류(ACNT_PRDT_CD). IRP(29) -- KIS 가 주문 엔드포인트를
#: APBK1744 로 거부한다. 연금저축(22)은 주문 가능이라 여기 없다(IRP 와 혼동 주의). 주문 가능
#: 여부를 상품코드 한 곳에서만 판단하는 단일 출처다 -- :attr:`StockAccount.orderable` 과 세션
#: 백스톱(주문 엔진의 orderable 파생)이 둘 다 이 집합을 쓴다.
_READ_ONLY_PRODUCT_CODES = frozenset({"29"})


class StockAccount:
    """``kis.account`` (위탁 01/연금저축 22/IRP 29) -- 국내/해외 주식 계좌의 시장별 뷰. IRP 는 조회전용 + ``.pension`` 렌즈."""

    #: ``kis.account`` 세 뷰의 판별자. 세 뷰는 공통 표면이 없어(주식은 .domestic/.overseas/.balance,
    #: 파생은 deposit/margin 등) 호출자가 isinstance 대신 ``account.kind`` 로 ``match`` 해 분기한다.
    kind: Literal["stock"] = "stock"

    def __init__(self, client: KISClient) -> None:
        self._client = client
        self._domestic = DomesticAccount(client)
        self._overseas = OverseasAccount(client)

    @property
    def product_code(self) -> str:
        """이 계좌의 상품코드(raw ``ACNT_PRDT_CD``) -- 위탁 01/연금저축 22/IRP 29. ``kind`` 가
        뷰 종류 판별자라면 이건 그 raw 벤더값으로, 소비자가 연금(29)/일반(01)을 예외 분기 없이
        구별할 때 쓴다(예: ``kis.account.product_code == "29"``).

        계좌 미설정 시 :class:`~kis_trader.errors.KISUsageError`."""
        _, product_code = self._client._require_account()
        return product_code

    @property
    def orderable(self) -> bool:
        """이 계좌로 API 주문을 낼 수 있는지 -- 상품계좌종류(ACNT_PRDT_CD)의 주문 가능 여부 **그리고**
        세션의 실효 ``orderable`` 킬스위치를 함께 반영한다. 위탁(01)/ISA(01)/연금저축(22)은 상품
        기준 주문 가능, IRP(29)는 조회전용이라 주문 불가(``False`` -- KIS 가 주문 엔드포인트를 APBK1744 로
        거부). 여기에 더해 세션을 ``KISClient(orderable=False)`` 로 열면 상품코드와 무관하게 주문을 막는다.

        명시적인 ``product_code not in _READ_ONLY_PRODUCT_CODES`` 항은 세션 ``_orderable`` 이 이미 그
        상품-조회전용 판정을 접어(:data:`_READ_ONLY_PRODUCT_CODES`) 들고 있어 **중복**이지만, 조회전용
        계좌 주문 거부가 세션 킬스위치 하나에만 의존하지 않도록 **의도적으로 둔 방어심층**이다(둘 중 어느
        쪽이 바뀌어도 거부가 유지된다).

        상품코드 읽기를 먼저 둬 계좌 미설정이면 :class:`~kis_trader.errors.KISUsageError` 가 난다
        (조회전용 계좌의 문서화된 계약 유지)."""
        return self.product_code not in _READ_ONLY_PRODUCT_CODES and self._client._orderable

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
        :meth:`balance` (국내+채권+해외 잔고 합성; 평가합계는 국내·해외만)와는 계약이 다르다.

        연금저축(22)은 법적으로 퇴직연금이 아닌 별개 사적연금이라 주문 가능한 일반 주식계좌
        (:meth:`domestic`/:meth:`overseas`)로 다루며 이 렌즈 대상이 아니다. DC가입자(55)는 API
        세션 자체가 불가하다. 그래서 상품코드가 29 가 아니면 와이어 전에
        :class:`~kis_trader.errors.KISUsageError` 로 fail-closed 한다."""
        _, product_code = self._client._require_account()
        if product_code != _IRP_PRODUCT_CODE:
            raise KISUsageError(
                f"kis.account.pension 은 IRP(29) 전용이다 -- 현재 상품코드 {product_code}. "
                "연금저축(22)은 별개 사적연금이라 일반 주식계좌(.domestic/.overseas)로 "
                "다루고, DC가입자(55)는 API 세션 자체가 불가하다."
            )
        return PensionAccount(self._client)

    def balance(self) -> Balance:
        """국내주식+채권+해외주식 잔고를 한 :class:`~kis_trader.integrated.Balance`
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
        # I/O 는 여기(세 조회), 합성은 순수 함수 make_balance 가 맡는다(경계 분리).
        dom = self.domestic.balance()
        bonds = tuple(self.domestic.bonds.balance())
        ovs = self.overseas.present_balance()
        return make_balance(dom, bonds, ovs)
