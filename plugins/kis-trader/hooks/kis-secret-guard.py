#!/usr/bin/env python3
"""PreToolUse secret guard -- KIS 자격증명이 파일/명령으로 새는 것을 차단한다.

Claude Code 가 Bash/Write/Edit/MultiEdit/NotebookEdit 를 실행하기 전에 그 입력을 stdin(JSON)으로
받아, KIS app key/secret/approval key/access token 의 **라벨이 붙은 실제 값**(긴 영숫자 리터럴)이나
Bearer 토큰이 들어 있으면 그 도구 호출을 거부한다(`permissionDecision: deny`).

잡는 형태(라벨 + 값): `app_key = "..."`(파이썬 kwarg), `"appkey": "..."`(JSON/credentials.json),
`appkey: ...`(HTTP 헤더), `?appkey=...`(URL 쿼리), `export KIS_APP_SECRET=...`. 값은 20자 이상
base64/urlsafe-base64 문자이어야 하고, 흔한 자리표시자(YOUR/EXAMPLE/XXXX...)는 면제한다.

한계(honest): **라벨 기반**이라 라벨 없는 위치인자(`Kis("...", "...")`)나 알 수 없는 입력 shape 는
못 잡는다. 이것은 advisory 심층 방어이지 하드 경계가 아니다 -- 1차 방어는 MCP 서버의 `_serialize`
와 자격증명 저장소 분리다. 판정이 안 서거나 내부 오류면 exit 0 으로 정상 권한 흐름에 맡긴다.
"""

from __future__ import annotations

import json
import re
import sys

# 라벨(키 이름) + 선택적 따옴표/공백 + [:=] + 선택적 따옴표 + 긴 값.
# 닫는 따옴표를 강제하지 않아 JSON("key":"v")·헤더(key: v)·URL(?key=v)·kwarg(key="v") 를 모두 잡는다.
# 값 문자류에 urlsafe-base64(_ -) 포함.
_SECRET = re.compile(
    r"""(app_?key|app_?secret|approval_?key|secret_key|access_?token)
        ["']?\s*[:=]\s*["']?
        (?P<value>[A-Za-z0-9+/=_\-]{20,})""",
    re.IGNORECASE | re.VERBOSE,
)
_BEARER = re.compile(r"\bBearer\s+(?P<value>[A-Za-z0-9._\-]{20,})")
# 계좌번호(CANO)/HTS ID -- 토큰(20자+)보다 짧아 _SECRET 엔 안 걸리므로 라벨 기반으로 따로 잡는다.
# 계좌는 8자리 종합계좌(선택적으로 -상품코드), HTS ID 는 영숫자 로그인 아이디. 라벨이 있어야
# 매칭되므로(bare `account=` 는 제외) 테스트의 더미 `account="..."` 는 걸리지 않는다.
_ACCOUNT_ID = re.compile(
    r"""(cano|account_?no|account_?number|htsid|hts_?id)
        ["']?\s*[:=]\s*["']?
        (?P<value>[A-Za-z0-9][A-Za-z0-9\-]{4,})""",
    re.IGNORECASE | re.VERBOSE,
)
# 실제 값이 아니라 자리표시자면 면제(문서/예시). 밑줄(_)도 구분자로 취급해야
# YOUR_APP_SECRET_HERE 류를 잡는다(\b 는 _ 를 단어문자로 봐서 경계가 안 생김).
_PLACEHOLDER = re.compile(
    r"(?i)(?:^|[^a-z0-9])(your|example|sample|placeholder|dummy|xxx+|fixme|todo|here)(?:[^a-z0-9]|$)"
)


def _candidate_text(tool_input: dict[str, object]) -> str:
    """도구 입력에서 검사할 텍스트를 모은다 -- Bash 명령/파일 내용/편집 신규문자열/노트북 셀.

    Edit 의 ``new_string``, Write 의 ``content``, MultiEdit 의 ``edits[].new_string``,
    NotebookEdit 의 ``new_source`` 를 포함한다(``old_string`` 은 제거 쪽이라 누출 경로가 아니다)."""
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


def _is_real_secret(value: str) -> bool:
    """자리표시자가 아닌 실제 값으로 보이는가(플레이스홀더 단어 없음)."""
    return _PLACEHOLDER.search(value) is None


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return 0  # 파싱 불가 -- 막지 않고 정상 흐름에 맡긴다(advisory).

    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    text = _candidate_text(tool_input)
    if not text:
        return 0

    for match in _SECRET.finditer(text):
        if _is_real_secret(match.group("value")):
            _deny("KIS app key/secret/approval key/access token 으로 보이는 실제 값이 포함되어 "
                  "있습니다. 자격증명은 파일/명령에 넣지 말고 `kis config` 저장소나 환경변수로 "
                  "관리하세요.")
            return 0
    for match in _BEARER.finditer(text):
        if _is_real_secret(match.group("value")):
            _deny("Bearer 액세스 토큰으로 보이는 값이 포함되어 있습니다. 토큰을 파일/명령에 "
                  "하드코딩하지 마세요.")
            return 0
    for match in _ACCOUNT_ID.finditer(text):
        if _is_real_secret(match.group("value")):
            _deny("계좌번호(CANO) 또는 HTS ID 로 보이는 값이 포함되어 있습니다. 계좌/아이디는 "
                  "파일/명령에 하드코딩하지 말고 `kis config` 저장소나 환경변수로 관리하세요.")
            return 0
    return 0


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


if __name__ == "__main__":
    sys.exit(main())
