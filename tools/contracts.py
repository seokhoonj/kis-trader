"""contract(*.toml) 로더 -- 정적 테스트와 라이브 probe 공용."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"


@dataclass(frozen=True, slots=True)
class Contract:
    name: str
    impl: str
    tr_id: str
    path: str
    method: str
    required_params: tuple[str, ...]
    optional_params: tuple[str, ...]
    response_fields: tuple[str, ...]
    invariants: tuple[str, ...]
    source_url: str
    notes: str


def _as_contract(d: dict) -> Contract:
    def seq(key: str) -> tuple[str, ...]:
        return tuple(d.get(key, []) or [])

    return Contract(
        name=d["name"], impl=d["impl"], tr_id=d["tr_id"], path=d["path"],
        method=d.get("method", "GET"),
        required_params=seq("required_params"), optional_params=seq("optional_params"),
        response_fields=seq("response_fields"), invariants=seq("invariants"),
        source_url=d.get("source_url", ""), notes=d.get("notes", ""),
    )


def load_contracts(area: str | None = None) -> list[Contract]:
    files = [CONTRACTS_DIR / f"{area}.toml"] if area else sorted(CONTRACTS_DIR.glob("*.toml"))
    out: list[Contract] = []
    for f in files:
        data = tomllib.loads(f.read_text(encoding="utf-8"))
        out.extend(_as_contract(e) for e in data.get("endpoint", []))
    return out
