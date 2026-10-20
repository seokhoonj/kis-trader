"""Offline source contracts. Never executes documentation Python or shell commands."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from markdown_it import MarkdownIt


@dataclass(frozen=True)
class Span:
    identifier: str
    file: str
    line: int
    end_line: int
    kind: str
    language: str
    text: str
    sha256: str


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def extract_documents(root: Path, manifest: list[str]) -> tuple[list[Span], list[str]]:
    spans = []
    errors = []
    parser = MarkdownIt("commonmark").enable("table")
    for file in manifest:
        path = root / file
        if not path.is_file():
            errors.append(file + ": missing tracked source")
            continue
        raw = path.read_bytes()
        if path.suffix == ".png":
            spans.append(
                Span(file + ":asset", file, 1, 1, "asset", "", "", hashlib.sha256(raw).hexdigest())
            )
            continue
        text = raw.decode()
        lines = text.splitlines()
        counts: dict[str, int] = {}
        last_map = [0, len(lines)]

        def add(
            kind: str, language: str, content: str, location: list[int],
            file: str = file, counts: dict[str, int] = counts,
        ) -> None:
            counts[kind] = counts.get(kind, 0) + 1
            spans.append(
                Span(
                    f"{file}:{kind}:{counts[kind]}",
                    file,
                    location[0] + 1,
                    location[1],
                    kind,
                    language,
                    content,
                    digest(content),
                )
            )

        if path.suffix != ".md":
            add("support", "", text, [0, len(lines)])
            continue
        for token in parser.parse(text):
            if token.map is not None:
                last_map = token.map
            location = token.map or last_map
            if token.type == "fence":
                add(
                    "fence",
                    token.info.strip().split()[0] if token.info.strip() else "",
                    token.content,
                    location,
                )
                closing = lines[location[1] - 1] if location[1] else ""
                if not re.match(
                    r"^[ >\t]*"
                    + re.escape(token.markup[0])
                    + r"{"
                    + str(len(token.markup))
                    + r",}\s*$",
                    closing,
                ):
                    errors.append(f"{file}:{location[0] + 1}: unclosed fence")
            elif token.type == "inline":
                # Parent block maps source lines; inline children carry content but no own map.
                add("prose", "", token.content, location)
                for child in token.children or []:
                    if child.type == "code_inline":
                        add("inline", "", child.content, location)
                    if child.type in ("link_open", "image"):
                        target = (
                            child.attrGet("href")
                            if child.type == "link_open"
                            else child.attrGet("src")
                        )
                        if isinstance(target, str):
                            add("link", "", target, location)
        # Physical lines remain explicitly covered even when markup has no semantic token.
        for number, line in enumerate(lines):
            add("physical_line", "", line, [number, number + 1])
    return spans, errors


def commands(span: Span) -> list[str]:
    if span.kind == "inline":
        return [span.text] if span.text.strip().startswith("kis ") else []
    if span.kind == "fence" and span.language in ("sh", "bash", "shell"):
        return [
            line.strip()
            for line in span.text.replace("\\\n", " ").splitlines()
            if line.strip().startswith("kis ")
        ]
    return []




_PUBLIC_MODULES = frozenset({
    "kis_trader", "kis_trader.config", "kis_trader.execution", "kis_trader.realtime",
    "kis_trader.instrument", "kis_trader.errors",
})


def _statements(nodes: list[ast.stmt]) -> Iterator[ast.stmt]:
    """분기/컨텍스트 안의 문장도 소스 순서로 분석한다. 실행·제어 흐름 증명은 하지 않는다."""
    for node in nodes:
        yield node
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(node, field, None)
            if isinstance(nested, list):
                yield from _statements(nested)
        if isinstance(node, (ast.Try, ast.TryStar)):
            for handler in node.handlers:
                yield from _statements(handler.body)


def _expressions(node: ast.AST) -> Iterator[ast.AST]:
    """하위 문장은 자기 문맥에서 따로 처리해 미할당 이름을 먼저 해석하지 않는다."""
    yield node
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, ast.stmt):
            yield from _expressions(child)


def check_api_bindings(root: Path, spans: list[Span], contexts: dict[str, str] | None = None,
    support_members: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    import ast
    import contextlib
    import dataclasses
    import importlib
    import inspect
    import os
    import tempfile
    import textwrap
    import types
    import typing
    from unittest.mock import patch

    from kis_trader import KISClient, OrderStore
    from kis_trader.instrument import DomesticListingIndex, InstrumentRecord, MasterIndex
    from kis_trader.realtime.client import RealtimeClient

    checks: list[dict[str, Any]] = []

    class DenyTransport:
        def request(
            self, *, method: str, path: str, tr_id: str, params: object = None,
            body: object = None, idempotent: bool, tr_cont: str = "",
        ) -> NoReturn:
            raise AssertionError("API binding must never execute transport")

    def deny(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("credential/auth/master fallback forbidden")

    # Explicit synthetic fixtures are isolated before constructors; no home fallback.
    with (
        tempfile.TemporaryDirectory(prefix="docs-api-") as temporary,
        contextlib.ExitStack() as stack,
    ):
        process_env = {k: v for k, v in os.environ.items() if not k.startswith("KIS_")}
        process_env.update(
            {
                k: str(Path(temporary) / k)
                for k in (
                    "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_STATE_HOME",
                    "APPDATA",
                    "LOCALAPPDATA",
                )
            }
        )
        stack.enter_context(patch.dict(os.environ, process_env, clear=True))
        for name in (
            "kis_trader.client._fill_credentials",
            "kis_trader.client.load_overseas_index",
            "kis_trader.client.load_domestic_index",
            "kis_trader._internal._auth.TokenManager.access_token",
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.create_connection",
        ):
            stack.enter_context(patch(name, deny))
        fixtures = [
            InstrumentRecord(s, e, c, "stock", "Synthetic", "Synthetic", e + s)
            for s, e, c in [("AAPL", "NAS", "USD"), ("00700", "HKS", "HKD")]
        ]
        # Account is a synthetic repeated digit fixture, never a local credential lookup.
        account_number = "7" * 8
        clients: dict[str, Any] = {}
        for product in ("01", "03", "08", "29"):
            store = OrderStore()
            store.bind_scope(cano=account_number, product_code=product, environment="real")
            client = KISClient(
                app_key="synthetic",
                app_secret="synthetic",
                account=account_number + "-" + product,
                environment="real",
                transport=DenyTransport(),
                store=store,
                master_index=MasterIndex(fixtures),
                domestic_index=DomesticListingIndex([]),
                config_dir=temporary,
            )
            clients[product] = client
            stack.callback(client.close)
            stack.callback(store.close)
        rt = RealtimeClient(
            "synthetic",
            "wss://invalid",
            instrument_resolver=clients["01"].overseas.resolve_instrument,
            connect=None,
        )
        typed_subscription = rt.domestic.stock("005930").trades()
        stack.callback(typed_subscription.close)
        stack.callback(rt.stop)
        blocks: list[dict[str, Any]] = []
        for span in spans:
            if span.kind != "fence" or span.language != "python":
                continue
            blocks.append(
                {"file": span.file, "line": span.line, "text": span.text, "identifier": span.identifier}
            )
        UNKNOWN = object()

        @dataclasses.dataclass
        class TypeValue:
            annotation: Any

        @dataclasses.dataclass
        class BoundMember:
            fn: Any
            owner: Any

        def hints(fn: Any) -> dict[str, Any]:
            names = {**vars(importlib.import_module("kis_trader")),
                     **vars(importlib.import_module("kis_trader.realtime"))}
            try:
                return typing.get_type_hints(fn, localns=names)
            except (AttributeError, NameError, TypeError, ValueError, KeyError, IndexError):
                return {}

        def annotation(fn: Any) -> Any:
            return hints(fn).get("return", UNKNOWN)

        def concrete(a: Any) -> Any:
            if isinstance(a, TypeValue):
                a = a.annotation
            origin = typing.get_origin(a)
            if origin in (typing.Union, types.UnionType):
                values = [v for v in typing.get_args(a) if v is not type(None)]
                return concrete(values[0]) if len(values) == 1 else UNKNOWN
            return origin or a

        def resolve(node: ast.AST | None, env: dict[str, Any]) -> Any:
            if isinstance(node, ast.Name):
                return env.get(node.id, UNKNOWN)
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.NamedExpr):
                assigned_value = resolve(node.value, env)
                if isinstance(node.target, ast.Name):
                    env[node.target.id] = assigned_value
                return assigned_value
            if isinstance(node, (ast.List, ast.Tuple)):
                values = [resolve(element, env) for element in node.elts]
                return tuple(values) if isinstance(node, ast.Tuple) else values
            if isinstance(node, ast.Dict):
                if any(key is None for key in node.keys):
                    return UNKNOWN
                try:
                    return {ast.literal_eval(key): resolve(value, env)
                            for key, value in zip(node.keys, node.values, strict=True) if key is not None}
                except (TypeError, ValueError):
                    return UNKNOWN
            if isinstance(node, ast.Attribute):
                owner = resolve(node.value, env)
                if owner is UNKNOWN:
                    return UNKNOWN
                if isinstance(owner, TypeValue):
                    cls = concrete(owner)
                    if cls is UNKNOWN:
                        return UNKNOWN
                    try:
                        hints = typing.get_type_hints(cls)
                    except (AttributeError, NameError, TypeError, ValueError, KeyError, IndexError):
                        hints = {}
                    if node.attr in hints:
                        return TypeValue(hints[node.attr])
                    try:
                        obj = inspect.getattr_static(cls, node.attr)
                    except (AttributeError, NameError, TypeError, ValueError, KeyError, IndexError):
                        return UNKNOWN
                    if isinstance(obj, property):
                        return TypeValue(annotation(obj.fget))
                    if isinstance(obj, staticmethod):
                        return obj.__func__
                    if isinstance(obj, classmethod):
                        return getattr(cls, node.attr)
                    return BoundMember(obj, owner) if callable(obj) else obj
                try:
                    return getattr(owner, node.attr)
                except (AttributeError, NameError, TypeError, ValueError, KeyError, IndexError):
                    return UNKNOWN
            if isinstance(node, ast.Subscript):
                value = resolve(node.value, env)
                if isinstance(node.slice, ast.Slice):
                    return value
                if isinstance(value, TypeValue):
                    type_arguments = typing.get_args(value.annotation)
                    if type_arguments:
                        return TypeValue(type_arguments[0])
                try:
                    return value[ast.literal_eval(node.slice)]
                except (AttributeError, NameError, TypeError, ValueError, KeyError, IndexError):
                    return UNKNOWN
            if isinstance(node, ast.Call):
                fn = resolve(node.func, env)
                if fn is UNKNOWN:
                    return UNKNOWN
                actual = fn.fn if isinstance(fn, BoundMember) else fn
                # Safe handle factory allowlist only; queries, save, lifecycle and auth NEVER executed.
                owner = getattr(actual, "__self__", None)
                name = getattr(actual, "__name__", "")
                safe = owner in (
                    *tuple(ns for c in clients.values() for ns in (c.domestic, c.overseas)),
                    rt.domestic,
                    rt.overseas,
                ) and name in ("stock", "index", "futures", "option", "bond", "elw")
                safe = safe or (
                    type(owner).__module__.startswith("kis_trader.realtime")
                    and name in ("trades", "order_book")
                )
                if safe and name in ("trades", "order_book"):
                    return TypeValue(annotation(actual))
                if safe:
                    try:
                        args = [ast.literal_eval(a) for a in node.args]
                        if any(k.arg is None for k in node.keywords):
                            return UNKNOWN
                        kwargs = {str(k.arg): ast.literal_eval(k.value) for k in node.keywords}
                        return actual(*args, **kwargs)
                    except (AttributeError, NameError, TypeError, ValueError, KeyError, IndexError):
                        return TypeValue(annotation(actual))
                if actual is KISClient:
                    return env["kis"]
                if (
                    isinstance(node.func, ast.Attribute)
                    and ast.unparse(node.func) == "kis.realtime"
                ):
                    return rt
                if inspect.isclass(actual):
                    return TypeValue(actual)
                return TypeValue(annotation(actual))
            return UNKNOWN

        def fixture_context(file: str, product: str) -> dict[str, Any]:
            kis = clients[product]
            env: dict[str, Any] = {}
            env.update(
                {
                    "kis": kis,
                    "stock": clients["01"].domestic.stock("005930"),
                    "account": kis.account,
                    "realtime": rt,
                    "KISClient": KISClient,
                    "sub": typed_subscription,
                    "bonds": clients["01"].account.domestic.bonds,
                    "calendar": clients["01"].domestic.calendar,
                    "market": clients["01"].domestic.market,
                    "ranking": clients["01"].domestic.ranking,
                    "index": clients["01"].domestic.index("KOSPI"),
                    "futures": clients["03"].domestic.futures("101W09"),
                    "bond": clients["01"].domestic.bond("KR6095572D97"),
                }
            )
            if file.endswith("overseas.md") and product == "01":
                env.update(stock=clients["01"].overseas.stock("AAPL"), account=kis.account.overseas)
            elif (file.endswith(("account.md", "orders.md"))) and product == "01":
                env["account"] = kis.account.domestic
            return env

        file_envs: dict[str, dict[str, Any]] = {}
        for block in blocks:
            file = block["file"]
            source = textwrap.dedent(block["text"])
            try:
                tree = ast.parse(source)
            except SyntaxError as e:
                checks.append(
                    {
                        "file": file,
                        "line": block["line"] + e.lineno,
                        "category": "PYTHON_SYNTAX",
                        "status": "SYNTAX_FAIL",
                        "reason": e.msg,
                    }
                )
                continue
            checks.append(
                {
                    "file": file,
                    "line": block["line"],
                    "category": "PYTHON_SYNTAX",
                    "status": "PARSE_PASS",
                    "reason": "AST parsed; no execution",
                }
            )
            product = (contexts or {}).get(block["identifier"], "01")
            if product not in clients:
                raise ValueError("Unknown declared account context")
            kis = clients[product]
            env: dict[str, Any] = dict(file_envs.get(file, {}))
            env.update(fixture_context(file, product))
            callback_contexts: dict[str, Any] = {}
            for callback_call in (item for item in ast.walk(tree) if isinstance(item, ast.Call)):
                callback_name = next((keyword.value.id for keyword in callback_call.keywords
                                      if keyword.arg == "on" and isinstance(keyword.value, ast.Name)), None)
                if callback_name is None:
                    continue
                callback_function = resolve(callback_call.func, env)
                callback_actual = callback_function.fn if isinstance(callback_function, BoundMember) else callback_function
                try:
                    callback_type = hints(callback_actual).get("on")
                except (TypeError, NameError, AttributeError):
                    continue
                if typing.get_origin(callback_type) in (typing.Union, types.UnionType):
                    choices = [choice for choice in typing.get_args(callback_type) if choice is not type(None)]
                    callback_type = choices[0] if len(choices) == 1 else None
                callback_arguments = typing.get_args(callback_type)
                if callback_arguments and isinstance(callback_arguments[0], list) and len(callback_arguments[0]) == 1:
                    callback_contexts[callback_name] = callback_arguments[0][0]
            for node in _statements(tree.body):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in callback_contexts and node.args.args:
                    env[node.args.args[0].arg] = TypeValue(callback_contexts[node.name])
                if (isinstance(node, ast.If) and isinstance(node.test, ast.Call)
                        and isinstance(node.test.func, ast.Name) and node.test.func.id == "isinstance"
                        and len(node.test.args) == 2 and isinstance(node.test.args[0], ast.Name)):
                    narrowed = resolve(node.test.args[1], env)
                    if isinstance(narrowed, type):
                        env[node.test.args[0].id] = TypeValue(narrowed)
                if (isinstance(node, ast.ImportFrom) and node.module
                        and node.module.startswith("kis_trader")
                        and node.module not in _PUBLIC_MODULES):
                    checks.append({"file": file, "line": block["line"] + node.lineno,
                                   "category": "API_IMPORT", "status": "BIND_FAIL",
                                   "reason": "Undeclared first-party import module", "text": node.module})
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and (
                        node.module in _PUBLIC_MODULES
                        or node.module in ("decimal", "datetime", "uuid")
                    )
                ):
                    module = importlib.import_module(node.module)
                    for alias in node.names:
                        try:
                            env[alias.asname or alias.name] = getattr(module, alias.name)
                        except AttributeError:
                            checks.append(
                                {
                                    "file": file,
                                    "line": block["line"] + node.lineno,
                                    "category": "API_IMPORT",
                                    "status": "BIND_FAIL",
                                    "text": alias.name,
                                    "reason": "Not exported by " + node.module,
                                }
                            )
                if isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name):
                    iterable = resolve(node.iter, env)
                    if isinstance(iterable, TypeValue):
                        item_types = typing.get_args(iterable.annotation)
                        if item_types:
                            env[node.target.id] = TypeValue(item_types[0])
                if isinstance(node, (ast.With, ast.AsyncWith)):
                    for entry in node.items:
                        if isinstance(entry.optional_vars, ast.Name):
                            env[entry.optional_vars.id] = resolve(entry.context_expr, env)
                for attribute in (n for n in _expressions(node) if isinstance(n, ast.Attribute)):
                    owner_value = resolve(attribute.value, env)
                    member_value = resolve(attribute, env)
                    expression_text = ast.unparse(attribute)
                    key = block["identifier"] + ":" + expression_text
                    support_reason = (support_members or {}).get(key)
                    if member_value is UNKNOWN:
                        checks.append({
                            "file": file, "line": block["line"] + attribute.lineno,
                            "category": "RESULT_MEMBER", "text": expression_text,
                            "status": "OPEN_SUPPORT_ATTRIBUTE" if support_reason else "MEMBER_FAIL",
                            "reason": support_reason or "Unresolved attribute owner/member; explicit context required",
                        })
                    elif isinstance(owner_value, TypeValue):
                        owner_type = concrete(owner_value)
                        if str(getattr(owner_type, "__module__", "")).startswith("kis_trader"):
                            checks.append({
                                "file": file, "line": block["line"] + attribute.lineno,
                                "category": "RESULT_MEMBER", "text": expression_text,
                                "status": "MEMBER_PASS",
                                "reason": "Actual public return annotation member; value/unit semantics remain separate",
                            })
                # Walk outer expressions before assignments are made; inner calls evaluated structurally.
                for call in sorted(
                    [n for n in _expressions(node) if isinstance(n, ast.Call)],
                    key=lambda n: (n.lineno, n.col_offset),
                ):
                    expr = ast.unparse(call.func)
                    fn = resolve(call.func, env)
                    if (
                        fn is UNKNOWN
                        and isinstance(call.func, ast.Name)
                        and call.func.id in ("print", "isinstance", "help", "input", "str")
                    ):
                        checks.append(
                            {
                                "file": file,
                                "line": block["line"] + call.lineno,
                                "category": "SUPPORT_CALL",
                                "status": "OPEN_RESULT_VALUES",
                                "text": ast.unparse(call),
                                "reason": "Known builtin; formatted result fields/value semantics require synthetic execution, not a first-party signature failure",
                            }
                        )
                        continue
                    if fn is UNKNOWN:
                        checks.append(
                            {
                                "file": file,
                                "line": block["line"] + call.lineno,
                                "category": "API_CALL",
                                "status": "OPEN_CONTEXT",
                                "text": ast.unparse(call),
                                "reason": "Context/owner not resolved; not executed or silently accepted",
                            }
                        )
                        continue
                    actual = fn.fn if isinstance(fn, BoundMember) else fn
                    if not str(getattr(actual, "__module__", "")).startswith("kis_trader"):
                        checks.append(
                            {
                                "file": file,
                                "line": block["line"] + call.lineno,
                                "category": "SUPPORT_CALL",
                                "status": "OPEN_SUPPORT",
                                "text": ast.unparse(call),
                                "reason": "Non-first-party/local callback requires separate value/behavior evidence",
                            }
                        )
                        continue
                    try:
                        sig = inspect.signature(actual)
                        if isinstance(fn, BoundMember) and list(sig.parameters)[:1] == ["self"]:
                            sig = sig.replace(parameters=list(sig.parameters.values())[1:])
                        if any(isinstance(a, ast.Starred) for a in call.args) or any(
                            k.arg is None for k in call.keywords
                        ):
                            raise ValueError("Expanded arguments need scenario context")
                        sig.bind(
                            *[UNKNOWN for a in call.args], **{str(k.arg): UNKNOWN for k in call.keywords}
                        )
                        status, reason = (
                            "BIND_PASS",
                            "Exact real public signature binds; runtime/type/value semantics not checked",
                        )
                    except TypeError as e:
                        status, reason = "BIND_FAIL", str(e)
                    except (ValueError, AttributeError) as e:
                        status, reason = "OPEN_SIGNATURE", str(e)
                    checks.append(
                        {
                            "file": file,
                            "line": block["line"] + call.lineno,
                            "category": "API_CALL",
                            "text": ast.unparse(call),
                            "signature": str(sig) if "sig" in locals() else "",
                            "owner": type(getattr(actual, "__self__", None)).__name__,
                            "status": status,
                            "reason": reason,
                        }
                    )
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    value = resolve(node.value, env)
                    for target in targets:
                        if isinstance(target, ast.Name):
                            env[target.id] = value
                if isinstance(node, ast.With):
                    for entry in node.items:
                        if isinstance(entry.optional_vars, ast.Name):
                            env[entry.optional_vars.id] = resolve(entry.context_expr, env)

            file_envs[file] = env

        for span in spans:
            if span.kind != "inline" or not span.text.startswith(("kis.", "stock.", "account.", "realtime.", "sub.", "futures.", "bond.")):
                continue
            reference_product = (contexts or {}).get(span.identifier)
            if reference_product is None or reference_product not in clients:
                checks.append({"file": span.file, "line": span.line, "category": "INLINE_REFERENCE", "status": "MEMBER_FAIL", "text": span.text, "reason": "Reference lacks explicit account context"})
                continue
            env = dict(file_envs.get(span.file, {}))
            env.update(fixture_context(span.file, reference_product))
            try:
                expression = ast.parse(span.text.removesuffix(".*"), mode="eval").body
                reference_target = resolve(expression.func if isinstance(expression, ast.Call) else expression, env)
            except SyntaxError:
                reference_target = UNKNOWN
            checks.append({
                "file": span.file, "line": span.line, "category": "INLINE_REFERENCE", "text": span.text,
                "status": "MEMBER_FAIL" if reference_target is UNKNOWN else "MEMBER_PASS",
                "reason": "Actual reference owner/member only; schematic arguments/value semantics are not certified",
            })

        corpus_root = root
        kis = clients["01"]
        roots = {
            "kis": kis,
            "kis.domestic": kis.domestic,
            "kis.overseas": kis.overseas,
            "kis.account": kis.account,
            "kis.orders": kis.orders,
            "kis.realtime()": rt,
        }
        taxonomy_blocks: list[dict[str, Any]] = [
            {"file": span.file, "line": span.line, "text": span.text, "category": "code_block"}
            for span in spans
            if span.file == "docs/taxonomy.md" and span.kind == "fence"
        ]
        factory_args = {
            "stock": ("005930",),
            "index": ("KOSPI",),
            "bond": ("KR6095572D97",),
            "elw": ("58J297",),
            "futures": ("101W09",),
            "option": ("201W09350",),
            "bond_index": ("BOND",),
        }
        for b in taxonomy_blocks:
            if b["file"] != "docs/taxonomy.md" or b["category"] != "code_block":
                continue
            lines = b["text"].splitlines()
            tree_root = next(
                (
                    roots[x.strip().split("  ")[0]]
                    for x in lines
                    if x.strip().split("  ")[0] in roots
                ),
                None,
            )
            if tree_root is None:
                continue
            owners: dict[int, Any] = {-1: tree_root}
            paths = {-1: lines[0]}
            for off, line in enumerate(lines, 1):
                match = re.search(r"[├└]──\s+(.*)", line)
                if not match:
                    continue
                depth = match.start() // 4
                entry_text = match.group(1).split("#", 1)[0].strip()
                member_match = re.match(r"([\w]+)", entry_text)
                if entry_text.startswith("["):
                    p = (
                        "03"
                        if "DomesticDerivativesAccount" in entry_text
                        else "08"
                        if "OverseasDerivativesAccount" in entry_text
                        else "01"
                    )
                    owners[depth] = clients[p].account
                    paths[depth] = entry_text
                    continue
                if not member_match:
                    continue
                name = member_match.group(1)
                parent = owners.get(depth - 1)
                path = paths.get(depth - 1, "?") + "." + name
                result = {
                    "file": b["file"],
                    "line": b["line"] + off,
                    "category": "TAXONOMY_MEMBER",
                    "entry": entry_text,
                    "path": path,
                }
                if parent is None:
                    result.update(
                        status="OPEN_CONTEXT",
                        reason="Parent owner not materialized; not silently accepted",
                    )
                    checks.append(result)
                    continue
                try:
                    # Access only namespace/property/handle members; never invoke query/lifecycle methods.
                    member = (
                        clients["29"].account.pension
                        if name == "pension" and type(parent).__name__ == "StockAccount"
                        else getattr(parent, name)
                    )
                except (
                    AttributeError,
                    NameError,
                    TypeError,
                    ValueError,
                    KeyError,
                    IndexError,
                ) as e:
                    result.update(status="MEMBER_FAIL", reason=type(e).__name__)
                    checks.append(result)
                    owners[depth] = None
                    paths[depth] = path
                    continue
                result.update(status="MEMBER_PASS", owner=type(parent).__name__)
                if callable(member):
                    try:
                        result["actual_signature"] = str(inspect.signature(member))
                    except (TypeError, ValueError):
                        result["actual_signature"] = "OPEN"
                    if name in factory_args:
                        try:
                            args = factory_args[name]
                            kw = {}
                            if parent in (kis.overseas, rt.overseas) and name == "stock":
                                args = ("AAPL",)
                                kw = {"exchange": "NAS"}
                            elif parent is kis.overseas and name == "index":
                                args = ("SPX",)
                            elif parent is kis.overseas and name in ("futures", "option"):
                                args = ("ESZ25",)
                            owners[depth] = member(*args, **kw)
                        except (
                            AttributeError,
                            NameError,
                            TypeError,
                            ValueError,
                            KeyError,
                            IndexError,
                        ):
                            owners[depth] = None
                    else:
                        owners[depth] = None
                else:
                    owners[depth] = member
                paths[depth] = path
                checks.append(result)

        root = corpus_root
        import kis_trader

        for meta in [{"file": file} for file in sorted({span.file for span in spans})]:
            if not meta["file"].endswith(".md"):
                continue
            lines = (root / meta["file"]).read_text().splitlines()
            owner = None
            table = False
            for i, line in enumerate(lines):
                if line.startswith("| 필드"):
                    table = True
                    prefix = "\n".join(lines[max(0, i - 22) : i])
                    names = re.findall(r"`([A-Z][A-Za-z0-9]+)`", prefix)
                    owner = None
                    for n in reversed(names):
                        value = getattr(kis_trader, n, None)
                        if isinstance(value, type) and dataclasses.is_dataclass(value):
                            owner = value
                            break
                    if owner is None:
                        checks.append({"file": meta["file"], "line": i + 1,
                                       "category": "TABLE_PUBLIC_FIELD", "status": "MEMBER_FAIL",
                                       "reason": "Field table lacks an actual exported dataclass owner"})
                    continue
                if not line.startswith("|"):
                    table = False
                    continue
                if not table or not owner:
                    continue
                try:
                    members = typing.get_type_hints(owner)
                except (AttributeError, NameError, TypeError, ValueError, KeyError, IndexError):
                    members = {f.name: object for f in dataclasses.fields(owner)}
                for field in re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", line.split("|")[1]):
                    exists = field in members or hasattr(owner, field)
                    checks.append(
                        {
                            "file": meta["file"],
                            "line": i + 1,
                            "category": "TABLE_PUBLIC_FIELD",
                            "owner": owner.__name__,
                            "field": field,
                            "status": "MEMBER_PASS" if exists else "MEMBER_FAIL",
                            "evidence": "Actual exported dataclass type hints/attributes; field meaning/unit not thereby proved",
                        }
                    )
    return checks



def tracked_docs(root: Path) -> list[str]:
    """현재 추적 문서만 읽는다. git 없는 복사본은 호출자가 명시적 목록을 제공해야 한다."""
    import subprocess

    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "docs/"], cwd=root,
        check=True, capture_output=True, text=True,
    )
    return sorted(name for name in result.stdout.split("\0") if name)


def check_documents(root: Path, manifest: list[str], registry: dict[str, Any]) -> dict[str, Any]:
    """기계적 계약 위반과 별도 의미/렌더 검토 잔여를 구분한다. 문서는 실행하지 않는다."""
    import contextlib
    import io

    from kis_trader.cli.app import build_parser

    invalid_paths = [file for file in manifest if (
        not file.startswith("docs/") or ".." in Path(file).parts
        or any(part in {"_site", ".quarto"} for part in Path(file).parts)
    )]
    if invalid_paths:
        return {"counts": {}, "errors": ["generated or invalid documentation corpus path"], "residuals": [], "api_evidence": []}
    spans, errors = extract_documents(root, manifest)
    counts = {"files": len(manifest), "spans": len(spans), "python_syntax": 0,
              "json_syntax": 0, "cli_parse": 0}
    residuals: list[str] = []
    files = registry.get("files", {})
    if registry.get("schema") != 1 or not isinstance(files, dict):
        errors.append("invalid documentation registry schema")
        return {"counts": counts, "errors": errors, "residuals": residuals, "api_evidence": []}
    if len(manifest) != len(set(manifest)) or set(files) != set(manifest):
        errors.append("tracked documentation coverage changed")
    for file, expected in files.items():
        path = root / file
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(file + ": source hash changed; recheck contracts and workflows")
    package_file = importlib.import_module("kis_trader").__file__
    assert package_file is not None
    package_root = Path(package_file).parent
    for source, expected in registry.get("runtime", {}).items():
        path = package_root / source
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(source + ": runtime proof changed; rerun source-bound workflows")
    if registry.get("checker") and hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != registry["checker"]:
        errors.append("documentation checker changed; rerun bindings and workflows")
    contexts = registry.get("contexts", {})
    classifications = registry.get("fences", {})
    cli_cases = registry.get("cli_cases", {})
    for span in spans:
        if span.kind == "fence":
            if span.language == "python":
                if span.identifier not in contexts:
                    errors.append(f"{span.file}:{span.line}: Python fence lacks declared account context")
                try:
                    ast.parse(span.text)
                    counts["python_syntax"] += 1
                except SyntaxError as error:
                    errors.append(f"{span.file}:{span.line}: invalid Python: {error.msg}")
            elif span.language == "json":
                try:
                    json.loads(span.text)
                    counts["json_syntax"] += 1
                    residuals.append(f"{span.file}:{span.line}: JSON syntax only; loader/provider schema and value semantics require separate scenarios")
                except ValueError:
                    errors.append(f"{span.file}:{span.line}: invalid JSON")
            elif span.language not in ("sh", "bash", "shell"):
                if span.identifier not in classifications:
                    errors.append(f"{span.file}:{span.line}: unclassified fence language {span.language!r}")
                else:
                    residuals.append(f"{span.file}:{span.line}: {classifications[span.identifier]}")
        for command in commands(span):
            try:
                tokens = shlex.split(command, comments=True)
                case = cli_cases.get(digest(json.dumps(tokens)))
                source_tokens = tokens
                if case is not None:
                    tokens = case
                if any(symbol in token for token in source_tokens for symbol in ("<", ">", "…", "...", "|", ";", "&", "$", "[", "]", "{", "}")) or source_tokens == ["kis", "config", "set"]:
                    if case is None:
                        raise ValueError("command template lacks explicit scenario")
                    tokens = case
                if not isinstance(tokens, list) or not tokens or tokens[0] != "kis":
                    raise ValueError("invalid declared CLI scenario")
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    try:
                        build_parser().parse_args(tokens[1:])
                    except SystemExit as error:
                        if error.code:
                            raise ValueError("argparse rejected example") from None
                counts["cli_parse"] += 1
            except ValueError as error:
                errors.append(f"{span.file}:{span.line}: CLI {error}")
    records = check_api_bindings(root, spans, contexts, registry.get("support_members", {}))
    for record in records:
        status = record["status"]
        location = f"{record['file']}:{record['line']}"
        if status.endswith("FAIL") or status in ("OPEN_CONTEXT", "OPEN_SIGNATURE"):
            errors.append(f"{location}: {status}: {record.get('text', record.get('entry', ''))}: {record.get('reason', '')}")
        elif status.startswith("OPEN"):
            residuals.append(f"{location}: {status}: {record.get('reason', '')}")
    counts.update({
        "api_bind": sum(r["category"] == "API_CALL" and r["status"] == "BIND_PASS" for r in records),
        "taxonomy_members": sum(r["category"] == "TAXONOMY_MEMBER" and r["status"] == "MEMBER_PASS" for r in records),
        "table_fields": sum(r["category"] == "TABLE_PUBLIC_FIELD" and r["status"] == "MEMBER_PASS" for r in records),
        "inline_references": sum(r["category"] == "INLINE_REFERENCE" and r["status"] == "MEMBER_PASS" for r in records),
        "result_members": sum(r["category"] == "RESULT_MEMBER" and r["status"] == "MEMBER_PASS" for r in records),
        "prose_spans_requiring_semantic_review": sum(s.kind == "prose" for s in spans),
    })
    return {"counts": counts, "errors": errors, "residuals": residuals, "api_evidence": records}


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline mechanical documentation contracts; not semantic/render certification")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--registry", type=Path, default=Path("tests/docs_contracts.json"))
    parser.add_argument("--manifest", type=Path, help="Explicit no-git copied-corpus file list")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text()) if args.manifest else tracked_docs(args.root)
    result = check_documents(args.root, manifest, json.loads(args.registry.read_text()))
    print(json.dumps({key: value for key, value in result.items() if key != "api_evidence"}, ensure_ascii=False, indent=2))
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
