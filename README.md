# kis-openapi

A clean Python client for the Korea Investment & Securities (KIS) Open API.

```python
from kis_openapi import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

price = kis.domestic.stock("005930").quote()          # 삼성전자 현재가
aapl  = kis.overseas.stock("AAPL").quote()             # AAPL (거래소 자동 해석)
gainers = kis.domestic.ranking.by_change(top="gainers")
```

**Audience: Korean KIS users.** Public **identifiers** use industry-standard English
terms (as used across international market-data and brokerage APIs); the explanatory
**docstrings are written in Korean** for the audience, and carry the underlying KIS URLs
and TR-ids. The guides under [`docs/`](docs/) are likewise in Korean.

Requires Python ≥ 3.11.

## Install

Greenfield (version `0.0.0`, not yet on PyPI) — install from source:

```bash
uv pip install -e .        # or: pip install -e .
```

## The shape of the API

Everything hangs off one session object, `KISClient`, split by **asset class** — you never
mirror KIS's URL tree by hand.

| Top level | What it is |
|---|---|
| `kis.domestic` | Domestic (KRX/NXT) stocks, indices, bonds, ELW, index futures/options, account, rankings, market, calendar |
| `kis.overseas` | Overseas stocks, indices, futures/options, account, rankings, reference data |
| `kis.pension` | Retirement-pension account (deposit / buyable / balance / orders) |
| `kis.orders` | Asset-neutral order lifecycle (`reconcile` / `cancel` / `modify`) |
| `kis.instrument` | Symbol resolution *before* an asset class is known |
| `kis.transport` / `kis.environment` / `kis.revoke_token()` | Session plumbing |

**Instrument handles** are how you talk to a single security or contract:

```python
kis.domestic.stock("005930")     # -> DomesticStock  (quote/bars/order_book/buy/sell/…)
kis.overseas.stock("AAPL")       # -> OverseasStock   (exchange auto-resolved from the master)
kis.domestic.futures("101W09")   # -> FuturesContract (has underlying_quote())
kis.domestic.option("201W09")    # -> OptionContract  (no underlying_quote — futures-only)
```

A handle only exposes what that instrument actually supports: an `OptionContract` has no
`underlying_quote()`, so a wrong call is caught by your type checker, not at runtime.

Result objects are **frozen** and read-only; every one keeps the raw vendor payload on a
private `_raw` escape hatch, and each field carries its KIS wire key + unit in the docstring.

## Market-wide rankings

`kis.domestic.ranking.*` returns market-wide rankings as lists of typed rows:

```python
kis.domestic.ranking.by_change(top="gainers")     # 등락률
kis.domestic.ranking.by_volume()                  # 거래량
kis.domestic.ranking.by_market_cap()              # 시가총액
kis.domestic.ranking.by_short_sale(window="1d")   # 공매도
```

Overseas rankings live under `kis.overseas.ranking.*`.

## Placing orders — a safety-first order path

Orders go through a client-side safety kernel that treats a brokerage order as what it is:
an irreversible, non-idempotent side effect.

```python
report = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)
report = kis.orders.reconcile(report.client_order_id)   # confirm against the broker
kis.orders.cancel(report.client_order_id)
```

The guarantees:

- **Client-side idempotency** — each order carries a `client_order_id`; a resend of the
  same logical order is de-duplicated by a persisted fingerprint, so a retry can never
  become a second order.
- **No retry on a write** — a POST that times out is left *in-flight*, never resent.
- **Conservative reconcile** — an ambiguous or non-finite broker response never confirms
  an order; uncertainty leaves it in-flight for you to check.

High-risk actions are **off by default**: `KISClient(orderable=…)` gates whether an account
may order at all (auto-derived from the account product type), and credit orders require an
explicit `allow_credit=True`. Optional `RiskLimits` add pre-trade quantity / notional /
price-collar caps. See [`docs/orders-and-safety.md`](docs/orders-and-safety.md).

## Documentation

- [`docs/quickstart.md`](docs/quickstart.md) — credentials, the client, your first calls
- [`docs/domestic.md`](docs/domestic.md) — `kis.domestic` in full
- [`docs/overseas.md`](docs/overseas.md) — `kis.overseas` in full
- [`docs/pension.md`](docs/pension.md) — `kis.pension`
- [`docs/orders-and-safety.md`](docs/orders-and-safety.md) — the order path and its safety model

Per-endpoint detail (parameters, the KIS URL, the TR-id, every field) lives in the Korean
docstring of each method — read it with `help(...)` or your IDE.
