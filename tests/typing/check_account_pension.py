"""정적 타입 픽스처 -- `.venv/bin/mypy --strict tests/typing/check_account_pension.py` 로 검사.

`kis.account` 유니온을 StockAccount 로 좁힌 뒤 `.pension` 이 PensionAccount 로 정합하는지 확인한다.
런타임 실행 대상이 아니다(assert_type 는 정적 전용).
"""

from typing import assert_type

from kis_trader.account import StockAccount
from kis_trader.client import KISClient
from kis_trader.pension.account import PensionAccount


def _check(kis: KISClient) -> None:
    view = kis.account
    assert isinstance(view, StockAccount)
    assert_type(view.pension, PensionAccount)
