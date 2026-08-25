# kis-trader

[![check](https://github.com/seokhoonj/kis-trader/actions/workflows/check.yml/badge.svg)](https://github.com/seokhoonj/kis-trader/actions/workflows/check.yml)
[![PyPI](https://img.shields.io/pypi/v/kis-trader)](https://pypi.org/project/kis-trader/)
[![Python](https://img.shields.io/pypi/pyversions/kis-trader)](https://pypi.org/project/kis-trader/)
[![License](https://img.shields.io/pypi/l/kis-trader)](https://github.com/seokhoonj/kis-trader/blob/main/LICENSE)

[한국어](README.md) | **English**

An unofficial Python client for the Korea Investment & Securities (KIS) **Open API**.

Domestic and overseas stocks and indices, ETFs and ETNs, ELWs, futures and options, and bonds —
their quotes, financials and flows, account balances and profit, rankings and conditional
screens, market data and calendars, retirement pensions, and buy/sell/modify/cancel orders. Live
quotes and execution notices (WebSocket) and a terminal command `kis` come with it.

## 1. Install

Not on PyPI yet. Install from the folder you received.

```bash
uv pip install -e .
```

Requires Python 3.11+. Once published, install with `pip install kis-trader`.

## 2. Quickstart

```python
from kis_trader import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

kis.domestic.stock("005930").quote()                 # Samsung Electronics quote
kis.overseas.stock("AAPL").quote()                   # Apple (exchange resolved automatically)
kis.domestic.ranking.by_change(direction="gainers")  # today's top gainers
```

Save the app key and account once as environment variables or with `KISConfig(...).save()`, and
later calls open without arguments. For issuing and storing credentials, see the
[Credentials and profiles](https://seokhoonj.github.io/kis-trader/configuration.html) chapter.

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
Python. An order is not actually sent until you add `--execute` — until then it is a dry run.

```bash
kis stock quote 005930
kis search 삼성전자
kis ranking change --direction gainers
kis account balance
kis order buy 005930 10 --limit-price 70000                       # dry run, not sent
kis order buy 005930 10 --limit-price 70000 --execute paper --yes  # sent to paper trading
kis account balance --asset bond                                  # list bond lots (buy-date/buy-seq)
kis order buy KR6449111CB8 100 --asset bond --limit-price 10125    # bond buy (dry run)
```

## 6. Use it from an AI coding agent

To manage an account through an AI tool such as Claude Code or Codex, use the
`skills/kis-trader/` skill. See the
[Claude skill](https://seokhoonj.github.io/kis-trader/claude-skill.html) and
[Codex skill](https://seokhoonj.github.io/kis-trader/codex-skill.html) chapters.

## 7. Documentation

The full documentation lives at **<https://seokhoonj.github.io/kis-trader/>** (source: `docs/`).

| Part | Chapters |
|------|----------|
| **Getting started** | [Quickstart](https://seokhoonj.github.io/kis-trader/quickstart.html) · [Issue an app key](https://seokhoonj.github.io/kis-trader/appkey.html) · [Credentials and profiles](https://seokhoonj.github.io/kis-trader/configuration.html) |
| **Domestic stocks** | [Quotes](https://seokhoonj.github.io/kis-trader/quotes.html) · [Financials](https://seokhoonj.github.io/kis-trader/financials.html) · [Flows](https://seokhoonj.github.io/kis-trader/flows.html) · [Account & profit](https://seokhoonj.github.io/kis-trader/account.html) · [Orders](https://seokhoonj.github.io/kis-trader/orders.html) |
| **Market & search** | [Rankings & screens](https://seokhoonj.github.io/kis-trader/screening.html) · [Market & indices](https://seokhoonj.github.io/kis-trader/market.html) · [Corporate actions & calendar](https://seokhoonj.github.io/kis-trader/corporate-actions.html) |
| **Overseas & pension** | [Overseas stocks](https://seokhoonj.github.io/kis-trader/overseas.html) · [Retirement pension](https://seokhoonj.github.io/kis-trader/pension.html) |
| **Other products** | [ETF & ETN](https://seokhoonj.github.io/kis-trader/etf.html) · [ELW](https://seokhoonj.github.io/kis-trader/elw.html) · [Futures & options](https://seokhoonj.github.io/kis-trader/derivatives.html) · [Bonds](https://seokhoonj.github.io/kis-trader/bonds.html) |
| **CLI & agents** | [Command Line](https://seokhoonj.github.io/kis-trader/cli.html) · [Claude Skill](https://seokhoonj.github.io/kis-trader/claude-skill.html) · [Codex Skill](https://seokhoonj.github.io/kis-trader/codex-skill.html) |
| **Reference** | [Realtime (WebSocket)](https://seokhoonj.github.io/kis-trader/realtime.html) · [Limits & gaps](https://seokhoonj.github.io/kis-trader/limits.html) |

The arguments and return value of each method are available through `help(the_method)`.

## 8. License

[MIT](LICENSE) © seokhoonj
