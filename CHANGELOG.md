# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.5.0 — Unreleased

Breaking release. The order "venue / board / MIC" concept is split into three explicit
axes and the market-identifier codes (XKRX/XKOS/NXTE/XKFE) are internalized. The on-disk
order store is byte-compatible: schema stays v10 / 19 slots, there is no file migration,
and 0.4.0 and 0.5.0 read each other's stores both ways.

### Changed

- The execution venue is now one `exchange` field, with explicit `region`
  (`"domestic"`/`"overseas"`) and `asset_class` (`"stock"`/`"derivative"`/`"bond"`) beside
  it. The domestic order vocabulary is `DomesticOrderExchange` = `KRX`/`NXT`/`SOR`; the
  domestic quote/handle/realtime vocabulary is `DomesticQuoteExchange` = `KRX`/`NXT`/`UN`.
  `UN` (quote/handle, "통합호가") and `SOR` (order, smart routing) name the same unified
  destination; a `DomesticStock` handle translates `UN` to `SOR` when it builds an order,
  and constructing an order with `exchange="UN"` is rejected. `KRX` is the regular
  exchange, `NXT` the alternative exchange (ATS).
- `DomesticStock.market` → `DomesticStock.exchange`; `StockStatus.market` →
  `StockStatus.exchange`.
- `resolve_market()` → `resolve_domestic_quote_exchange()`.
- Realtime: domestic `.trades(venue=...)` / `.order_book(venue=...)` → `exchange=...`
  (`DomesticQuoteExchange`; the unified value `"unified"` is now `"UN"`); overseas
  `venue=...` → `zone=...` (`"global"`/`"asia"`).
- CLI: `--venue` → `--region`. `--exchange` is registered per-subcommand — order
  subcommands accept `KRX`/`NXT`/`SOR`, quote subcommands `KRX`/`NXT`/`UN`, overseas codes
  stay free-form — and the domestic `--exchange` now reaches every stock subcommand
  (including `status`) and is echoed in the dry-run order ticket. Division help now cites
  `--exchange KRX`/`--exchange NXT` instead of the removed `--board`.
- MCP: the order and quote tools take `region` (was `venue` / `market`); the order ticket
  serializes `region`.
- `RankedOverseasStock` renamed to `OverseasRankedStock` — the sole overseas result type
  that did not lead with the `Overseas` qualifier its ~40 siblings share.
- `TransportTimeout` renamed to `TransportTimeoutError` — matches the package's exception
  naming (every other error ends in `Error`, e.g. the sibling `OrderTimeoutError`) and the
  Python convention (`TimeoutError`).
- Calendar/listing entities' `list_date` field renamed to `listing_date`, matching the
  `listing_date` every other listing-bearing entity already uses.
- `DerivativePosition.side` / `DerivativeValuationPosition.side` are now normalized to
  `Side | None` (`"buy"`/`"sell"`), matching every other `side` in the package, instead of
  the raw localized `"매수"`/`"매도"` string (which remains available in `_raw`).

- `kis.account.pension.orders(only_unfilled=...)` renamed to `unfilled_only=...`, matching
  the `unfilled_only` every other order/fill query uses.
- `RawResponse.body` is now deeply immutable: nested arrays are exposed as tuples and
  nested objects as read-only mappings, where before only the top-level mapping was
  read-only (its nested lists and dicts were still mutable in place). Scalar values and key
  lookup are unchanged — `resp.body["output"]["x"]` reads the same value — but the nested
  container types change: an array block is now a `tuple` (not `list`) and an object block a
  read-only mapping. Two consequences for code that only reads the body: `resp.body` no
  longer compares equal to the original list/dict-shaped JSON payload, and
  `json.dumps(resp.body["output"])` on a nested block raises (a mapping proxy is not
  JSON-serializable) — normalize with `dict()` / a recursive converter first. Code that
  mutated a nested block in place, or asserted a block is exactly a `list`/`dict`, must
  adjust (the parse layer already accepts both).

- `kis.orders.open()` renamed to `kis.orders.open_orders()`, matching the `open_orders()`
  every account view already uses (`account.domestic.open_orders()`, the overseas and bond
  siblings) and the `open_orders` convention across broker APIs.
- `kis.domestic.search_stocks()` (was `kis.domestic.search()`), matching the overseas
  `kis.overseas.search_stocks()`; `symbol` search is one verb across both regions. The MCP
  tool keeps its `search` name for agent-config stability.
- Realtime `OverseasFuturesHandle` renamed to `OverseasDerivativeHandle`: a single handle
  serves both `realtime().overseas.futures(...)` and `.option(...)` (they share the same
  wire TRs), so it is named for the asset class — matching the REST `OverseasDerivative*`
  vocabulary — rather than for one of the two instruments it carries.

- Overseas ranking directions are now typed, matching the domestic ranking. The bare
  `top: str` parameter is renamed to `direction` with a `Literal`: `ranking.by_change(...)`
  takes `direction: Direction` (`"gainers"`/`"losers"`, the same `Direction` the domestic
  ranking already uses), and `ranking.by_price_fluctuation(...)` takes
  `direction: FluctuationDirection` (`"risers"`/`"fallers"`). The shared `Direction` literal
  now lives in one place and `FluctuationDirection` is exported alongside it.

- Realtime execution-notice entities now use the same `filled_*` / `order_id` field names as
  the REST order surface, instead of three different spellings. Across `StockExecutionNotice`,
  `OverseasExecutionNotice`, `FuturesOrderNotice`, `FuturesExecutionNotice` and
  `DerivativeExecutionNotice`: `account_no` →
  `account_number`, `order_no` / `order_number` → `order_id`, `original_order_no` /
  `original_order_number` → `original_order_id`, `executed_qty` / `executed_quantity` →
  `filled_quantity`, `total_executed_quantity` → `total_filled_quantity`, `executed_price` →
  `filled_price`, `total_executed_price` → `total_filled_price`, `executed_amount` →
  `filled_amount`, `executed_date` → `filled_date`, `refused` → `rejected`, `branch_no` →
  `branch_number` (matching the REST `branch_number`), and `execution_no` → `fill_number`
  (matching the REST `OverseasDerivativeFill.fill_number`).
  `DerivativeExecutionNotice` already used `filled_quantity` / `filled_price`; its remaining
  `order_number` / `original_order_number` are renamed to `order_id` / `original_order_id` here
  with the rest. The raw vendor keys are unchanged and still available in `_raw`.
- The same execution-notice entities also unify their remaining per-notice fields, which had
  been modelled three different ways for the same wire fields: `order_qty` → `order_quantity`
  and `sell_buy_class` → `sell_buy` (the lone domestic-stock outliers), and the two status
  flags are now one name and one type across all notices — `CNTG_YN` is `fill_status: str`
  (was `conclusion_flag` / `fill_status` / a lossy `executed: bool`) and `ACPT_YN` is
  `accepted_status: str` (was `accepted_flag` / a lossy `accepted: bool`). Both keep the raw
  ledger code (`"1"`/`"2"`/`"3"`), so the order-vs-fill and received/confirmed/cancelled
  distinctions the booleans discarded are preserved; a prior `if notice.executed:` becomes
  `if notice.fill_status == "2":`.

- `Index.daily_history()` renamed to `Index.recent_prices()`, with its result types
  `IndexDailyHistory` → `RecentIndexPrices` and `IndexDailyPoint` → `RecentIndexPoint`. The
  method takes `interval` 1d/1wk/1mo (not only daily) and returns a snapshot plus the index's
  recent day/week/month values (level, OHLC, change, volume) with the sentiment and
  20-day-disparity indicators — the index twin of the stock `recent_prices()`. The old name
  mislabelled the weekly/monthly capability as "daily" and read ambiguously against the
  existing OHLC `bars()`.

- `OverseasAlgoExecution.executed_amount` renamed to `amount`, matching the record's bare
  `quantity` / `price` — it is a single-execution record, so the `executed_` prefix was
  redundant (the whole entity is one execution). The timestamp field keeps its idiomatic
  `executed_at` name.

- The bond instrument identifier is renamed `code` → `symbol` across the bond handle
  (`kis.domestic.bond(...)`), the result entities (`BondQuote`, `BondProfile`, `BondIssuance`,
  `BondDailyPrice`, `BondValuation`), `kis.account.domestic.bonds.buyable(...)`, and the
  realtime `rt.domestic.bond(...)` handle — so a bond is identified by the same `symbol` as
  every other tradable instrument and as `Order.symbol`, instead of a bond-only `code`. The
  value is unchanged (the bond's ISIN / 표준코드); the bond-position/order entities already used
  `symbol`. The realtime `bond_index(...)` handle keeps `code` (a market index, not a tradable
  bond).

- Several closed-choice parameters that were bare `str` are now typed, matching the sibling
  that already types them: `account.domestic.derivatives.open_orders(side=...)` is `SideFilter`
  (like every other `side` filter), the domestic credit-buyable `credit_type` is `CreditType`,
  `overseas.ranking.by_new_highlow(extreme=...)` is `Literal["high","low"]`, and the MCP
  `place_order` (`region`/`side`) and `ranking_change` (`direction`) tools type their closed
  choices (like the `quote` tool). Runtime behavior is unchanged — the same values are accepted;
  the annotations just let the type checker reject an invalid literal.
- `calendar.dividends(dividend_kind=...)` renamed to `settlement: DividendSettlement` — the
  filter selects 결산/중간 (final/interim), which is a different axis from the cash/stock
  "dividend kind", and it now reuses the `DividendSettlement` literal the dividend ranking
  already uses. The overseas-derivative `OverseasDerivativePosition.exercise_reserved` and
  `OverseasDerivativeMargin.net_risk_applied` fields are now `bool` (were the raw `"Y"`/`"N"`
  string).

- Consistency renames of lone-outlier fields: `RealizedProfitBalance.total_value` →
  `total_evaluation` (every other class maps `tot_evlu_amt` that way);
  `OverseasCurrentPrice.traded_amount` → `trading_amount` (the sibling ranked-stock spelling);
  `OverseasPeriodProfitRow.return_rate` / `OverseasPeriodProfit.total_return_rate` →
  `return_percent` / `total_return_percent` (matching `OverseasBalance` and the domestic trade
  profit). The realtime/overseas `OverseasDerivative` handle attribute `market` → `product`
  (it holds `"future"`/`"option"`, a product kind, not a board/market), and
  `overseas.index(kind=...)` is typed `Literal["index","fx","bond","gold"]`.

- `market.broker_opinions(broker=...)` renamed to `member_code` — the input is a member-firm
  code (the sibling broker fetchers already call it `member_code`), whereas the result field
  `BrokerOpinion.broker` keeps its name (it holds the firm's name). `market.investor_net_buy_stocks(direction=...)`
  renamed to `side: TradeSide` (reusing the dividend/bulk-trade `TradeSide`), dropping a local
  duplicate `TradeDirection` literal.

### Added

- Stock handles (`DomesticStock`/`OverseasStock`) now have a readable `__repr__`
  (`DomesticStock(symbol='005930', exchange='KRX')`).

### Removed

- `DomesticBoard`, `StockVenue`, `OverseasVenue`, `resolve_market`, and the public
  market-identifier on `Order` (now derived from `region` + `asset_class` inside the
  engine and fingerprint codec). `OverseasZone` is added for the overseas realtime feed.

### Unchanged (noted to be explicit)

- `Quote.market` and `OrderBook.market` keep the name `market`: they are shared snapshot
  types whose value spans asset classes (a stock exchange, a derivative market, a bond
  market division), so the generic `market` fits them, while the precise `exchange` name
  is reserved for stock-specific handles and the order layer.

## 0.4.0 — 2026-10-05

### Added

- `AnalystOpinion.broker`: the member-company (brokerage) name that issued the
  opinion (raw `mbcr_name`). Lets a consumer tell apart several brokerages' opinions
  on the same day. Verified against a live `invest-opinion` response.
- `kis.account.product_code`: the raw account product code (`ACNT_PRDT_CD`) on the
  three account views (stock, domestic derivatives, overseas derivatives), beside
  `kind`. Distinguishes IRP (29) from a general account (01) without probing the
  `.pension` lens. Raises `KISUsageError` when no account is set.

### Changed

- `analyst_opinions()` now collects a wide date range in full. The `invest-opinion`
  endpoint has no continuation key and caps one response at the newest 100 rows, so
  a multi-year request previously lost the older rows silently; the fetch now walks
  the window backward until it reaches the requested start. Incremental queries
  under the cap still make a single call.
- `AnalystOpinion` gained `broker` in its field order (after `timestamp`), so
  positional construction of this result object shifts by one. It is built by
  keyword internally and consumed by attribute access, so this affects only code
  that constructed the object positionally.

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
