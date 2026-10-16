"""impl 함수에서 KIS 로 보내는 요청 파라미터 키 집합을 AST 로 정적 추출.

한계(단순 휴리스틱): 함수 본문 내 모든 dict 리터럴의 상수-문자열 키와 모든 상수-문자열
subscript 를 수집한다. request 생성만 하는 thin fetch 함수엔 정확하나, 한 함수가 request 생성과
response 파싱(``row["X"]``)을 겸하면 요청 외 키를 과수집할 수 있다(``required ⊆ sent`` 를 거짓
PASS 시킬 여지). 그런 impl 은 request 경로를 별도 함수로 분리하거나 live probe 로 검증하라.
dict unpacking(``{**base}``)이 있으면 base 키를 정적으로 알 수 없어 ``resolved=False`` 로 떨군다."""
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
                if k is None:  # dict unpacking {**base} -- base 키를 정적으로 모름
                    resolved = False
                elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
                else:  # 비상수 키(변수/표현식) = 정적 확정 불가
                    resolved = False
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str):
            keys.add(node.slice.value)
    return ParamScan(keys=frozenset(keys), resolved=resolved)
