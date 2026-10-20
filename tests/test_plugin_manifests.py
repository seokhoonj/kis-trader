"""플러그인/MCP 매니페스트의 구조·버전 정합성 테스트.

버전은 여러 파일에 손으로 박혀 있어(pyproject + server.json x2 + 두 plugin.json +
.agents marketplace) 릴리스 때 한 곳만 올리고 나머지를 놓치기 쉽다. pyproject 를 유일한
근거(source of truth)로 두고 나머지가 전부 일치하는지 못박는다. 쌍둥이 plugin.json
(Claude/Codex)이 메타데이터에서 갈라지지 않는지, hooks.json 이 가리키는 가드 스크립트가
실제로 존재하는지도 함께 검증한다.

주의: installed 메타데이터(kis_trader.__version__ = importlib.metadata.version)는 editable
설치에서 낡은 값을 돌려줄 수 있어 근거로 쓰지 않는다 -- 파일에 적힌 pyproject 가 근거다.
"""

from __future__ import annotations

import json
import pathlib
import tomllib

from packaging.requirements import Requirement

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PLUGIN = _ROOT / "plugins" / "kis-trader"


def _load_json(relative_path: str) -> dict:
    return json.loads((_ROOT / relative_path).read_text(encoding="utf-8"))


def _pyproject_version() -> str:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_all_manifests_are_valid_json():
    for rel in (
        "server.json",
        ".claude-plugin/marketplace.json",
        ".agents/plugins/marketplace.json",
        "plugins/kis-trader/.claude-plugin/plugin.json",
        "plugins/kis-trader/.codex-plugin/plugin.json",
        "plugins/kis-trader/hooks/hooks.json",
    ):
        assert isinstance(_load_json(rel), dict), rel


def test_version_is_consistent_across_every_manifest():
    """릴리스 때 올려야 하는 모든 버전 필드가 pyproject 와 일치한다(드리프트 차단)."""
    expected_version = _pyproject_version()
    server = _load_json("server.json")
    claude = _load_json("plugins/kis-trader/.claude-plugin/plugin.json")
    codex = _load_json("plugins/kis-trader/.codex-plugin/plugin.json")
    agents = _load_json(".agents/plugins/marketplace.json")

    version_by_manifest_location = {
        "server.json .version": server["version"],
        "server.json .packages[0].version": server["packages"][0]["version"],
        "claude plugin.json .version": claude["version"],
        "codex plugin.json .version": codex["version"],
        ".agents marketplace .plugins[0].version": agents["plugins"][0]["version"],
    }
    drift = {where: manifest_version
             for where, manifest_version in version_by_manifest_location.items()
             if manifest_version != expected_version}
    assert not drift, f"version drift vs pyproject {expected_version!r}: {drift}"


def test_twin_plugin_manifests_agree_on_metadata():
    """Claude/Codex 쌍둥이 plugin.json 이 공통 메타데이터에서 갈라지지 않는다."""
    claude = _load_json("plugins/kis-trader/.claude-plugin/plugin.json")
    codex = _load_json("plugins/kis-trader/.codex-plugin/plugin.json")
    for field in ("name", "version", "description", "homepage", "keywords"):
        assert claude[field] == codex[field], f"{field} differs: {claude[field]!r} vs {codex[field]!r}"


def test_hooks_reference_existing_guard_scripts():
    """hooks.json 의 command 가 가리키는 스크립트가 실제로 플러그인 안에 존재한다."""
    hooks = _load_json("plugins/kis-trader/hooks/hooks.json")
    referenced = [
        entry["command"]
        for group in hooks["hooks"].values()
        for matcher in group
        for entry in matcher["hooks"]
    ]
    assert referenced, "hooks.json declared no hook commands"
    for command in referenced:
        # command 형태: python3 "${CLAUDE_PLUGIN_ROOT}/hooks/<name>.py"
        assert "${CLAUDE_PLUGIN_ROOT}" in command
        relative_hook_path = command.split("${CLAUDE_PLUGIN_ROOT}/", 1)[1].rstrip('"').strip()
        assert (_PLUGIN / relative_hook_path).is_file(), f"hook script missing: {relative_hook_path}"


def test_claude_marketplace_source_points_at_the_plugin():
    """.claude-plugin/marketplace.json 의 플러그인 source 경로가 실제 플러그인 디렉터리를 가리킨다."""
    marketplace_manifest = _load_json(".claude-plugin/marketplace.json")
    entry = marketplace_manifest["plugins"][0]
    target = (_ROOT / entry["source"]).resolve()
    assert target.is_dir(), f"source path does not resolve: {entry['source']}"
    assert (target / ".claude-plugin" / "plugin.json").is_file()


def test_plugin_server_requirement_pins_tested_published_version():
    """서버 실행 요구사항은 검증한 배포 버전에 고정하고 실행 명령을 보존한다."""
    manifest = _load_json("plugins/kis-trader/.claude-plugin/plugin.json")
    server = manifest["mcpServers"]["kis-trader"]
    assert server["command"] == "uvx"
    assert len(server["args"]) == 3
    assert server["args"][0] == "--from"
    assert server["args"][2] == "kis-mcp"
    requirement = Requirement(server["args"][1])
    assert requirement.name == "kis-trader"
    assert requirement.extras == {"mcp"}
    assert str(requirement.specifier) == "==0.11.0"
    assert requirement.url is None
    assert requirement.marker is None
