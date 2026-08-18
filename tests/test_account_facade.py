from kis_trader.client import KISClient


def _c(account: str | None) -> KISClient:
    return KISClient(app_key="k", app_secret="s", account=account, environment="paper")


def test_account_string_moved_to_private():
    assert _c("12345678-01")._account == "12345678-01"
    assert _c(None)._account is None
