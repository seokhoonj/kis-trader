"""플러그인 PreToolUse 가드 훅(secret-guard / order-guard) 회귀 테스트.

훅 스크립트는 패키지가 아니라 plugins/kis-trader/hooks/ 의 독립 .py 라, importlib 로 로드해
모듈 레벨 정규식/헬퍼를 직접 검증한다. 두 훅 다 조용한-실패 버그 이력이 있어(= order-guard 의
따옴표형 real 미매칭, secret-guard 의 JSON "appkey":"v" 미매칭) 패턴을 못박는다.
"""

from __future__ import annotations

import importlib.util
import pathlib

_HOOKS = pathlib.Path(__file__).resolve().parent.parent / "plugins" / "kis-trader" / "hooks"


def _load(filename: str):
    path = _HOOKS / filename
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_")[:-3], path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_order_guard_real_env_matches_quoted_and_unquoted_forms():
    g = _load("kis-order-guard.py")
    for text in (
        'environment="real"', "environment='real'", "KIS_MCP_ENVIRONMENT=real",
        '{"environment": "real"}', "environment = 'real'", "KIS_ENVIRONMENT=real",
    ):
        assert g._REAL_ENV.search(text), f"should match: {text!r}"
    for text in ("environment=realtime", "environment=realm", 'environment="paper"', "KIS_MCP_ENVIRONMENT=paper"):
        assert not g._REAL_ENV.search(text), f"should NOT match: {text!r}"


def test_order_guard_order_verbs():
    g = _load("kis-order-guard.py")
    for text in (".buy(", ".sell(", "place_order(", ".order(", "cancel_order(", "modify_order("):
        assert g._ORDER_VERB.search(text), f"should match verb: {text!r}"
    assert not g._ORDER_VERB.search("inquire_balance(")


def test_secret_guard_catches_credential_forms():
    s = _load("kis-secret-guard.py")
    real = "Ab3xZ9Qw8Lm2Kp7Rt5Yu1Vn4Bc6De0Fg"
    for text in (
        f'app_secret = "{real}"',          # 파이썬 kwarg
        f'{{"appkey": "{real}"}}',         # JSON / credentials.json 형식
        f'curl -H "appkey: {real}"',       # HTTP 헤더
        f"https://x?appkey={real}&y=1",    # URL 쿼리
        f'access_token="{real}"',          # access token
    ):
        m = s._SECRET.search(text)
        assert m and s._is_real_secret(m.group("value")), f"should flag secret in: {text!r}"


def test_secret_guard_spares_placeholders_and_non_secrets():
    s = _load("kis-secret-guard.py")
    # 자리표시자(밑줄 결합 포함)는 실제 값이 아니다.
    for placeholder in ("YOUR_APP_SECRET_HERE_1234567890", "REPLACE_WITH_YOUR_KEY_000000"):
        m = s._SECRET.search(f'app_secret = "{placeholder}"')
        assert m is None or not s._is_real_secret(m.group("value")), placeholder
    # 라벨 없는 git SHA / UUID 는 매칭되지 않는다(라벨 접두 필요).
    assert s._SECRET.search("git checkout 9acf6e2abcd1234567890ef") is None


def test_secret_guard_candidate_text_reads_multiedit_and_notebook():
    s = _load("kis-secret-guard.py")
    text = s._candidate_text({"edits": [{"new_string": "A"}, {"new_string": "B"}], "new_source": "C"})
    assert "A" in text and "B" in text and "C" in text
