"""impl 함수에서 KIS 로 보내는 요청 파라미터 키 집합을 AST 로 정적 추출.

한계(단순 휴리스틱): 함수 본문 내 모든 dict 리터럴의 상수-문자열 키와 모든 상수-문자열
subscript 를 수집한다. request 생성만 하는 thin fetch 함수엔 정확하나, 한 함수가 request 생성과
response 파싱(``row["X"]``)을 겸하면 요청 외 키를 과수집할 수 있다(``required ⊆ sent`` 를 거짓
PASS 시킬 여지). 그런 impl 은 request 경로를 별도 함수로 분리하거나 live probe 로 검증하라.
dict unpacking(``{**base}``)이 있으면 base 키를 정적으로 알 수 없어 ``resolved=False`` 로 떨군다.

params 를 같은 모듈 헬퍼로 조립해 변수로만 넘기는 패턴(``params = _helper(...)`` 뒤
``request(params=params)``)은 그 헬퍼까지 따라가되, **헬퍼는 return 으로 돌려주는 dict 리터럴의 키만**
센다(:func:`_collect_builder_keys`). 헬퍼 본문의 로깅·룩업 등 무관한 dict 를 세면 보내지도 않는 키를
거짓 PASS 시키므로, 반환 매핑만 전송 키로 본다(반환이 변수/조건식이면 ``resolved=False``)."""
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


def _own_returns(fn: ast.FunctionDef) -> list[ast.Return]:
    """``fn`` 자신의 return 문만(중첩 함수/람다의 return 은 제외) 모은다 -- 헬퍼가 실제로 돌려주는
    매핑을 찾기 위함."""
    found: list[ast.Return] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # 중첩 함수의 return 은 이 헬퍼의 반환이 아니다
            if isinstance(child, ast.Return):
                found.append(child)
            walk(child)

    walk(fn)
    return found


def _collect_builder_keys(fn: ast.FunctionDef, keys: set[str]) -> bool:
    """params 빌더 헬퍼가 **return 으로 돌려주는 dict 리터럴**의 상수-문자열 키만 모은다. 헬퍼 본문의
    로깅·분기·룩업 등 무관한 dict 는 세지 않는다(그걸 전송 키로 오인하면 required 검사가 거짓 PASS).
    반환이 dict 리터럴이 아니거나(변수/조건식) 반환이 없으면 정적 추적 불가 -> ``False``(보수적 skip);
    unpack(``{**base}``)/비상수 키도 ``False``."""
    returns = _own_returns(fn)
    if not returns:
        return False
    resolved = True
    for ret in returns:
        if not isinstance(ret.value, ast.Dict):
            resolved = False  # 변수/조건식 반환은 정적 추적 불가
            continue
        for k in ret.value.keys:
            if k is None or not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                resolved = False
            else:
                keys.add(k.value)
    return resolved


def extract_sent_param_keys(impl: str) -> ParamScan:
    mod, func = impl.split(":")
    tree = ast.parse(_module_file(mod).read_text(encoding="utf-8"))
    fn = _func_node(tree, func)
    local_funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    # `params` 가 같은 모듈 헬퍼로 조립되는 패턴(``params = _helper(...)`` 뒤 ``request(params=params)``)을
    # 추적한다 -- thin fetch 가 params 를 변수로만 넘기면 전송 키가 impl 본문에 직접 안 보여, 그 헬퍼의
    # dict 리터럴 키를 따라가야 한다(예: 재무·가치 순위가 공유하는 _fundamentals_params).
    params_builder_by_var: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) and node.value.func.id in local_funcs:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    params_builder_by_var[tgt.id] = local_funcs[node.value.func.id]
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
        elif isinstance(node, ast.Call):  # request(params=<헬퍼로 조립된 변수>) 를 그 헬퍼로 해소
            for kw in node.keywords:
                if kw.arg == "params" and isinstance(kw.value, ast.Name) \
                        and kw.value.id in params_builder_by_var \
                        and not _collect_builder_keys(params_builder_by_var[kw.value.id], keys):
                    resolved = False
    return ParamScan(keys=frozenset(keys), resolved=resolved)
