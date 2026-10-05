# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.3.0 — 2026-10-05

All changes are additive; no existing name, signature, or behavior was removed or
changed.

### Added

- `kis config`: an interactive command that stores a profile's credentials via
  hidden prompts (the first-time-setup counterpart to `KISConfig(...).save()`).
  App key and secret are read only through `getpass` — never as flags — and are
  never echoed; it confirms before overwriting a profile and requires an explicit
  affirmation to write a `real` profile.
- `account.domestic.fills(older_than_three_months=...)` and
  `kis account fills --older-than-three-months`: fetch the daily order/fill
  history older than three months (the before-period TR on the same endpoint).
  The two windows are mutually exclusive, so a call returns one side.
- `MarketInvestorFlow.participants`: net quantity and amount for all fifteen
  investor subjects (securities, investment trust, fund, other corporation, and
  the rest). The existing `foreign_net` / `individual_net` /
  `institutional_net` fields are unchanged.

### Fixed

- Corrected the documented units on the investor-flow entities (documentation
  only — no values change). Amounts are 백만원 (million won) across all of them
  (`InvestorActivity` previously said 원). Trade quantities are 주 for per-stock
  views (`InvestorFlow`, `DetailedInvestorFlow`, `InvestorNetBuyStock`) and 천주
  for market-wide views (`MarketInvestorFlow`, `MarketInvestorSnapshot`); the
  shared value types defer the quantity unit to their container. Verified against
  live responses.

## 0.2.0 — 2026-10-02

Public naming consistency pass. 0.1.0 is yanked; use 0.2.0.

### Changed (breaking)

- After-hours single-price rankings now use the `after_hours` name, matching the
  stock handle (`stock.after_hours_*`): `ranking.by_overtime_change` /
  `by_overtime_volume` / `by_overtime_expected_change` →
  `by_after_hours_change` / `by_after_hours_volume` / `by_after_hours_expected_change`,
  and the result type `OvertimeRanking` → `AfterHoursRanking` (its `overtime_*`
  fields → `after_hours_*`).
- `DividendMarket` values are uppercase (`"KOSPI"` / `"KOSPI200"` / `"KOSDAQ"`),
  matching every other market filter.
- `StockStatus.is_in_liquidation` → `is_under_liquidation_trading` (정리매매 is the
  delisting liquidation-trading session, parallel to `is_under_administration`).
- ELW `ranking.quick_change` → `by_quick_change`; ELW volume-sort values use the
  domestic ranking terms (`trading_volume` / `cumulative_trading_amount` /
  `volume_growth` / `turnover`).
- A record's reference date (KIS `bass_dt`, 기준일자) is named `base_date` on every
  entity that carries it as an attribute (`LendableStock`,
  `OverseasDerivativeTransaction`, `OverseasPeriodProfit`, `AccountRight`,
  `OverseasRight`); only `TradingDay`, whose subject is the calendar day, keeps `date`.

## 0.1.0 — 2026-10-02 [YANKED]

First public release.

- Typed Python client for the Korea Investment & Securities (KIS) Open API —
  domestic and overseas quotes, orders, and account views (`kis.domestic`,
  `kis.overseas`, `kis.account`, `kis.orders`).
- Order safety core: an idempotent order store (dedup by client order id and
  fingerprint), fail-closed `RiskLimits`, and a `reconcile` path for orders whose
  outcome is unclear.
- Real-time WebSocket subscriptions for quotes, order books, and fills.
- Optional MCP server (`pip install 'kis-trader[mcp]'`, console script `kis-mcp`)
  exposing the safe read surface plus a dry-run order preview and reconcile.
- English public identifiers with Korean docstrings; ships `py.typed`.
