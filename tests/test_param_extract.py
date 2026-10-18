import ast

from tools.param_extract import _collect_builder_keys, extract_sent_param_keys


def test_extracts_literal_param_keys_from_fetch_by_market_cap():
    scan = extract_sent_param_keys("kis_trader.overseas._engine.ranking:fetch_by_market_cap")
    assert scan.resolved is True
    # 추출기는 코드가 실제로 보내는 키를 반영한다: 이 impl 은 EXCD·CURR_GB 를 보낸다.
    assert "EXCD" in scan.keys
    assert "CURR_GB" in scan.keys


def test_extracts_conditional_and_subscript_keys_from_by_change():
    scan = extract_sent_param_keys("kis_trader.overseas._engine.ranking:fetch_by_change")
    assert {"EXCD", "GUBN", "NDAY", "VOL_RANG"} <= scan.keys


def test_follows_params_builder_helper():
    # params 가 같은 모듈 헬퍼(_fundamentals_params)로 조립돼 변수로만 넘어가도, 그 헬퍼의 dict 키까지
    # 따라가 정적 확정한다 -- 이 추적이 없으면 required ⊆ sent 검증이 빈 집합으로 공허해진다.
    scan = extract_sent_param_keys("kis_trader.domestic._engine.ranking:fetch_financial_ratio")
    assert scan.resolved is True
    assert {"FID_COND_MRKT_DIV_CODE", "FID_RANK_SORT_CLS_CODE",
            "FID_INPUT_OPTION_1", "FID_INPUT_OPTION_2", "FID_BLNG_CLS_CODE"} <= scan.keys


def test_builder_keys_ignore_unrelated_dicts():
    # 빌더 헬퍼의 로깅/룩업 등 **반환하지 않는** dict 키는 전송 파라미터로 세지 않는다 -- 세면 보내지도
    # 않는 required 키를 거짓 PASS 시킨다(계약 검사 무력화).
    src = ("def helper():\n"
           "    unrelated = {'NOT_SENT': 'x'}\n"
           "    note({'ALSO_NOT': 1})\n"
           "    return {'SENT_A': '1', 'SENT_B': '2'}\n")
    fn = ast.parse(src).body[0]
    keys: set[str] = set()
    assert _collect_builder_keys(fn, keys) is True
    assert keys == {"SENT_A", "SENT_B"}


def test_builder_keys_unresolved_on_variable_return():
    # 반환이 dict 리터럴이 아니면(변수/mutation 조립) 정적 추적 불가 -> False(보수적 skip, 거짓 PASS 아님).
    src = ("def helper():\n"
           "    params = {'X': '1'}\n"
           "    params['Y'] = '2'\n"
           "    return params\n")
    fn = ast.parse(src).body[0]
    keys: set[str] = set()
    assert _collect_builder_keys(fn, keys) is False


def test_builder_keys_unresolved_on_disjoint_conditional_returns():
    # 조건부 두 갈래가 서로 다른 키를 돌려주면 union 하지 않는다 -- 한 갈래에만 있는 키를 '항상 전송'으로
    # 오인해 required 검사를 거짓 PASS 시키므로, 여러 return 은 resolved=False(보수적 skip).
    src = ("def helper(flag):\n"
           "    if flag:\n"
           "        return {'A': '1'}\n"
           "    return {'B': '2'}\n")
    fn = ast.parse(src).body[0]
    keys: set[str] = set()
    assert _collect_builder_keys(fn, keys) is False
    assert keys == set()  # 어느 갈래 키도 수집하지 않는다(부분 union 금지)


def test_builder_keys_ignore_nested_function_returns():
    # 중첩 함수(동기/비동기)의 return 은 이 헬퍼의 반환이 아니다 -- 바깥 return dict 키만 센다.
    src = ("def helper():\n"
           "    def inner():\n"
           "        return {'NESTED': 'x'}\n"
           "    async def ainner():\n"
           "        return {'ANESTED': 'y'}\n"
           "    return {'SENT': '1'}\n")
    fn = ast.parse(src).body[0]
    keys: set[str] = set()
    assert _collect_builder_keys(fn, keys) is True
    assert keys == {"SENT"}
