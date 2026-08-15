"""CLI 출력 렌더링 -- frozen dataclass 결과를 표(사람용) 또는 JSON(기계용)으로.

여기서는 **표현만** 한다. 도메인 값(합계·비율·순위)을 새로 만들지 않고, 패키지가 준 필드를
고를/포맷할 뿐이다. 직렬화는 공개 필드만 걷고 이름을 보존하며, ``_raw`` 원본은 ``include_raw``
를 명시할 때만 싣는다(불안정한 벤더 스키마·계좌식별값 노출 방지).
"""
from __future__ import annotations

import dataclasses
import json
import unicodedata
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

#: KIS 데이터는 KST(+09:00) 고정 오프셋으로 온다 -- 사람용 표에선 이를 "KST" 라벨로 보인다.
_KST_OFFSET = timedelta(hours=9)


def _tz_label(value: datetime) -> str:
    """사람용 표의 시각 뒤에 붙일 타임존 라벨(앞 공백 포함). +09:00 은 `KST`, 그 밖의 오프셋은
    `+HH:MM`, naive(오프셋 없음)면 빈 문자열."""
    offset = value.utcoffset()
    if offset is None:
        return ""
    if offset == _KST_OFFSET:
        return " KST"
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    hours, minutes = divmod(abs(total) // 60, 60)
    return f" {sign}{hours:02d}:{minutes:02d}"


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
    if isinstance(value, datetime):
        # 사람용 표: `2026-08-15 19:57:45 KST` (T 대신 공백, 초 단위, 오프셋을 라벨로).
        # 기계용 JSON 은 to_jsonable 이 ISO 8601 전체를 그대로 싣는다(마이크로초·오프셋 보존).
        return f"{value.strftime('%Y-%m-%d %H:%M:%S')}{_tz_label(value)}"
    if isinstance(value, (date, time)):
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


def _is_record_list(value: Any) -> bool:
    """비어있지 않은, dataclass/dict 레코드들의 리스트인가 -- 표 안에서 JSON 으로 접지 않고
    들여쓴 하위 표로 펼칠 대상(예: 호가창의 bids/asks, 리포트의 종목/통화 행)."""
    if not isinstance(value, (list, tuple)) or not value:
        return False
    first = value[0]
    return isinstance(first, dict) or (dataclasses.is_dataclass(first) and not isinstance(first, type))


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _display_width(text: str) -> int:
    """터미널 표시 폭 -- 한글·전각(CJK)은 한 글자가 두 칸을 차지한다. 열 정렬을 문자 수가 아니라
    실제 표시 폭으로 맞춰, 종목명 등 한글이 섞여도 어긋나지 않게 한다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    """``text`` 를 표시 폭 ``width`` 까지 오른쪽 공백으로 채운다(좌측 정렬, CJK 폭 반영)."""
    return text + " " * max(0, width - _display_width(text))


def _align(text: str, width: int, *, right: bool) -> str:
    """표시 폭 ``width`` 로 정렬 -- ``right`` 면 앞을 채워 우측 정렬, 아니면 좌측(CJK 폭 반영)."""
    fill = " " * max(0, width - _display_width(text))
    return fill + text if right else text + fill


def _is_numeric(value: Any) -> bool:
    """숫자 값인가(우측 정렬 대상). ``bool`` 은 숫자로 보지 않는다(True/False 는 좌측)."""
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _render_table(value: Any, *, no_header: bool) -> str:
    # 리스트(행 모음) -> 컬럼 정렬 격자. 단일 dataclass -> key/value 2열.
    if isinstance(value, (list, tuple)):
        rows = list(value)
        if not rows:
            return "(빈 결과)"
        first = rows[0]
        if dataclasses.is_dataclass(first) and not isinstance(first, type):
            columns = _public_fields(first)
            raw = [[getattr(row, col) for col in columns] for row in rows]
        elif isinstance(first, dict):
            # dict 행 -> 첫 행의 키 순서를 컬럼으로(호가창 사다리처럼 임의 헤더가 필요할 때).
            columns = list(first.keys())
            raw = [[row.get(col) for col in columns] for row in rows]
        else:
            return "\n".join(_cell(item) for item in rows)
        table = [[_cell(cell) for cell in record] for record in raw]
        widths = [_display_width(col) for col in columns]
        for record in table:
            for i, cell in enumerate(record):
                widths[i] = max(widths[i], _display_width(cell))
        # 열의 값이 (결측 제외) 전부 숫자면 우측 정렬해 자릿수를 맞춘다(가격·잔량·거래량 등),
        # 그 외(종목명 등 텍스트)는 좌측. 헤더도 열 정렬을 따른다.
        right = [
            any(_is_numeric(record[i]) for record in raw)
            and all(record[i] is None or _is_numeric(record[i]) for record in raw)
            for i in range(len(columns))
        ]
        lines = []
        if not no_header:
            lines.append("  ".join(
                _align(col, widths[i], right=right[i]) for i, col in enumerate(columns)).rstrip())
        for record in table:
            lines.append("  ".join(
                _align(cell, widths[i], right=right[i]) for i, cell in enumerate(record)).rstrip())
        return "\n".join(lines)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _render_fields(
            [(col, getattr(value, col)) for col in _public_fields(value)], no_header=no_header)
    if isinstance(value, dict):
        return _render_fields(list(value.items()), no_header=no_header)
    return _cell(value)


def _render_fields(items: list[tuple[Any, Any]], *, no_header: bool) -> str:
    """단일 객체의 (이름, 값) 쌍들을 key/value 2열로. 값이 레코드 리스트면 JSON 으로 접지 않고
    이름을 머리로 두고 들여쓴 하위 표로 펼친다(중첩 표)."""
    scalar_keys = [str(key) for key, val in items if not _is_record_list(val)]
    width = max((_display_width(key) for key in scalar_keys), default=0)
    lines = []
    for key, val in items:
        if _is_record_list(val):
            lines.append(str(key))
            lines.append(_indent(_render_table(list(val), no_header=no_header)))
        else:
            lines.append(f"{_pad(str(key), width)}  {_cell(val)}")
    return "\n".join(lines)


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
