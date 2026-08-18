"""해외 아시아(홍콩/중국/일본/베트남) 예약주문 -- PRDT_TYPE_CD 파생.

원장(KIS 공식 전체문서, 시트 `해외주식 예약주문접수/조회/접수취소`) 기준: 아시아 예약
발주(TTTS3013U)는 상품유형코드(PRDT_TYPE_CD)가 필수이며 거래소에서 파생된다 --
515 일본 / 551 상해A / 552 심천A / 507 하노이 / 508 호치민, 홍콩만 통화별
(501 HKD / 543 CNY / 558 USD).
"""

import pytest

from kis_trader.errors import KISUsageError
from kis_trader.overseas._engine import reserved_orders as ro


def test_asia_prdt_type_cd_by_exchange():
    assert ro._asia_prdt_type_cd("TSE", "HKD") == "515"   # 일본
    assert ro._asia_prdt_type_cd("SHS", "HKD") == "551"   # 중국 상해A
    assert ro._asia_prdt_type_cd("SZS", "HKD") == "552"   # 중국 심천A
    assert ro._asia_prdt_type_cd("HNX", "HKD") == "507"   # 베트남 하노이
    assert ro._asia_prdt_type_cd("HSX", "HKD") == "508"   # 베트남 호치민


def test_asia_prdt_type_cd_hong_kong_currency():
    assert ro._asia_prdt_type_cd("HKS", "HKD") == "501"
    assert ro._asia_prdt_type_cd("HKS", "CNY") == "543"
    assert ro._asia_prdt_type_cd("HKS", "USD") == "558"


def test_asia_prdt_type_cd_hong_kong_rejects_unknown_currency():
    with pytest.raises(KISUsageError, match="HKD/CNY/USD"):
        ro._asia_prdt_type_cd("HKS", "JPY")


def test_asia_prdt_type_cd_non_hk_rejects_non_hkd_currency():
    with pytest.raises(KISUsageError, match="통화"):
        ro._asia_prdt_type_cd("TSE", "CNY")   # 통화 지정은 홍콩 전용


def test_asia_prdt_type_cd_unknown_exchange_rejected():
    with pytest.raises(KISUsageError, match="거래소"):
        ro._asia_prdt_type_cd("NAS", "HKD")   # 미국은 아시아 파생 대상이 아니다
