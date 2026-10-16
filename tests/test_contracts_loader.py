from tools.contracts import Contract, load_contracts


def test_loads_change_contract():
    contracts = load_contracts("overseas_ranking")
    by_name = {c.name: c for c in contracts}
    c = by_name["overseas_ranking_change"]
    assert isinstance(c, Contract)
    assert c.impl == "kis_trader.overseas._engine.ranking:fetch_by_change"
    assert "GUBN" in c.required_params
    assert c.required_params == ("EXCD", "NDAY", "GUBN", "VOL_RANG", "AUTH", "KEYB")


def test_area_none_merges_all_files():
    assert any(c.name == "overseas_ranking_change" for c in load_contracts())
