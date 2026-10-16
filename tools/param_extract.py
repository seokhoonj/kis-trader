"""impl 함수에서 KIS 로 보내는 요청 파라미터 키 집합을 AST 로 정적 추출."""
from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ParamScan:
    keys: frozenset[str]
    resolved: bool


def _module_file(dotted: str) -> Path:
    spec = importlib.util.find_spec(dotted)
    if spec is None or spec.origin is None:
        raise LookupError(f"module not found: {dotted}")
    return Path(spec.origin)


def _func_node(tree: ast.Module, func: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return node
    raise LookupError(f"function not found: {func}")


def extract_sent_param_keys(impl: str) -> ParamScan:
    mod, func = impl.split(":")
    tree = ast.parse(_module_file(mod).read_text(encoding="utf-8"))
    fn = _func_node(tree, func)
    keys: set[str] = set()
    resolved = True
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
                elif k is not None:  # 비상수 키 = 정적 확정 불가
                    resolved = False
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str):
            keys.add(node.slice.value)
    return ParamScan(keys=frozenset(keys), resolved=resolved)
