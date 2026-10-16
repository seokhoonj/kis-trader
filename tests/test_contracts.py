"""정적 contract test -- 코드가 보내는 요청 키 ⊇ 계약 필수 키. CI 편입, 크리덴셜 불요."""
from __future__ import annotations

import pytest
from tools.contracts import load_contracts
from tools.param_extract import extract_sent_param_keys

_CONTRACTS = load_contracts()


@pytest.mark.parametrize("c", _CONTRACTS, ids=lambda c: c.name)
def test_code_sends_required_params(c):
    scan = extract_sent_param_keys(c.impl)
    if not scan.resolved:
        pytest.skip(f"{c.name}: params 동적 구성 -- live_required (정적 확정 불가)")
    missing = set(c.required_params) - scan.keys
    assert not missing, (
        f"{c.name}: 코드가 필수 파라미터 {sorted(missing)} 를 안 보냄 "
        f"(계약 source: {c.source_url}). impl={c.impl}"
    )
