"""Sphinx 교차참조 가드 -- 문서/코드의 ``:class:``/``:meth:`` 등 ``kis_trader.*`` 참조가 전부
실제로 해석되는지 importlib 로 검증한다. path-shape grep 으로는 못 잡는 결함(존재하지 않는
모듈 세그먼트, 유효 모듈 경로 위의 오타난 클래스명)을 recurrence-guard 로 막는다 -- 네이밍
정규화 2라운드에서 6건이 반복해 발생했다(일부는 격론에서야 발견).
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
# :role:`~kis_trader.a.b.C.d` / :role:`kis_trader.a.b` -- 역할·틸트·백틱을 벗기고 dotted 경로만.
_REF = re.compile(r":(?:class|meth|func|attr|mod|obj|data|exc):`~?(kis_trader(?:\.[A-Za-z_][A-Za-z0-9_]*)+)`")


def _iter_sources() -> list[Path]:
    files = list((_ROOT / "src").rglob("*.py"))
    files += list((_ROOT / "docs").glob("*.md"))
    files += list((_ROOT / "docs").glob("*.qmd"))
    readme = _ROOT / "README.md"
    if readme.exists():
        files.append(readme)
    return files


def _resolve(ref: str) -> bool:
    """``kis_trader.a.b.C.d`` 가 해석되나 -- 가장 긴 import 가능한 모듈 접두어를 찾고 나머지는
    getattr 로 따라간다. 하나라도 끊기면 False."""
    parts = ref.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ModuleNotFoundError:
            continue
        except ImportError:
            # 모듈은 존재하나 선택 의존성이 없어 import 가 실패(예: mcp 미설치) -- dangling 아님.
            return True
        for attr in parts[cut:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def _all_refs() -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    for path in _iter_sources():
        text = path.read_text(encoding="utf-8")
        for m in _REF.finditer(text):
            seen.append((str(path.relative_to(_ROOT)), m.group(1)))
    return seen


_REFS = _all_refs()


def test_crossref_corpus_nonempty():
    # 가드 자체가 비면(정규식 깨짐 등) 조용히 통과하지 않도록 최소 코퍼스를 확인.
    assert len(_REFS) > 50


@pytest.mark.parametrize(("where", "ref"), _REFS, ids=[f"{w}:{r}" for w, r in _REFS])
def test_crossref_resolves(where: str, ref: str):
    assert _resolve(ref), f"{where}: 해석되지 않는 교차참조 {ref!r}"
