from tools.param_extract import extract_sent_param_keys


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
