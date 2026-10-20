"""오프라인 문서 계약 검사기의 실제 결함 감지 회귀 테스트."""

import hashlib
import json
import subprocess

import pytest
from tools.check_docs import check_documents, digest, extract_documents, tracked_docs


def corpus(tmp_path, text):
    path = tmp_path / "docs" / "example.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    manifest = ["docs/example.md"]
    spans, errors = extract_documents(tmp_path, manifest)
    assert not errors
    registry = {
        "schema": 1,
        "files": {manifest[0]: hashlib.sha256(path.read_bytes()).hexdigest()},
        "contexts": {span.identifier: "01" for span in spans if span.kind == "fence" and span.language == "python"},
        "cli_cases": {}, "fences": {},
    }
    return manifest, registry


def test_parser_handles_nested_fences_delimiters_tables_and_every_physical_line(tmp_path):
    text = "## Section\n\n> ~~~python\n> print('sample')\n> ~~~\n\n| example | value |\n| --- | --- |\n| `kis --help` | a\\|b |\n\n``literal ` code``\n"
    manifest, _ = corpus(tmp_path, text)
    spans, errors = extract_documents(tmp_path, manifest)
    assert not errors
    assert [(s.language, s.text) for s in spans if s.kind == "fence"] == [("python", "print('sample')\n")]
    assert any(s.kind == "inline" and s.text == "kis --help" and s.line == 9 for s in spans)
    assert any(s.kind == "inline" and s.text == "literal ` code" for s in spans)
    assert sum(s.kind == "physical_line" for s in spans) == len(text.splitlines())


def test_unclosed_fence_is_an_error(tmp_path):
    path = tmp_path / "example.md"
    path.write_text("```python\nx = 1\n")
    assert any("unclosed" in e for e in extract_documents(tmp_path, [path.name])[1])


@pytest.mark.parametrize("mutation", ["changed", "deleted"])
def test_source_cannot_inherit_stale_evidence(tmp_path, mutation):
    manifest, registry = corpus(tmp_path, "original meaning\n")
    path = tmp_path / manifest[0]
    path.write_text("changed meaning\n") if mutation == "changed" else path.unlink()
    assert check_documents(tmp_path, manifest, registry)["errors"]


def test_new_tracked_document_fails_current_manifest_join(tmp_path):
    _manifest, registry = corpus(tmp_path, "original meaning\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "docs/new.md").write_text("new meaning\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs"], check=True)
    assert tracked_docs(tmp_path) == ["docs/example.md", "docs/new.md"]
    assert any("coverage changed" in e for e in check_documents(tmp_path, tracked_docs(tmp_path), registry)["errors"])


@pytest.mark.parametrize("code", [
    'realtime.subscribe("TR", "symbol")',
    'kis.overseas.product_info("NAS", "AAPL")',
    'kis.overseas.nonexistent()',
    'unmapped_owner.method()',
])
def test_actual_signature_or_unknown_receiver_errors_even_with_fresh_registry(tmp_path, code):
    manifest, registry = corpus(tmp_path, "```python\n" + code + "\n```\n")
    assert check_documents(tmp_path, manifest, registry)["errors"]


def test_real_return_field_typo_is_checked_without_executing_query(tmp_path):
    manifest, registry = corpus(tmp_path, '```python\nquote = kis.domestic.stock("005930").quote()\nquote.nonexistent_price\n```\n')
    assert check_documents(tmp_path, manifest, registry)["errors"]


@pytest.mark.parametrize("text", [
    "| Example |\n| --- |\n| `kis stock quote SYMBOL --made-up` |\n",
    "`kis order cancel <request-id>`\n",
])
def test_invalid_or_unclassified_cli_is_an_error(tmp_path, text):
    manifest, registry = corpus(tmp_path, text)
    assert check_documents(tmp_path, manifest, registry)["errors"]


def test_exact_declared_template_binds_real_parser(tmp_path):
    import shlex
    manifest, registry = corpus(tmp_path, "`kis order cancel <request-id>`\n")
    registry["cli_cases"][digest(json.dumps(shlex.split("kis order cancel <request-id>")))] = ["kis", "order", "cancel", "synthetic-id"]
    assert not check_documents(tmp_path, manifest, registry)["errors"]


def test_unknown_fence_language_requires_explicit_classification(tmp_path):
    manifest, registry = corpus(tmp_path, "```unrecognized\ncontent\n```\n")
    assert check_documents(tmp_path, manifest, registry)["errors"]


def test_runtime_hash_staleness_is_an_error(tmp_path):
    manifest, registry = corpus(tmp_path, "original meaning\n")
    registry["runtime"] = {"client.py": "0" * 64}
    assert any("runtime proof changed" in e for e in check_documents(tmp_path, manifest, registry)["errors"])


def test_missing_firstparty_import_is_checked_even_when_unused(tmp_path):
    manifest, registry = corpus(tmp_path, '```python\nfrom kis_trader.nonexistent import Missing\n```\n')
    assert check_documents(tmp_path, manifest, registry)["errors"]


def test_inline_public_reference_typo_uses_real_member_lookup(tmp_path):
    manifest, registry = corpus(tmp_path, '`kis.overseas.nonexistent()`\n')
    spans, _ = extract_documents(tmp_path, manifest)
    registry["contexts"].update({s.identifier: "01" for s in spans if s.kind == "inline"})
    assert check_documents(tmp_path, manifest, registry)["errors"]


def test_generated_website_cannot_be_the_corpus(tmp_path):
    _manifest, registry = corpus(tmp_path, "text\n")
    registry["files"] = {"docs/_site/page.html": "0" * 64}
    assert check_documents(tmp_path, ["docs/_site/page.html"], registry)["errors"]


def test_current_tracked_docs_have_fresh_mechanical_contracts():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "tests/docs_contracts.json").read_text())
    assert set(registry) == {"schema", "files", "contexts", "cli_cases", "fences", "runtime", "checker", "verification_scope", "support_members"}
    result = check_documents(root, tracked_docs(root), registry)
    assert not result["errors"], result["errors"]
    assert result["counts"]["prose_spans_requiring_semantic_review"] > 0
    assert result["residuals"]


@pytest.mark.parametrize("owner,field", [("Quote", "nonexistent_price"), ("NonexistentQuote", "current_price")])
def test_field_tables_require_real_owner_and_member(tmp_path, owner, field):
    manifest, registry = corpus(tmp_path, f"`{owner}` fields:\n\n| 필드 | 뜻 |\n| --- | --- |\n| `{field}` | value |\n")
    assert check_documents(tmp_path, manifest, registry)["errors"]


def test_documentation_python_is_never_executed(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("network must not execute"))
    manifest, registry = corpus(tmp_path, '```python\nimport socket\nsocket.create_connection(("invalid", 1))\n```\n')
    assert check_documents(tmp_path, manifest, registry)["errors"]


@pytest.mark.parametrize("field,invalid", [("price", False), ("nonexistent", True)])
def test_conditional_firstparty_result_assignment_preserves_member_checks(tmp_path, field, invalid):
    text = f'''```python
order_book = stock.order_book()
if order_book.bids:
    best_bid = order_book.bids[0]
    print(best_bid.{field})
```
'''
    manifest, registry = corpus(tmp_path, text)
    errors = check_documents(tmp_path, manifest, registry)["errors"]
    assert bool(errors) is invalid, errors


@pytest.mark.parametrize("code", [
    "unmapped_owner.nonexistent",
    'data = {"quote": stock.quote()}\ndata["quote"].nonexistent_price',
    '(quote := stock.quote()).nonexistent_price',
])
def test_unresolved_or_nested_public_attribute_never_silently_passes(tmp_path, code):
    manifest, registry = corpus(tmp_path, "```python\n" + code + "\n```\n")
    assert check_documents(tmp_path, manifest, registry)["errors"]


def test_explicit_dynamic_member_classification_remains_a_residual(tmp_path):
    manifest, registry = corpus(tmp_path, "```python\npacket.data\n```\n")
    assert check_documents(tmp_path, manifest, registry)["errors"]
    registry["support_members"] = {"docs/example.md:fence:1:packet.data": "Dynamic payload requires a separate behavior scenario"}
    result = check_documents(tmp_path, manifest, registry)
    assert not result["errors"]
    assert any("separate behavior scenario" in item for item in result["residuals"])
