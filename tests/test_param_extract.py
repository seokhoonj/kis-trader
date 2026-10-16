from tools.param_extract import extract_sent_param_keys


def test_extracts_literal_param_keys_from_fetch_by_market_cap():
    scan = extract_sent_param_keys("kis_trader.overseas._engine.ranking:fetch_by_market_cap")
    assert scan.resolved is True
    # 추출기는 코드가 실제로 보내는 키만 반영한다: 이 impl 은 EXCD 를 보내고 CURR_GB 는 안 보낸다.
    assert "EXCD" in scan.keys
    assert "CURR_GB" not in scan.keys


def test_extracts_conditional_and_subscript_keys_from_by_change():
    scan = extract_sent_param_keys("kis_trader.overseas._engine.ranking:fetch_by_change")
    assert {"EXCD", "GUBN", "NDAY", "VOL_RANG"} <= scan.keys
