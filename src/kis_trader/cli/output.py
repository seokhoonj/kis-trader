"""CLI 출력 렌더링 -- frozen dataclass 결과를 표(사람용) 또는 JSON(기계용)으로.

여기서는 **표현만** 한다. 도메인 값(합계·비율·순위)을 새로 만들지 않고, 패키지가 준 필드를
고를/포맷할 뿐이다. 직렬화는 공개 필드만 걷고 이름을 보존하며, ``_raw`` 원본은 ``include_raw``
를 명시할 때만 싣는다(불안정한 벤더 스키마·계좌식별값 노출 방지).
"""
from __future__ import annotations

import dataclasses
import json
import unicodedata
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, Literal


def to_jsonable(value: Any, *, include_raw: bool = False) -> Any:
    """dataclass/Decimal/날짜/Enum 을 JSON 직렬화 가능한 값으로. 정밀도 보존을 위해 Decimal 은
    문자열, 날짜는 ISO 8601, Enum 은 공개 value, 결측은 ``None``."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item, include_raw=include_raw) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(val, include_raw=include_raw) for key, val in value.items()}
    if dataclasses.is_dataclass(value):
        out: dict[str, Any] = {}
        for field in dataclasses.fields(value):
            if field.name.startswith("_"):
                continue
            out[field.name] = to_jsonable(getattr(value, field.name), include_raw=include_raw)
        if include_raw and hasattr(value, "_raw"):
            out["_raw"] = value._raw
        return out
    return str(value)


def _cell(value: Any) -> str:
    """표 한 칸용 스칼라 문자열. 중첩 구조는 compact JSON 으로 접는다."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (str, int, float)):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # Money 같은 소형 값객체는 "amount currency" 로, 그 외는 compact JSON.
        fields = [f.name for f in dataclasses.fields(value) if not f.name.startswith("_")]
        if fields == ["amount", "currency"]:
            return f"{value.amount} {value.currency}"
        return json.dumps(to_jsonable(value), ensure_ascii=False, separators=(",", ":"))
    return json.dumps(to_jsonable(value), ensure_ascii=False, separators=(",", ":"))


def _public_fields(obj: Any) -> list[str]:
    return [f.name for f in dataclasses.fields(obj) if not f.name.startswith("_")]


def _display_width(text: str) -> int:
    """터미널 표시 폭 -- 한글·전각(CJK)은 한 글자가 두 칸을 차지한다. 열 정렬을 문자 수가 아니라
    실제 표시 폭으로 맞춰, 종목명 등 한글이 섞여도 어긋나지 않게 한다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    """``text`` 를 표시 폭 ``width`` 까지 오른쪽 공백으로 채운다(CJK 폭 반영)."""
    return text + " " * max(0, width - _display_width(text))


def _render_table(value: Any, *, no_header: bool) -> str:
    # 리스트(행 모음) -> 컬럼 정렬 격자. 단일 dataclass -> key/value 2열.
    if isinstance(value, (list, tuple)):
        rows = list(value)
        if not rows:
            return "(빈 결과)"
        if not (dataclasses.is_dataclass(rows[0]) and not isinstance(rows[0], type)):
            return "\n".join(_cell(item) for item in rows)
        columns = _public_fields(rows[0])
        table = [[_cell(getattr(row, col)) for col in columns] for row in rows]
        widths = [_display_width(col) for col in columns]
        for record in table:
            for i, cell in enumerate(record):
                widths[i] = max(widths[i], _display_width(cell))
        lines = []
        if not no_header:
            lines.append("  ".join(_pad(col, widths[i]) for i, col in enumerate(columns)).rstrip())
        for record in table:
            lines.append("  ".join(_pad(cell, widths[i]) for i, cell in enumerate(record)).rstrip())
        return "\n".join(lines)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        columns = _public_fields(value)
        width = max((_display_width(col) for col in columns), default=0)
        return "\n".join(f"{_pad(col, width)}  {_cell(getattr(value, col))}" for col in columns)
    if isinstance(value, dict):
        width = max((_display_width(str(key)) for key in value), default=0)
        return "\n".join(f"{_pad(str(key), width)}  {_cell(val)}" for key, val in value.items())
    return _cell(value)


def render(
    value: Any, *, fmt: Literal["table", "json", "jsonl"] = "table", include_raw: bool = False,
    no_header: bool = False, meta: dict[str, Any] | None = None,
) -> str:
    """결과 객체를 최종 문자열로. ``fmt`` = ``table``/``json``/``jsonl``."""
    if fmt == "json":
        return json.dumps(
            {"ok": True, "data": to_jsonable(value, include_raw=include_raw), "meta": meta or {}},
            ensure_ascii=False, indent=2,
        )
    if fmt == "jsonl":
        items = value if isinstance(value, (list, tuple)) else [value]
        return "\n".join(
            json.dumps(to_jsonable(item, include_raw=include_raw), ensure_ascii=False)
            for item in items
        )
    return _render_table(value, no_header=no_header)
