# kis-trader

[![check](https://github.com/seokhoonj/kis-trader/actions/workflows/check.yml/badge.svg)](https://github.com/seokhoonj/kis-trader/actions/workflows/check.yml)
[![PyPI](https://img.shields.io/pypi/v/kis-trader)](https://pypi.org/project/kis-trader/)
[![Python](https://img.shields.io/pypi/pyversions/kis-trader)](https://pypi.org/project/kis-trader/)
[![License](https://img.shields.io/pypi/l/kis-trader)](https://github.com/seokhoonj/kis-trader/blob/main/LICENSE)

[한국어](README.md) | **English**

An unofficial Python client for the Korea Investment & Securities (KIS) **Open API** — wrapped in a clean, typed API.

- **Broad coverage** — domestic and overseas stocks, indices, ETFs/ETNs, ELWs, futures/options, and bonds: quotes, financials and flows, account balances and P&L, rankings and conditional screens, market data and calendars, retirement pensions.
- **Orders** — buy/sell/modify/cancel, plus credit, reserved, and TWAP/VWAP-split orders.
- **Safety core** — duplicate order sends are blocked by default; on a timeout nothing is resent — you confirm acceptance with `reconcile()`. Credit trading is blocked by default. Pre-trade risk limits are an explicit opt-in through `KISClient(..., risk=RiskLimits(...))`.
- **Real-time** — WebSocket subscriptions for quotes, order books, and execution notices.
- **Three interfaces** — a Python API, the `kis` CLI, and an MCP server / skills for AI agents.

## 1. Install

```bash
pip install kis-trader
```

Requires Python 3.11+.

To use it as an MCP server from an AI agent such as Claude Desktop, install the optional `mcp` extra:

```bash
pip install 'kis-trader[mcp]'
```

## 2. Quickstart

```python
from kis_trader import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01", environment="real")

kis.domestic.stock("005930").quote()                 # Samsung Electronics quote
kis.overseas.stock("AAPL").quote()                   # Apple (exchange resolved automatically)
kis.domestic.ranking.by_change(direction="gainers")  # today's top gainers
```

Run `kis config` in the terminal to enter each account type's app key and account interactively and
store them, or save them as environment variables or with `KISConfig(...).save()`. Once saved, later
`KISClient()` calls can omit the arguments. The profile (account type) and the environment (live or
paper) are separate axes, so you open the same profile under either environment just by switching the
environment — rehearse the order flow on paper before switching to live. For issuing and storing
credentials, see the [Credentials and profiles](https://seokhoonj.github.io/kis-trader/configuration.html) chapter.

## 3. Structure

Under `KISClient`, asset classes and orders split into namespaces.

```python
kis.domestic   # domestic: stocks, indices, ETFs, ELWs, futures/options, bonds, rankings, market, calendar
kis.overseas   # overseas: stocks, indices, futures/options, rankings
kis.account    # account queries -- balances, P&L, positions, open orders (.domestic/.overseas market views, IRP adds .pension)
kis.orders     # list (open), check, modify, and cancel submitted orders
```

Reach a symbol or contract through a handle, then read quotes and place orders from it.

```python
kis.domestic.stock("005930")     # domestic stock -- quote, chart, order book, buy, sell
kis.overseas.stock("AAPL")       # overseas stock -- exchange resolved automatically
kis.domestic.futures("101W09")   # index futures
kis.domestic.option("201W09350")    # index options
```

## 4. Orders

```python
report = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)  # buy 10 shares
kis.orders.modify(report.client_order_id, limit_price=70500)   # amend the price
kis.orders.cancel(report.client_order_id)                      # cancel
```

Only when acceptance is uncertain — for example after a send timeout — do you settle the
state with `kis.orders.reconcile(report.client_order_id)` (for a completed order it just returns
the stored report — it does not re-query the broker).

Orders cannot be undone. A duplicate-order safeguard is on by default (credit trading is blocked
by default); the full rules are in the [Orders](https://seokhoonj.github.io/kis-trader/orders.html)
chapter.

## 5. Command line (`kis`)

Installing also adds the terminal command `kis`, so you can query and order without writing
Python. An order is not sent until you add `--execute` — until then it is a dry run.

```bash
kis config                      # store credentials in a profile (interactive)
kis stock quote 005930
kis search 삼성전자
kis ranking change --direction gainers
kis account balance
kis order buy 005930 10 --limit-price 70000
kis order buy 005930 10 --limit-price 70000 --execute paper --yes
```

Bonds, futures/options, overseas, reserved orders, TWAP and the full option set are in the
[Command line](https://seokhoonj.github.io/kis-trader/cli.html) chapter.

## 6. Use it from an AI coding agent

To manage an account through an AI tool such as Claude Code or Codex, use the
`plugins/kis-trader/skills/kis-trader/` skill. See the
[Claude skill](https://seokhoonj.github.io/kis-trader/claude-skill.html) and
[Codex skill](https://seokhoonj.github.io/kis-trader/codex-skill.html) chapters.

For MCP-compatible agents (Claude Desktop, etc.), the `kis_trader.mcp` server
(`pip install 'kis-trader[mcp]'`, then `kis-mcp`) exposes account-query, order-preview,
and domestic- and overseas-stock place/modify/cancel tools, and can switch the account and
environment it reads without a restart. Paper trading is the default; live orders go through a
double gate, human confirmation, and a session circuit breaker (place/modify also require RiskLimits
and a symbol allowlist; cancel is exempt as it reduces exposure). See the
[MCP Server](https://seokhoonj.github.io/kis-trader/mcp.html) chapter.

## 7. Documentation

The full documentation lives at **<https://seokhoonj.github.io/kis-trader/>** (source: `docs/`).

| Part | Chapters |
|------|----------|
| **Getting started** | [Quickstart](https://seokhoonj.github.io/kis-trader/quickstart.html) · [Issue an app key](https://seokhoonj.github.io/kis-trader/appkey.html) · [Credentials and profiles](https://seokhoonj.github.io/kis-trader/configuration.html) |
| **Domestic stocks** | [Quotes](https://seokhoonj.github.io/kis-trader/quotes.html) · [Financials](https://seokhoonj.github.io/kis-trader/financials.html) · [Flows](https://seokhoonj.github.io/kis-trader/flows.html) · [Account & profit](https://seokhoonj.github.io/kis-trader/account.html) · [Orders](https://seokhoonj.github.io/kis-trader/orders.html) |
| **Market & search** | [Rankings & screens](https://seokhoonj.github.io/kis-trader/screening.html) · [Market & indices](https://seokhoonj.github.io/kis-trader/market.html) · [Corporate actions & calendar](https://seokhoonj.github.io/kis-trader/corporate-actions.html) |
| **Overseas & pension** | [Overseas stocks](https://seokhoonj.github.io/kis-trader/overseas.html) · [Retirement pension](https://seokhoonj.github.io/kis-trader/pension.html) |
| **Other products** | [ETF & ETN](https://seokhoonj.github.io/kis-trader/etf.html) · [ELW](https://seokhoonj.github.io/kis-trader/elw.html) · [Futures & options](https://seokhoonj.github.io/kis-trader/derivatives.html) · [Bonds](https://seokhoonj.github.io/kis-trader/bonds.html) |
| **CLI & agents** | [Command Line](https://seokhoonj.github.io/kis-trader/cli.html) · [Claude Code Skill](https://seokhoonj.github.io/kis-trader/claude-skill.html) · [Codex Skill](https://seokhoonj.github.io/kis-trader/codex-skill.html) · [MCP Server](https://seokhoonj.github.io/kis-trader/mcp.html) |
| **Reference** | [Realtime (WebSocket)](https://seokhoonj.github.io/kis-trader/realtime.html) · [Limits & gaps](https://seokhoonj.github.io/kis-trader/limits.html) |

The arguments and return value of each method are available through Python's `help()` (e.g. `help(kis.domestic.stock)`).

## 8. License

[MIT](LICENSE)
