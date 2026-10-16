from tools.param_extract import extract_sent_param_keys


def test_extracts_literal_param_keys_from_fetch_by_market_cap():
    scan = extract_sent_param_keys("kis_trader.overseas._engine.ranking:fetch_by_market_cap")
    assert scan.resolved is True
    # 현재 코드는 CURR_GB 를 안 보낸다(=버그, Task 5 에서 수정) -- 추출기는 코드 사실만 반영
    assert "EXCD" in scan.keys
    assert "CURR_GB" not in scan.keys


def test_extracts_conditional_and_subscript_keys_from_by_change():
    scan = extract_sent_param_keys("kis_trader.overseas._engine.ranking:fetch_by_change")
    assert {"EXCD", "GUBN", "NDAY", "VOL_RANG"} <= scan.keys
