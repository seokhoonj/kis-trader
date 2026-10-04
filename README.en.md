# kis-trader

[![check](https://github.com/seokhoonj/kis-trader/actions/workflows/check.yml/badge.svg)](https://github.com/seokhoonj/kis-trader/actions/workflows/check.yml)
[![PyPI](https://img.shields.io/pypi/v/kis-trader)](https://pypi.org/project/kis-trader/)
[![Python](https://img.shields.io/pypi/pyversions/kis-trader)](https://pypi.org/project/kis-trader/)
[![License](https://img.shields.io/pypi/l/kis-trader)](https://github.com/seokhoonj/kis-trader/blob/main/LICENSE)

[한국어](README.md) | **English**

An unofficial Python client for the Korea Investment & Securities (KIS) **Open API** — wrapped in a clean, typed API.

- **Broad coverage** — domestic and overseas stocks, indices, ETFs/ETNs, ELWs, futures/options, and bonds: quotes, financials and flows, account balances and P&L, rankings and conditional screens, market data and calendars, retirement pensions.
- **Orders** — buy/sell/modify/cancel, plus credit, reserved, and TWAP-split orders.
- **Safety core** — an idempotent order store (dedup, no double orders), pre-trade risk limits (fat-finger guard), and reconcile (re-check instead of resend) are on by default; credit trading blocked by default.
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

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

kis.domestic.stock("005930").quote()                 # Samsung Electronics quote
kis.overseas.stock("AAPL").quote()                   # Apple (exchange resolved automatically)
kis.domestic.ranking.by_change(direction="gainers")  # today's top gainers
```

Save the app key and account once as environment variables or with `KISConfig(...).save()`, and
later calls open without arguments. Both live and paper-trading accounts are supported, so you can
rehearse the order flow on paper before switching to live. For issuing and storing credentials, see
the [Credentials and profiles](https://seokhoonj.github.io/kis-trader/configuration.html) chapter.

## 3. Structure

Under `KISClient`, asset classes and orders split into namespaces.

```python
kis.domestic   # domestic: stocks, indices, ETFs, ELWs, futures/options, bonds, account, rankings, market, calendar
kis.overseas   # overseas: stocks, indices, futures/options, account, rankings
kis.account    # account queries (dispatched by product code; IRP adds .pension)
kis.orders     # list (open), check, modify, and cancel submitted orders
```

Reach a symbol or contract through a handle, then read quotes and place orders from it.

```python
kis.domestic.stock("005930")     # domestic stock -- quote, chart, order book, buy, sell
kis.overseas.stock("AAPL")       # overseas stock -- exchange resolved automatically
kis.domestic.futures("101W09")   # index futures
kis.domestic.option("201W09")    # index options
```

## 4. Orders

```python
r = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)  # buy 10 shares
kis.orders.reconcile(r.client_order_id)   # re-check with the broker whether it was actually accepted
kis.orders.cancel(r.client_order_id)      # cancel
```

Orders cannot be undone. A duplicate-order safeguard is on by default (credit trading is blocked
by default); the full rules are in the [Orders](https://seokhoonj.github.io/kis-trader/orders.html)
chapter.

## 5. Command line (`kis`)

Installing also adds the terminal command `kis`, so you can query and order without writing
Python. An order is not sent until you add `--execute` — until then it is a dry run.

```bash
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
(`pip install 'kis-trader[mcp]'`, then `kis-mcp`) exposes account-query and order-preview
tools (real-order submission is not exposed). See the
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
| **CLI & agents** | [Command Line](https://seokhoonj.github.io/kis-trader/cli.html) · [Claude Skill](https://seokhoonj.github.io/kis-trader/claude-skill.html) · [Codex Skill](https://seokhoonj.github.io/kis-trader/codex-skill.html) · [MCP Server](https://seokhoonj.github.io/kis-trader/mcp.html) |
| **Reference** | [Realtime (WebSocket)](https://seokhoonj.github.io/kis-trader/realtime.html) · [Limits & gaps](https://seokhoonj.github.io/kis-trader/limits.html) |

The arguments and return value of each method are available through `help(the_method)`.

## 8. License

[MIT](LICENSE)
