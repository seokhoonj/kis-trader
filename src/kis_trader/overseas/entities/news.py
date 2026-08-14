"""해외 뉴스 DATA -- 종목 뉴스 헤드라인.

해외 종목 뉴스 조회가 돌려주는 헤드라인 한 건."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class OverseasNewsHeadline:
    """해외뉴스 종합 피드의 제목 한 건."""

    news_type: str
    key: str
    timestamp: datetime
    category_code: str
    category_name: str
    source: str
    country_code: str
    exchange_code: str
    symbol: str
    symbol_name: str
    title: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
