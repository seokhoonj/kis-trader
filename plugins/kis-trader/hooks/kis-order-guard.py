#!/usr/bin/env python3
"""PreToolUse order guard -- 실전(real) 환경 주문으로 보이는 명령에 사용자 확인을 강제한다.

Bash 명령(또는 스크립트 작성)이 **실전 환경 신호**(`KIS_MCP_ENVIRONMENT=real`,
`environment="real"` 등)와 **주문 동사**(`.buy(` / `.sell(` / `place_order` / `.order(` 등)를
함께 담고 있으면 `permissionDecision: "ask"` 로 사용자에게 확인을 요청한다. 모의(paper)이거나
주문이 아니면 통과시킨다.

이것은 **best-effort 심층 방어**다 -- 임의 파이썬에서 실주문을 100% 식별할 수는 없다. 실주문의
권한 있는(authoritative) 안전장치는 라이브러리(OrderTimeoutError/in-flight)와 MCP 서버의
이중게이트+RiskLimits+allowlist+elicitation 이다. 이 훅은 명백한 실전 주문 명령에 한 번 더 확인을
붙일 뿐이다. 판정이 안 서거나 내부 오류면 exit 0 으로 정상 흐름에 맡긴다.
"""

from __future__ import annotations

import json
import re
import sys

_REAL_ENV = re.compile(
    r"""(KIS_MCP_ENVIRONMENT|KIS_ENVIRONMENT|environment)    # 환경 키
        \s*[:=]\s*                                           # = 또는 :
        (["']?)real\2\b""",                                  # real (단어경계 -- realtime/realm 제외)
    re.IGNORECASE | re.VERBOSE,
)
_ORDER_VERB = re.compile(
    r"(?i)(\.buy\(|\.sell\(|\bplace_order\b|\.order\(|\bsubmit_order\b|\border_cash\b|\bcancel_order\b|\bmodify_order\b)"
)


def _candidate_text(tool_input: dict[str, object]) -> str:
    parts: list[str] = []
    for key in ("command", "content", "new_string", "file_text", "new_source"):
        value = tool_input.get(key)
        if isinstance(value, str):
            parts.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                new_string = edit.get("new_string")
                if isinstance(new_string, str):
                    parts.append(new_string)
    return "\n".join(parts)


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return 0

    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    text = _candidate_text(tool_input)
    if not text:
        return 0

    if _REAL_ENV.search(text) and _ORDER_VERB.search(text):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": (
                    "실전(real) 환경에서 주문을 보내는 명령으로 보입니다. 종목/방향/수량/가격이 "
                    "의도한 대로인지 직접 확인한 뒤 승인하세요. (모의로 먼저 검증하는 것을 권장)"
                ),
            }
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
