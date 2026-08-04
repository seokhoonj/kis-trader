# kis-openapi

A clean Python client for the Korea Investment & Securities (KIS) Open API.

```python
from kis_openapi import ...  # design in progress
```

**Audience: Korean KIS users.** Public **identifiers** use industry-standard English
terms (as used across international market-data and brokerage APIs); the explanatory
**docstrings are written in Korean** for the audience, and carry the underlying KIS URLs
and TR-ids. (This audience choice is deliberate — see the source's docstring language.)

Design goals: standard English identifiers, semantic grouping of related vendor endpoints
into single clean functions (with the underlying KIS URLs in each docstring), market data
including analyst opinions/estimates where KIS provides them, domestic and overseas stock
orders and balances across account types, and an order path built to a safety standard
(client-side idempotency, no write-retries, conservative reconciliation).

## Market-wide rankings — `kis.ranking`

`kis.ranking.*` returns market-wide rankings (top movers, most traded, and so on) as
lists of typed rows. Coverage of the KIS ranking endpoints:

| Ranking | Verb | Done |
|---|---|:---:|
| Price change (등락률) | `by_change` | ✅ |
| Volume (거래량) | `by_volume` | ✅ |
| Market cap (시가총액) | `by_market_cap` | ✅ |
| Disparity (이격도) | `by_disparity` | ✅ |
| Quote balance (호가잔량) | `by_quote_balance` | ✅ |
| Execution strength (체결강도) | `by_volume_power` | ✅ |
| Bulk-trade count (대량체결건수) | `by_bulk_trades` | ✅ |
| Watchlist registrations (관심종목 등록상위) | `by_interest` | ✅ |
| Preferred-vs-common disparity (우선주 괴리율) | `by_preferred_disparity` | ✅ |
| Financial ratios (재무비율) | `by_finance_ratio` | ✅ |
| Valuation multiples (시장가치) | `by_valuation` | ✅ |
| Profit & asset figures (수익자산지표) | `by_profit_asset` | ✅ |
| Firm's own trading (당사매매종목) | `by_company_trades` | ✅ |
| Dividend rate (배당률) | `by_dividend` | ✅ |
| Short selling (공매도) | `by_short_sale` | ✅ |
| Credit balance (신용잔고) | | |
| Near 52-week high/low (신고신저근접) | | |
| Expected-open change (예상체결 등락) | | |
| After-hours quote balance (시간외잔량) | | |
| After-hours change (시간외 등락) | | |
| After-hours volume (시간외 거래량) | | |
| After-hours expected change (시간외 예상체결) | | |
| Most-viewed on HTS (HTS 조회상위) | | |
