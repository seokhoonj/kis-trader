"""국내지수 실시간 파서 -- 체결 / 예상체결 / 프로그램매매.

원장 Response Body 필드순을 그대로 ``^`` 인덱스에 매핑한다(필드순이 정본). 자산군 엔티티는
:mod:`..messages` 의 스타일(frozen dataclass, 산업표준 영어 식별자, 한국어 docstring,
``_raw`` = 전체 Element->원문, 숫자는 :class:`~decimal.Decimal`)을 그대로 따른다.

체결(H0UPCNT0)과 예상체결(H0UPANC0)은 동일한 30필드 레이아웃을 공유한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .._registry import TRSpec, register

# H0UPANC0 응답 필드 순서(원장 Response Body). 인덱스 = ^ 위치.
_EXPECTED_CONCLUSION_FIELDS = (
    "BSTP_CLS_CODE", "BSOP_HOUR", "PRPR_NMIX", "PRDY_VRSS_SIGN", "BSTP_NMIX_PRDY_VRSS",
    "ACML_VOL", "ACML_TR_PBMN", "PCAS_VOL", "PCAS_TR_PBMN", "PRDY_CTRT",
    "OPRC_NMIX", "NMIX_HGPR", "NMIX_LWPR", "OPRC_VRSS_NMIX_PRPR", "OPRC_VRSS_NMIX_SIGN",
    "HGPR_VRSS_NMIX_PRPR", "HGPR_VRSS_NMIX_SIGN", "LWPR_VRSS_NMIX_PRPR", "LWPR_VRSS_NMIX_SIGN", "PRDY_CLPR_VRSS_OPRC_RATE",
    "PRDY_CLPR_VRSS_HGPR_RATE", "PRDY_CLPR_VRSS_LWPR_RATE", "UPLM_ISSU_CNT", "ASCN_ISSU_CNT", "STNR_ISSU_CNT",
    "DOWN_ISSU_CNT", "LSLM_ISSU_CNT", "QTQT_ASCN_ISSU_CNT", "QTQT_DOWN_ISSU_CNT", "TICK_VRSS",
)

# H0UPPGM0 응답 필드 순서(원장 Response Body). 인덱스 = ^ 위치.
_PROGRAM_TRADE_FIELDS = (
    "BSTP_CLS_CODE", "BSOP_HOUR", "ARBT_SELN_ENTM_CNQN", "ARBT_SELN_ONSL_CNQN", "ARBT_SHNU_ENTM_CNQN",
    "ARBT_SHNU_ONSL_CNQN", "NABT_SELN_ENTM_CNQN", "NABT_SELN_ONSL_CNQN", "NABT_SHNU_ENTM_CNQN", "NABT_SHNU_ONSL_CNQN",
    "ARBT_SELN_ENTM_CNTG_AMT", "ARBT_SELN_ONSL_CNTG_AMT", "ARBT_SHNU_ENTM_CNTG_AMT", "ARBT_SHNU_ONSL_CNTG_AMT", "NABT_SELN_ENTM_CNTG_AMT",
    "NABT_SELN_ONSL_CNTG_AMT", "NABT_SHNU_ENTM_CNTG_AMT", "NABT_SHNU_ONSL_CNTG_AMT", "ARBT_SMTN_SELN_VOL", "ARBT_SMTM_SELN_VOL_RATE",
    "ARBT_SMTN_SELN_TR_PBMN", "ARBT_SMTM_SELN_TR_PBMN_RATE", "ARBT_SMTN_SHNU_VOL", "ARBT_SMTM_SHNU_VOL_RATE", "ARBT_SMTN_SHNU_TR_PBMN",
    "ARBT_SMTM_SHNU_TR_PBMN_RATE", "ARBT_SMTN_NTBY_QTY", "ARBT_SMTM_NTBY_QTY_RATE", "ARBT_SMTN_NTBY_TR_PBMN", "ARBT_SMTM_NTBY_TR_PBMN_RATE",
    "NABT_SMTN_SELN_VOL", "NABT_SMTM_SELN_VOL_RATE", "NABT_SMTN_SELN_TR_PBMN", "NABT_SMTM_SELN_TR_PBMN_RATE", "NABT_SMTN_SHNU_VOL",
    "NABT_SMTM_SHNU_VOL_RATE", "NABT_SMTN_SHNU_TR_PBMN", "NABT_SMTM_SHNU_TR_PBMN_RATE", "NABT_SMTN_NTBY_QTY", "NABT_SMTM_NTBY_QTY_RATE",
    "NABT_SMTN_NTBY_TR_PBMN", "NABT_SMTM_NTBY_TR_PBMN_RATE", "WHOL_ENTM_SELN_VOL", "ENTM_SELN_VOL_RATE", "WHOL_ENTM_SELN_TR_PBMN",
    "ENTM_SELN_TR_PBMN_RATE", "WHOL_ENTM_SHNU_VOL", "ENTM_SHNU_VOL_RATE", "WHOL_ENTM_SHNU_TR_PBMN", "ENTM_SHNU_TR_PBMN_RATE",
    "WHOL_ENTM_NTBY_QT", "ENTM_NTBY_QTY_RAT", "WHOL_ENTM_NTBY_TR_PBMN", "ENTM_NTBY_TR_PBMN_RATE", "WHOL_ONSL_SELN_VOL",
    "ONSL_SELN_VOL_RATE", "WHOL_ONSL_SELN_TR_PBMN", "ONSL_SELN_TR_PBMN_RATE", "WHOL_ONSL_SHNU_VOL", "ONSL_SHNU_VOL_RATE",
    "WHOL_ONSL_SHNU_TR_PBMN", "ONSL_SHNU_TR_PBMN_RATE", "WHOL_ONSL_NTBY_QTY", "ONSL_NTBY_QTY_RATE", "WHOL_ONSL_NTBY_TR_PBMN",
    "ONSL_NTBY_TR_PBMN_RATE", "TOTAL_SELN_QTY", "WHOL_SELN_VOL_RATE", "TOTAL_SELN_TR_PBMN", "WHOL_SELN_TR_PBMN_RATE",
    "SHNU_CNTG_SMTN", "WHOL_SHUN_VOL_RATE", "TOTAL_SHNU_TR_PBMN", "WHOL_SHUN_TR_PBMN_RATE", "WHOL_NTBY_QTY",
    "WHOL_SMTM_NTBY_QTY_RATE", "WHOL_NTBY_TR_PBMN", "WHOL_NTBY_TR_PBMN_RATE", "ARBT_ENTM_NTBY_QTY", "ARBT_ENTM_NTBY_TR_PBMN",
    "ARBT_ONSL_NTBY_QTY", "ARBT_ONSL_NTBY_TR_PBMN", "NABT_ENTM_NTBY_QTY", "NABT_ENTM_NTBY_TR_PBMN", "NABT_ONSL_NTBY_QTY",
    "NABT_ONSL_NTBY_TR_PBMN", "ACML_VOL", "ACML_TR_PBMN",
)


def _decimal(value: str) -> Decimal:
    """실시간 숫자 필드 -> Decimal. 빈 값/파싱 불가는 0 으로(스트림 중단 방지)."""
    try:
        return Decimal(value) if value else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


@dataclass(frozen=True, slots=True)
class ExpectedConclusion:
    """국내지수 실시간 예상체결(H0UPANC0). 장 전/후 동시호가의 예상 지수/등락/거래량 등.

    전체 30개 필드는 ``_raw`` (KIS Element 이름 기준)에 있고, 아래는 자주 쓰는 헤드라인만
    타입화한 것이다.
    """

    sector_code: str  # 업종 구분 코드
    time: str  # HHMMSS
    expected_index: Decimal  # 현재가 지수(예상)
    change_sign: str  # 전일 대비 부호 1상한 2상승 3보합 4하한 5하락
    change: Decimal  # 업종 지수 전일 대비
    change_percent: Decimal  # 전일 대비율
    accumulated_volume: Decimal
    accumulated_value: Decimal  # 누적 거래대금
    open: Decimal  # 시가 지수
    high: Decimal  # 지수 최고가
    low: Decimal  # 지수 최저가
    rising_count: Decimal  # 상승 종목 수
    unchanged_count: Decimal  # 보합 종목 수
    falling_count: Decimal  # 하락 종목 수
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


@dataclass(frozen=True, slots=True)
class IndexTick:
    """국내지수 실시간체결(H0UPCNT0). 실시간 지수 레벨·등락·거래량·등락종목수(breadth).

    예상체결(:class:`ExpectedConclusion`, H0UPANC0)과 동일한 30필드 레이아웃을 공유하되
    값이 예상이 아닌 실제 체결 지수다. 전체 필드는 ``_raw`` 에 있다.
    """

    sector_code: str  # 업종 구분 코드
    time: str  # HHMMSS
    index_value: Decimal  # 현재가 지수
    change_sign: str  # 전일 대비 부호 1상한 2상승 3보합 4하한 5하락
    change: Decimal  # 업종 지수 전일 대비
    change_percent: Decimal  # 전일 대비율
    accumulated_volume: Decimal
    accumulated_value: Decimal  # 누적 거래대금
    open: Decimal  # 시가 지수
    high: Decimal  # 지수 최고가
    low: Decimal  # 지수 최저가
    rising_count: Decimal  # 상승 종목 수
    unchanged_count: Decimal  # 보합 종목 수
    falling_count: Decimal  # 하락 종목 수
    upper_limit_count: Decimal  # 상한 종목 수
    lower_limit_count: Decimal  # 하한 종목 수
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


@dataclass(frozen=True, slots=True)
class ProgramTrade:
    """국내지수 실시간 프로그램매매(H0UPPGM0). 차익/비차익, 위탁/자기, 순매수 수량/대금 등.

    전체 88개 필드는 ``_raw`` (KIS Element 이름 기준)에 있고, 아래는 자주 쓰는 헤드라인만
    타입화한 것이다.
    """

    sector_code: str  # 업종 구분 코드
    time: str  # HHMMSS
    total_sell_quantity: Decimal  # 총 매도 수량
    total_buy_quantity: Decimal  # 총 매수 수량(SHNU_CNTG_SMTN)
    whole_net_buy_quantity: Decimal  # 전체 순매수 수량
    whole_net_buy_value: Decimal  # 전체 순매수 거래 대금
    arbitrage_net_buy_quantity: Decimal  # 차익 합계 순매수 수량
    nonarbitrage_net_buy_quantity: Decimal  # 비차익 합계 순매수 수량
    accumulated_volume: Decimal
    accumulated_value: Decimal  # 누적 거래대금
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


def parse_expected_conclusion(fields: list[str]) -> ExpectedConclusion:
    """H0UPANC0 한 레코드(30필드) -> :class:`ExpectedConclusion`."""
    raw = MappingProxyType(dict(zip(_EXPECTED_CONCLUSION_FIELDS, fields, strict=False)))
    return ExpectedConclusion(
        sector_code=raw["BSTP_CLS_CODE"],
        time=raw["BSOP_HOUR"],
        expected_index=_decimal(raw["PRPR_NMIX"]),
        change_sign=raw["PRDY_VRSS_SIGN"],
        change=_decimal(raw["BSTP_NMIX_PRDY_VRSS"]),
        change_percent=_decimal(raw["PRDY_CTRT"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        accumulated_value=_decimal(raw["ACML_TR_PBMN"]),
        open=_decimal(raw["OPRC_NMIX"]),
        high=_decimal(raw["NMIX_HGPR"]),
        low=_decimal(raw["NMIX_LWPR"]),
        rising_count=_decimal(raw["ASCN_ISSU_CNT"]),
        unchanged_count=_decimal(raw["STNR_ISSU_CNT"]),
        falling_count=_decimal(raw["DOWN_ISSU_CNT"]),
        _raw=raw,
    )


def parse_index_tick(fields: list[str]) -> IndexTick:
    """H0UPCNT0 한 레코드(30필드) -> :class:`IndexTick`.

    예상체결(H0UPANC0)과 동일한 30필드 레이아웃이라 그 튜플을 공유한다.
    """
    raw = MappingProxyType(dict(zip(_EXPECTED_CONCLUSION_FIELDS, fields, strict=False)))
    return IndexTick(
        sector_code=raw["BSTP_CLS_CODE"],
        time=raw["BSOP_HOUR"],
        index_value=_decimal(raw["PRPR_NMIX"]),
        change_sign=raw["PRDY_VRSS_SIGN"],
        change=_decimal(raw["BSTP_NMIX_PRDY_VRSS"]),
        change_percent=_decimal(raw["PRDY_CTRT"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        accumulated_value=_decimal(raw["ACML_TR_PBMN"]),
        open=_decimal(raw["OPRC_NMIX"]),
        high=_decimal(raw["NMIX_HGPR"]),
        low=_decimal(raw["NMIX_LWPR"]),
        rising_count=_decimal(raw["ASCN_ISSU_CNT"]),
        unchanged_count=_decimal(raw["STNR_ISSU_CNT"]),
        falling_count=_decimal(raw["DOWN_ISSU_CNT"]),
        upper_limit_count=_decimal(raw["UPLM_ISSU_CNT"]),
        lower_limit_count=_decimal(raw["LSLM_ISSU_CNT"]),
        _raw=raw,
    )


def parse_program_trade(fields: list[str]) -> ProgramTrade:
    """H0UPPGM0 한 레코드(88필드) -> :class:`ProgramTrade`."""
    raw = MappingProxyType(dict(zip(_PROGRAM_TRADE_FIELDS, fields, strict=False)))
    return ProgramTrade(
        sector_code=raw["BSTP_CLS_CODE"],
        time=raw["BSOP_HOUR"],
        total_sell_quantity=_decimal(raw["TOTAL_SELN_QTY"]),
        total_buy_quantity=_decimal(raw["SHNU_CNTG_SMTN"]),
        whole_net_buy_quantity=_decimal(raw["WHOL_NTBY_QTY"]),
        whole_net_buy_value=_decimal(raw["WHOL_NTBY_TR_PBMN"]),
        arbitrage_net_buy_quantity=_decimal(raw["ARBT_SMTN_NTBY_QTY"]),
        nonarbitrage_net_buy_quantity=_decimal(raw["NABT_SMTN_NTBY_QTY"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        accumulated_value=_decimal(raw["ACML_TR_PBMN"]),
        _raw=raw,
    )


register(TRSpec("H0UPCNT0", field_count=len(_EXPECTED_CONCLUSION_FIELDS), parser=parse_index_tick))
register(TRSpec("H0UPANC0", field_count=len(_EXPECTED_CONCLUSION_FIELDS), parser=parse_expected_conclusion))
register(TRSpec("H0UPPGM0", field_count=len(_PROGRAM_TRADE_FIELDS), parser=parse_program_trade))
