"""금액+통화 -- :class:`Money`.

통화가 섞이는 곳(해외 계좌: USD/HKD/JPY/...)에서 금액이 스스로 통화를 지니게 한다. 국내 금액은
KRW 단일통화라 :class:`~decimal.Decimal` 로 충분해 Money 를 쓰지 않는다 -- Money 는 다통화 도메인용이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Money:
    """금액과 그 통화(불변). ``amount`` 는 통화 단위 그대로의 값, ``currency`` 는 ISO 통화코드(USD 등)."""

    amount: Decimal
    currency: str

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
