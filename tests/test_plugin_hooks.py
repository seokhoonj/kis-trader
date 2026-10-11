"""플러그인 PreToolUse 가드 훅(secret-guard / order-guard) 회귀 테스트.

훅 스크립트는 패키지가 아니라 plugins/kis-trader/hooks/ 의 독립 .py 라, importlib 로 로드해
모듈 레벨 정규식/헬퍼를 직접 검증한다. 두 훅 다 조용한-실패 버그 이력이 있어(= order-guard 의
따옴표형 real 미매칭, secret-guard 의 JSON "appkey":"v" 미매칭) 패턴을 못박는다.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

_HOOKS = pathlib.Path(__file__).resolve().parent.parent / "plugins" / "kis-trader" / "hooks"


def _load_hook_module(filename: str):
    path = _HOOKS / filename
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_")[:-3], path)
    assert spec and spec.loader
    hook_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook_module)
    return hook_module


def test_order_guard_real_env_matches_quoted_and_unquoted_forms():
    order_guard = _load_hook_module("kis-order-guard.py")
    for text in (
        'environment="real"', "environment='real'", "KIS_MCP_ENVIRONMENT=real",
        '{"environment": "real"}', "environment = 'real'", "KIS_ENVIRONMENT=real",
    ):
        assert order_guard._REAL_ENV.search(text), f"should match: {text!r}"
    for text in ("environment=realtime", "environment=realm", 'environment="paper"', "KIS_MCP_ENVIRONMENT=paper"):
        assert not order_guard._REAL_ENV.search(text), f"should NOT match: {text!r}"


def test_order_guard_order_verbs():
    order_guard = _load_hook_module("kis-order-guard.py")
    for text in (".buy(", ".sell(", "place_order(", ".order(", "cancel_order(", "modify_order("):
        assert order_guard._ORDER_VERB.search(text), f"should match verb: {text!r}"
    assert not order_guard._ORDER_VERB.search("inquire_balance(")


def test_secret_guard_catches_credential_forms():
    secret_guard = _load_hook_module("kis-secret-guard.py")
    sample_credential = "Ab3xZ9Qw8Lm2Kp7Rt5Yu1Vn4Bc6De0Fg"
    for text in (
        f'app_secret = "{sample_credential}"',          # 파이썬 kwarg
        f'{{"appkey": "{sample_credential}"}}',         # JSON / credentials.json 형식
        f'curl -H "appkey: {sample_credential}"',       # HTTP 헤더
        f"https://x?appkey={sample_credential}&y=1",    # URL 쿼리
        f'access_token="{sample_credential}"',          # access token
    ):
        secret_match = secret_guard._SECRET.search(text)
        assert secret_match and secret_guard._is_real_secret(secret_match.group("value")), \
            f"should flag secret in: {text!r}"


def test_secret_guard_spares_placeholders_and_non_secrets():
    secret_guard = _load_hook_module("kis-secret-guard.py")
    # 자리표시자(밑줄 결합 포함)는 실제 값이 아니다.
    for placeholder in ("YOUR_APP_SECRET_HERE_1234567890", "REPLACE_WITH_YOUR_KEY_000000"):
        secret_match = secret_guard._SECRET.search(f'app_secret = "{placeholder}"')
        assert secret_match is None or not secret_guard._is_real_secret(secret_match.group("value")), placeholder
    # 라벨 없는 git SHA / UUID 는 매칭되지 않는다(라벨 접두 필요).
    assert secret_guard._SECRET.search("git checkout 9acf6e2abcd1234567890ef") is None


def test_secret_guard_candidate_text_reads_multiedit_and_notebook():
    secret_guard = _load_hook_module("kis-secret-guard.py")
    text = secret_guard._candidate_text({"edits": [{"new_string": "A"}, {"new_string": "B"}], "new_source": "C"})
    assert "A" in text and "B" in text and "C" in text


def test_secret_guard_catches_account_and_htsid():
    secret_guard = _load_hook_module("kis-secret-guard.py")
    for text in (
        'cano = "12345678"',           # 종합계좌 8자리
        'account_no="12345678-01"',    # 계좌-상품코드
        '{"htsid": "mykisid01"}',      # HTS 로그인 아이디
        "hts_id=trader2026",
    ):
        account_match = secret_guard._ACCOUNT_OR_HTS_ID.search(text)
        assert account_match and secret_guard._is_real_secret(account_match.group("value")), \
            f"should flag account/id in: {text!r}"
    # 라벨 없는 bare account= (라이브러리 인자/테스트 더미) 는 걸리지 않는다.
    assert secret_guard._ACCOUNT_OR_HTS_ID.search('account="12345678-01"') is None
    # 숫자 없는 식별자 대입(라이브러리가 상시 쓰는 일상 코드)은 hard-deny 하면 안 된다 -- lookahead 가 거른다.
    for benign in ('cano = resultvar', 'hts_id = username', 'account_no = order_ident',
                   'account_number = response_json', 'cano = fetch_value()'):
        assert secret_guard._ACCOUNT_OR_HTS_ID.search(benign) is None, f"should NOT flag benign code: {benign!r}"


def _run_hook(filename: str, tool_input: dict, env: dict | None = None) -> dict:
    """훅을 서브프로세스로 실행하고(stdin=JSON) deny 판정을 파싱한다. env 로 ambient 환경변수 주입."""
    run_env = dict(os.environ)
    run_env.pop("KIS_MCP_ENVIRONMENT", None)
    run_env.pop("KIS_ENVIRONMENT", None)
    if env:
        run_env.update(env)
    completed_process = subprocess.run(
        [sys.executable, str(_HOOKS / filename)],
        input=json.dumps({"tool_input": tool_input}),
        capture_output=True, text=True, env=run_env, check=False,
    )
    hook_output = completed_process.stdout.strip()
    return json.loads(hook_output) if hook_output else {}


def test_order_guard_blocks_order_under_ambient_real_env():
    # 명령 텍스트엔 "real" 이 없어도, 세션이 KIS_MCP_ENVIRONMENT=real 이면 주문을 막아야 한다.
    hook_output = _run_hook("kis-order-guard.py", {"command": "kis.domestic.stock('005930').buy(quantity=10)"},
                            env={"KIS_MCP_ENVIRONMENT": "real"})
    assert hook_output.get("hookSpecificOutput", {}).get("permissionDecision") == "ask"


def test_order_guard_allows_order_without_real_env():
    # real 신호가 (텍스트에도 환경변수에도) 없으면 조용히 통과한다.
    hook_output = _run_hook("kis-order-guard.py", {"command": "kis.domestic.stock('005930').buy(quantity=10)"})
    assert hook_output == {}


def test_order_guard_does_not_ask_on_read_under_ambient_real_env():
    # ambient real 세션이어도 주문 동사가 없는 읽기 명령은 물어보지 않는다(usability: over-ask 방지).
    # AND 분기(real-signal AND order-verb)가 OR 로 퇴화하면 모든 명령에 물어봐 이 테스트가 깨진다.
    hook_output = _run_hook("kis-order-guard.py", {"command": "kis.domestic.stock('005930').quote()"},
                            env={"KIS_MCP_ENVIRONMENT": "real"})
    assert hook_output == {}


def test_secret_guard_denies_real_secret_end_to_end():
    # 차단 경로를 main() 까지 구동(regex 단위테스트만으론 deny JSON 출력/루프 회귀를 못 잡는다).
    secret = "Ab3xZ9Qw8Lm2Kp7Rt5Yu1Vn4Bc6De0Fg"
    hook_output = _run_hook("kis-secret-guard.py", {"content": f'app_secret = "{secret}"'})
    assert hook_output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_secret_guard_denies_account_identifier_end_to_end():
    hook_output = _run_hook("kis-secret-guard.py", {"content": 'cano = "12345678"'})
    assert hook_output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_secret_guard_allows_placeholder_and_benign_code_end_to_end():
    assert _run_hook("kis-secret-guard.py",
                     {"content": 'app_secret = "YOUR_APP_SECRET_HERE_1234567890"'}) == {}
    assert _run_hook("kis-secret-guard.py", {"command": "cano = resultvar"}) == {}
