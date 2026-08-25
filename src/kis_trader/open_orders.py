"""세션 계좌의 미체결 주문을 도메인별로 모은 결과(DATA) -- :class:`OpenOrders`.

``kis.orders.open()`` 이 돌려주는 구조체다. 한 세션은 한 계좌(한 상품코드)라, 그 계좌에
해당하는 도메인만 채워지고 나머지는 빈 튜플이다: 주식계좌(위탁 01/연금저축 22/IRP 29)면
``domestic`` + ``overseas``, 국내선물옵션(03)이면 ``derivatives``. 한 건짜리 국내 미체결
엔티티는 :class:`~kis_trader.open_order.OpenOrder`(단수) 이고, 이 파일은 여러 도메인을 가로질러
모은 집계(복수)다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domestic.entities.derivative_account import DerivativeOpenOrder
from .open_order import OpenOrder
from .overseas.entities.orders import OverseasOpenOrder


@dataclass(frozen=True, slots=True)
class OpenOrders:
    """세션 계좌의 미체결(접수 후 정정·취소 가능) 주문을 도메인별로 모은 결과(불변).

    ``domestic`` 국내주식, ``overseas`` 해외주식, ``derivatives`` 국내선물옵션 미체결. 세션 계좌
    종류에 해당하는 도메인만 차고 나머지는 빈 튜플이다. ``len(result)`` 은 전 도메인 총 건수라
    ``if kis.orders.open():`` 로 미체결 존재 여부를 바로 볼 수 있다. 채권 미체결은 조회에 날짜가
    필요해 여기 넣지 않는다(``kis.account.domestic.bonds.open_orders(order_date)``).
    """

    domestic: tuple[OpenOrder, ...] = ()
    overseas: tuple[OverseasOpenOrder, ...] = ()
    derivatives: tuple[DerivativeOpenOrder, ...] = ()

    def __len__(self) -> int:
        """전 도메인 미체결 주문 총 건수."""
        return len(self.domestic) + len(self.overseas) + len(self.derivatives)
