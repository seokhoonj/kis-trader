# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.11.0 (unreleased)

Naming normalization across the public surface plus order-safety hardening from a
full-package review. This is a breaking release (0.x minor).

### Added

- `kis.orders.reconcile(client_order_id, *, refresh=False)` — pass `refresh=True` to
  re-scan the broker instead of returning a stored report. Fill-measuring callers
  (TWAP `shortfall`, the CLI `kis order reconcile`, and the MCP `reconcile` tool) now
  force a refresh; the default stays conservative for timeout recovery.

### Changed (breaking)

- Public naming normalized for consistency/symmetry (ranking methods and entities,
  `stock_fills` → `stock_orders`, trade-profit `rows` → `trades`, overseas screener
  `search_stocks` → `screen_stocks`, reserved-order `sequence` → `reserved_order_id`,
  `Quote.market` → `Quote.exchange` (the field holds a venue code; `OrderBook.market`
  is unchanged), execution-strength unified to `execution_strength` across realtime,
  intraday and ranking surfaces, and related). No deprecation aliases — update call sites.
- `DomesticExecutionStrengthRanking` → `ExecutionStrengthRanking`: domestic ranking
  result entities carry no region prefix (matching `VolumeRanking` / `MarketCapRanking`),
  and there is no dedicated overseas twin to distinguish from (the overseas
  execution-strength ranking returns the generic `OverseasRankedStock`).
- `credentials.json` profile bundles are a closed schema: an unknown field (e.g. a
  typo like `acount`) is now rejected instead of silently ignored.
- Realtime `RealtimeClient.subscribe` / `unsubscribe` take `tr_id` and `tr_key` as
  keyword-only arguments — they were same-typed positional strings that could be
  transposed. Pass them by name (`subscribe(tr_id=..., tr_key=...)`).

### Fixed

- `reconcile` no longer returns a stale place-time snapshot where a fill measurement
  is needed, and matches the daily-execution row by the stored exchange order number
  (not an ambiguous fingerprint), preventing a TWAP double-buy and sibling-order
  misattribution.
- Reserved-order writes convert a throttle (HTTP 500) to a no-resend timeout and clear
  in-flight state on auth failure, matching the overseas discipline (no double
  reservation / phantom in-flight).
- Daytime derivative partial cancel is rejected before the wire instead of silently
  cancelling the full quantity.
- Ranking and overseas-balance parsers fail closed on a non-mapping row instead of
  crashing or silently under-reporting; the order dedup store rejects a current-schema
  file missing its required maps.
- A TWAP slice interrupted mid-placement (Ctrl-C) is recorded as pending for reconcile
  instead of being dropped, so a possibly-live order stays out of `shortfall` and cannot
  be double-ordered; an interrupt landing right after a slice was recorded no longer
  double-counts it.
- The realtime client survives a malformed system frame (a non-mapping body/frame) instead
  of terminating the receive loop, and `stop()` called from a realtime callback thread is
  rejected with a clear error instead of raising a join failure.
- `MarketFunds` money fields are documented as hundred-million won (values unchanged).

## 0.10.0 - 2026-10-15

### Added

- Public `kis_trader.config.list_profiles()` helper for listing the schema-2 credential profiles
  stored across both environments or within one selected environment.
- `kis config list`, which prints stored profile names grouped by `real` and `paper` without
  exposing credential values.
- Read-only MCP `list_profiles` tool, limited to stored profiles supported by `use_profile` and
  annotated with the active profile, environment, and session switching capability.

## 0.9.0 — 2026-10-15

Expose the read-only market data the library already provides through the MCP server and the CLI, so
an agent or the terminal can pull price history, exchange rates, and order-book / trade / status data.

### Added

- MCP read tools: `stock_bars` (domestic and overseas OHLCV), `index_bars` (index / FX / bond / gold
  bars, e.g. `FX@KRW` for USD/KRW exchange-rate history), `order_book`, `trades`, `status`,
  `ranking_volume`, and `ranking_market_cap`.
- CLI `kis index bars` for index / FX / bond / gold bars (stock `bars` already accepted
  `--region overseas`).
- A single-source `OverseasIndexKind` literal (`index` / `fx` / `bond` / `gold`) typing the overseas
  index accessor and feeding the CLI and MCP.
- Docs: a 시장·지수 section documenting overseas index / FX / bond / gold bars.

### Changed

- The MCP OHLCV tool is now `stock_bars` (renamed from `bars` in 0.8.0) so the stock and index bar
  tools read symmetrically; `index_bars` carries `region` and `kind`, and `order_book` / `trades`
  gained a `region` parameter.

## 0.8.0 — 2026-10-14

Adds a credential-config command group, read-only market-data tools over MCP and the CLI, and a
client-setup documentation overhaul.

### Added

- `kis config default` subgroup (`profile` / `environment`, with an `env` alias) mirroring the Python
  `set_default_profile` / `set_default_environment`, and `kis config mcp`, which prints a ready-to-paste
  Claude Desktop configuration block with the `kis-mcp` path filled in, pure-JSON stdout, and a stderr
  warning when the resolved profile/environment combination is not stored.
- Read-only market-data MCP tools: `bars` (OHLCV), `stock_investor_flows`, `market_investor_flows`,
  and `investor_net_buy_stocks`.
- Investor-flow CLI commands: `kis stock investors`, and a `kis market` group with `investors` and
  `net-buy`.

### Changed

- `kis config set-hts-id` is now `kis config hts-id` (with no argument it shows the current value), and
  the former `set-default` command is replaced by the `default` subgroup.
- Documentation: a beginner single-account walkthrough, the three directories the package writes
  (configuration, cache, and the non-regenerable order-deduplication state store), and per-client MCP
  connection guidance for Claude Desktop, Cursor, and ChatGPT (with each client's plan requirements).

## 0.7.0 — 2026-10-14

Breaking redesign of the credential store toward a two-axis model (profile = account type,
environment = a separate axis), plus a naming-consistency and safety pass across the public surface.

### Changed

- **MCP `balance` tool always returns a dict.** The overseas no-market query previously returned a
  bare list; it is now wrapped as `{"by_market": [...]}` so the tool has one response shape
  (the library `account.overseas.balance()` list is unchanged).
- **Overseas reserved-order queries are capped to KIS's 7-day range limit.** KIS rejects an
  `order-resv-list` query spanning 7 days or more, so `kis.account.overseas.reserved_orders(start,
  end)` now refuses a span over 6 days with a clear error (instead of an opaque server failure), and
  reconcile queries a 6-day window anchored on the order's placement date.
- **`credentials.json` schema v2 (breaking).** Credentials now nest by environment then profile
  (`real` / `paper` top-level blocks), so a profile names an account type and `environment`
  selects which slot to open. A `schema: 2` marker distinguishes the format; the runtime reads only
  schema-2, and a pre-0.7.0 flat file must be converted once with `kis config migrate` (see below).
  Under a schema-2 file an omitted environment resolves to
  `paper` (previously a stored per-profile value that defaulted to `real`), and `real` is reached
  only when stated explicitly (call argument, `KIS_DEFAULT_ENVIRONMENT`, or a `default_environment`
  marker written by the new static `KISConfig.set_default_environment`) -- a profile present in one
  environment is never auto-promoted to the other.
- **The runtime now requires the two-axis schema-2 credential format (breaking).** A pre-0.7.0 flat
  `credentials.json` is no longer read at session load; the legacy runtime adapter was removed, so a
  flat file is refused with a clear error naming `kis config migrate` instead of being resolved
  (previously an omitted environment on a flat file silently defaulted to `real`). Convert the file
  once with `kis config migrate`; the converter still reads the old format. An empty or missing
  store is unchanged -- it raises the same kind of clean "no credentials" error as before.
- `resolve_credentials` gains a keyword-only `environment` selector; `KISConfig` writes the nested
  bundle (no `environment` field inside it) and defaults `environment` to `paper`. Environment
  variables are two-axis: `KIS_<PROFILE>_<ENV>_<FIELD>` and override fields of an existing schema-2
  bundle; legacy unprefixed (`KIS_APP_KEY`) and single-axis (`KIS_<NAME>_...`) keys are ignored by
  the runtime, and there is no implicit `main` fallback.
- `KISClient(app_key=..., app_secret=...)` without `environment` now raises instead of defaulting
  to `real`: a directly supplied credential must state its environment so it cannot be aimed at the
  wrong server.
- `kis config` becomes a command group -- `setup` / `show` / `set` / `set-default` / `set-hts-id` /
  `migrate` -- with a global `--environment` selector. `kis config migrate` converts a flat file to
  schema 2, asking before it merges profiles and refusing rather than overwriting on a name
  collision; `kis config migrate --yes` runs non-interactively, auto-accepting the default merge and
  refusing on a collision that would need a rename.
- **`KISConfig.set_default(...)` renamed to `KISConfig.set_default_profile(...)` (breaking).** Writing
  the default profile marker now reads as the profile axis it sets, and a sibling
  `KISConfig.set_default_environment(...)` writes the environment default (the `default_environment`
  marker). The old `set_default` name is removed.
- `RiskLimits` extends to overseas stock orders: a new `max_order_notional_by_currency` map
  caps the per-order notional using each market group's fixed risk-check currency (not the
  symbol's settlement currency; for example `{"USD": 10000, "JPY":
  1500000}`), the price-collar check applies to overseas orders against the exchange's current
  price, and the quantity cap applies everywhere. An overseas order under a risk session without
  a cap for its currency is refused (fail-closed; no FX conversion, and the domestic KRW
  `max_order_notional` never substitutes). Hong Kong orders are capped as HKD regardless of the
  instrument's actual trading counter (a documented limitation for non-HKD HK counters).
- **Swappable same-type arguments are now keyword-only.** Overseas lookups take the primary
  identifier first with the scoping argument keyword-only -- `product_info(symbol, *, exchange)`,
  `industry_stocks(exchange, *, industry_code, ...)`, `collateral_stocks(symbol, *, country, ...)`,
  `corporate_actions(symbol, *, country, ...)` -- mirroring the domestic facade. The MCP order
  kernel's risk-guard booleans are keyword-only and renamed to read like the `KISClient` properties
  they carry (`has_risk_limits`, `has_domestic_risk_cap`).
- **Public naming unified.** `TradeSide` is removed (use `Side`, which already annotates observed
  fills and positions); the domestic derivative P/L methods are `valuation_pnl()` /
  `settlement_pnl()` (matching the package-wide `pnl` term); the bond sell-lot selector is
  `buy_sequence` end to end (`--buy-sequence`, `bond.sell(buy_sequence=...)`,
  `Order.bond_buy_sequence`) to match `BondPosition.buy_sequence`.
- **Bare `date` names now carry their reference point.** `BrokerOpinion.trading_date`,
  `MarketFunds.trading_date`, `TradingDay.calendar_date`, `WatchlistGroup.base_date`; and the
  facade filters `MarketQueries.news(on_date=...)` and `ELWScreenerQueries.newly_listed(base_date=...)`.

### Added

- MCP server: runtime account switching (`use_environment`, `use_profile`, `current_account`) so the
  active account changes within a chat session without a restart; account-type-aware reads
  (`balance`/`positions` take `region`, and `market` for overseas; the pension lens is for IRP, while general/ISA/pension-savings read via the domestic-stock handlers)
  and `quote(session="after_hours")` for 시간외; and overseas stock orders (`place_order`/
  `order_preview` take `region` and `exchange`, with per-currency caps from
  `KIS_MCP_MAX_ORDER_NOTIONAL_<CCY>`). Switching the active environment changes only the read/order
  context -- a real order still requires the startup `KIS_MCP_ALLOW_REAL` + `KIS_MCP_REAL_CONFIRM`
  gate, RiskLimits, the allowlist, and human confirmation. Domestic real orders are allowed on
  orderable stock accounts (general/ISA product 01, pension-savings 22); overseas orders are
  general-account-only.

### Fixed

- **Product info no longer crashes on the KIS "no date" sentinel.** `fetch_product_info` mapped the
  `"00000000"` sentinel (returned for a product not on sale) through a strict parser and raised,
  failing the whole lookup; optional dates now degrade to `None`.
- **One undated market-funds row no longer fails the whole history.** `MarketFunds.trading_date` is
  parsed fail-soft, matching its `date | None` contract.
- **Realtime reconnect survives a send failure on the reopened socket.** A re-registration send that
  hit an accept-then-close previously propagated out of the receive loop, killed the background
  thread, and silently voided `reconnect=True`; it now backs off and retries.
- **`kis-mcp` without the `mcp` extra fails gracefully.** A bare `pip install kis-trader` install
  running `kis-mcp` now prints a `pip install 'kis-trader[mcp]'` hint and exits instead of crashing
  with a raw `ModuleNotFoundError`.

### Notes

- On a schema-2 file an omitted environment runs at the paper rate limit (1 request/second) until
  `real` is selected.
- The MCP `balance` tool returns the domestic stock balance; the unified balance (domestic + bonds +
  overseas) stays on the Python API `kis.account.balance()`.

## 0.6.0 — 2026-10-12

Additive release. The domestic account DATA records expose the full non-sensitive field set
of their KIS responses, so reading the account surface no longer needs `_raw`. Operator PII
(contact phone, request IP, employee number) and the computer-order notification metadata
(channel / medium / receipt codes, notification time) are deliberately never promoted and
stay in `_raw`.

### Added

- `DomesticBalance`: `securities_evaluation` (유가증권평가금액), `total_loan_amount`,
  `previous_day_total_asset`, `asset_change_amount` / `asset_change_percent` (전일 대비 총자산 증감,
  부호 포함), `cma_evaluation`, `previous_day_buy_amount` / `today_buy_amount` /
  `previous_day_sell_amount` / `today_sell_amount`, `previous_day_expenses` / `today_expenses`,
  `d2_auto_redemption_amount` / `next_day_auto_redemption_amount`, `total_short_sell_amount`,
  and the `financing_auto_redemption_enabled` flag. Absent fields fold to `0` (tolerant parsing), so
  account-type variants that omit a field do not fail.
- `DomesticPosition`: `change` / `change_percent` (전일대비·등락율, 부호 포함),
  `evaluation_return_percent`, `trade_type_name` (현금/신용), `substitute_price`,
  `margin_rate_name` / `guarantee_rate_name`, `stock_loan_unit_price`,
  `previous_day_buy_quantity` / `previous_day_sell_quantity` / `today_buy_quantity` /
  `today_sell_quantity`, `loan_amount` / `short_sell_amount`, and `loan_date` / `expiry_date`
  (`None` for a cash lot with no credit).
- `StockFill`: `canceled_quantity` (취소확인수량), `exchange` typed as
  `DomesticOrderExchange | None` (KRX/NXT/SOR; `None` for a pre-NXT order or a venue code the
  package does not yet model — the raw code stays in `_raw` and a warning is logged, so one
  unknown code never voids a whole fills read), `exchange_division_code` (raw KIS code),
  `fill_condition_name` (IOC/FOK/없음), `order_type_code`,
  `product_type_code`, `order_organization_number`, `reservation_end_date`, `loan_date`, and a
  `stop: StopTrigger | None` sub-record (`trigger_price` + `effective_datetime`) that is `None`
  for a non-stop order. New public type `StopTrigger`.

## 0.5.0 — 2026-10-11

Breaking release. The order "venue / board / MIC" concept is split into three explicit
axes and the market-identifier codes (XKRX/XKOS/NXTE/XKFE) are internalized. The on-disk
order store is byte-compatible: schema stays v10 / 19 slots, there is no file migration,
and 0.4.0 and 0.5.0 read each other's stores both ways.

### Changed

- `account.overseas.present_balance(inquiry=...)` and `settlement_balance(inquiry=...)` now take a
  typed `BalanceInquiry = Literal["all","general","ministock"]` instead of a bare string, mapped by
  the engine to the wire `INQR_DVSN_CD` (`00`/`01`/`02` = 전체/일반해외주식/미니스탁) and fail-closed
  on an unknown value, matching the `nation` parameter's shape. Both endpoints share the same KIS
  value set (CTRP6504R / CTRP6010R). Callers passing the raw `"00"`/`"01"`/`"02"` should pass
  `"all"`/`"general"`/`"ministock"`.
- `AfterHoursConclusion` → `AfterHoursExecutionPoint`. It is field-for-field the same polling,
  per-timestamp execution time-series element as `IntradayExecutionPoint` /
  `ExpectedExecutionPoint` (timestamp, price, change, ask/bid, cumulative/tick volume), returned as
  a list by `DomesticStock.after_hours_conclusions()`; the bare `Conclusion` suffix is reserved for
  the realtime expected-conclusion family (`*ExpectedConclusion`), so the realized series now takes
  the `*ExecutionPoint` suffix of its siblings. The `after_hours_conclusions()` method name and the
  wire behavior are unchanged.
- Domestic-market Literal types consolidated. The three identical `["all","KOSPI","KOSDAQ"]`
  filters — `SearchMarket`, `MarketFilter`, `ExpectedMarket` — are now one `DomesticMarketFilter`
  (the all-bearing sibling of `DomesticMarket`, paralleling `SideFilter`/`DerivativeProductFilter`),
  and the duplicate 2-value `Market` is now `DomesticMarket` (paralleling `DomesticOrderExchange`/
  `DomesticQuoteExchange`). Distinct-valued neighbours stay separate: `CreditMarketFilter`
  (+`KOSPI200`), `DividendMarket`, `ExpectedCloseMarket` (+`KRX100`) each back a different endpoint
  whose accepted set differs, so a shared name would wrongly imply interchangeability. The one of
  these that was root-importable, `kis_trader.SearchMarket`, is now `kis_trader.DomesticMarketFilter`;
  the module-level `MarketFilter` / `ExpectedMarket` / `Market` become `DomesticMarketFilter` /
  `DomesticMarket`. Values and wire behavior are unchanged.
- The execution venue is now one `exchange` field, with explicit `region`
  (`"domestic"`/`"overseas"`) and `asset_class` (`"stock"`/`"derivative"`/`"bond"`) beside
  it. The domestic order vocabulary is `DomesticOrderExchange` = `KRX`/`NXT`/`SOR`; the
  domestic quote/handle/realtime vocabulary is `DomesticQuoteExchange` = `KRX`/`NXT`/`UN`.
  `UN` (quote/handle, "통합호가") and `SOR` (order, smart routing) name the same unified
  destination; a `DomesticStock` handle translates `UN` to `SOR` when it builds an order,
  and constructing an order with `exchange="UN"` is rejected. `KRX` is the regular
  exchange, `NXT` the alternative exchange (ATS).

  Migrating a direct `Order(...)` (or `Order.limit/market/stop/stop_limit`): 0.4.0 took a MIC
  `exchange=` (default `"XKRX"`) plus a `board=` field/kwarg, both removed in 0.5.0. Map them to
  the three new fields `region` / `asset_class` / `exchange`:
  - `exchange="XKRX"`/`"XKOS"`/`"NXTE"` + `board="KRX"`/`"NXT"`/`"UN"` → `region="domestic"`,
    `asset_class="stock"`, `exchange="KRX"`/`"NXT"`/`"SOR"` (`board="UN"` → `exchange="SOR"`);
  - `exchange="XKFE"` → `region="domestic"`, `asset_class="derivative"`;
  - `exchange="BOND"` → `region="domestic"`, `asset_class="bond"`;
  - `exchange="OSFO"` → `region="overseas"`, `asset_class="derivative"` (these three keep
    `exchange` at its `"KRX"` default); overseas-stock codes keep `exchange` with
    `region="overseas"`, `asset_class="stock"`.
  The `Order.board` field and the `board=` constructor parameter are removed.
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
- The overseas realtime entities that the `Overseas*` sweep had missed are renamed to match
  their siblings (and the `OverseasDerivativeHandle` the 0.5.0 handle rename produced): the
  해외선물옵션 feed types `FuturesTradeTick` / `FuturesOrderBook` / `FuturesOrderNotice` /
  `FuturesExecutionNotice` → `OverseasDerivativeTradeTick` / `OverseasDerivativeOrderBook` /
  `OverseasDerivativeOrderNotice` / `OverseasDerivativeExecutionNotice` (they name 해외선물옵션,
  not just futures, and `FuturesTradeTick` was easily confused with the domestic `FuturesTick` in
  `kis_trader.realtime`), and the 해외주식 feed types `DelayedTradeTick` / `AsiaDelayedOrderBook`
  → `OverseasDelayedTradeTick` / `OverseasAsiaDelayedOrderBook` (their file-siblings
  `OverseasOrderBook` / `OverseasExecutionNotice` already carry the qualifier). Fields and wire
  behavior are unchanged — class names only.
- `StockStatus.exchange` is now typed `DomesticQuoteExchange` (`"KRX"`/`"NXT"`/`"UN"`) instead of
  bare `str`, matching the `DomesticStock.exchange` the engine feeds it (the value was always the
  typed quote-exchange; only the annotation narrowed). Wire behavior unchanged.
- The Domestic-qualifier sweep is completed: the remaining domestic entity types
  that have a distinct `Overseas*` sibling (or ride the `OverseasDerivative*` family mirror) are
  prefixed `Domestic`. Renamed: `Position` → `DomesticPosition`, `ProductInfo` →
  `DomesticProductInfo`, `RankedStock` → `DomesticRankedStock`, `DerivativeQuote` →
  `DomesticDerivativeQuote`, and the fifteen domestic derivative-account entities
  `DerivativePosition`/`DerivativeBalance`/`DerivativeValuationPosition`/`DerivativeValuationBalance`/
  `DerivativeSettlementPosition`/`DerivativeSettlementBalance`/`DerivativeFill`/`DerivativeFillHistory`/
  `DerivativeCommission`/`DerivativeCommissionHistory`/`DerivativeOrderable`/`DerivativeDeposit`/
  `DerivativeNightBalance`/`DerivativeNightMargin`/`DerivativeOpenOrder` → `DomesticDerivative*`.
  `DerivativeMarginRate` stays bare (it is in the quote module with no exact overseas twin), as do
  the realtime `DerivativeOrderBook`/`DerivativeExecutionNotice` and the aggregate `Balance`.
  Fields and behavior are unchanged — class names only.
- The four domestic result/handle types that already had an `Overseas*` sibling but lacked
  the region qualifier are renamed with a `Domestic` prefix: `OpenOrder` →
  `DomesticOpenOrder`, `ReservedOrder` → `DomesticReservedOrder`, `BuyableAmount` →
  `DomesticBuyableAmount`, and the `Index` query handle → `DomesticIndex`. Each is
  domestic-only (distinct from its `Overseas*` sibling), so the bare name misread as a
  region-neutral type; the prefix matches the `DomesticBalance` / `OverseasBalance`
  convention (the cross-domain aggregate `OpenOrders` stays bare). Fields and behavior are
  unchanged — only the class names.
- The tradable-instrument identifier is now `symbol` everywhere, not a mix of `symbol` and
  `code`. Previously only stocks, bonds, and overseas-derivative entities used `symbol`
  while domestic-derivative and ELW used `code` and the overseas-derivative factory used
  `series_code`. Now renamed to `symbol`: the domestic-derivative entities
  (`DomesticDerivativeQuote`, `ExpectedExecutionTrend`, `FuturesBoardQuote`, `OptionBoardRow`) and
  the ELW entities (`ELWQuote`, `ELWSensitivityPoint`, `ELWVolatilityPoint`,
  `ELWIndicatorPoint`, `ELWLPFlow`) `.code` → `.symbol`; the futures/option/elw contract
  handles' attribute and constructor; and the `code`/`series_code` parameter of every
  instrument factory — `kis.domestic.stock/futures/option/elw(...)`,
  `kis.overseas.futures/option(...)`, and their realtime counterparts — is now `symbol`.
  Classifiers that are not a tradable instrument keep `code`: `OptionExpiry.code`
  (expiry-month), `kis.domestic.index(code)` / realtime `index` / `bond_index`
  (index/sector codes). Wire keys and behavior are unchanged.
- `TransportTimeout` renamed to `TransportTimeoutError` — matches the package's exception
  naming (every other error ends in `Error`, e.g. the sibling `OrderTimeoutError`) and the
  Python convention (`TimeoutError`).
- Calendar/listing entities' `list_date` field renamed to `listing_date`, matching the
  `listing_date` every other listing-bearing entity already uses.
- `DomesticDerivativePosition.side` / `DomesticDerivativeValuationPosition.side` are now normalized to
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
- The realtime domestic handle types are renamed with a `Domestic` prefix to match their
  `Overseas*` siblings and the REST `Domestic*` handles: `StockHandle` →
  `DomesticStockHandle`, `FuturesHandle` → `DomesticFuturesHandle`, `OptionHandle` →
  `DomesticOptionHandle`, `IndexHandle` → `DomesticIndexHandle`, `ELWHandle` →
  `DomesticELWHandle`, `BondHandle` → `DomesticBondHandle`, `BondIndexHandle` →
  `DomesticBondIndexHandle`, and the `ExecutionNotices` group → `DomesticExecutionNotices`.
  All realtime handle types (domestic + overseas) and their closed-choice aliases
  (`FuturesKind`, `OptionKind`, `NoticeSession`, `OverseasZone`, `CustomerType`) are now
  exported from `kis_trader.realtime` — the REST handles were already root-exported, so the
  realtime handles returned by the namespaces are now nameable too.
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
  `OverseasExecutionNotice`, `OverseasDerivativeOrderNotice`, `OverseasDerivativeExecutionNotice` and
  `DerivativeExecutionNotice` (each for the fields it carries): `account_no` →
  `account_number`, `order_no` / `order_number` → `order_id`, `original_order_no` /
  `original_order_number` → `original_order_id`, `executed_qty` / `executed_quantity` →
  `filled_quantity`, `total_executed_quantity` → `total_filled_quantity`, `executed_price` →
  `filled_price`, `total_executed_price` → `average_filled_price` (TOT_CCLD_UV 총체결단가, a
  per-unit weighted-average price, not a summed amount), `executed_amount` →
  `filled_amount`, `executed_date` → `filled_date`, `refused` → `rejected`, `branch_no` →
  `branch_number` (matching the REST `branch_number`; `branch_number` is lifted out only on
  `OverseasExecutionNotice` — the stock/derivative notices keep `BRNC_NO` in `_raw`), and
  `execution_no` → `fill_number` (matching the REST `OverseasDerivativeFill.fill_number`).
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

- `Index.daily_history()` renamed to `DomesticIndex.recent_prices()` (the handle itself is renamed
  `Index` → `DomesticIndex`, see above), with its result types
  `IndexDailyHistory` → `RecentIndexPrices` and `IndexDailyPoint` → `RecentIndexPoint`. The
  method takes `interval` 1d/1wk/1mo (not only daily) and returns a snapshot plus the index's
  recent day/week/month values (level, OHLC, change, volume) with the sentiment and
  20-day-disparity indicators — the index twin of the stock `recent_prices()`. The old name
  mislabelled the weekly/monthly capability as "daily" and read ambiguously against the
  existing OHLC `bars()`.

- `DomesticDerivativeFill.fill_time` is now `time | None` (was a raw `str`) -- the official
  KIS field spec declares `ccld_btwn` as `string`/length 6/HHMMSS '체결시간' (a single fill
  time, not an interval), matching the overseas twin's `executed_at`; it is parsed with the
  shared fail-soft `_parse_hhmmss`.
- `OverseasAlgoExecution.price` / `amount` are now `Money` (were `Decimal`), carrying the
  trade currency — the algo-execution response (inquire-algo-ccnl, confirmed via the official
  KIS repo column map) includes `TR_CRCY` 거래통화, so these monetary fields follow the overseas
  `Money` convention. `OverseasAlgoOrder.order_price` and `OverseasReservedOrder.order_price`
  stay `Decimal` (their responses carry no currency column).
- `OverseasOpenOrder.quantity` / `filled_quantity` / `unfilled_quantity` are now `Decimal`
  (were `int`), matching the sibling `OverseasReservedOrder` / `OverseasAlgoOrder` for the same
  wire keys. The old `int` parse raised on any fractional quantity, so a single pending
  fractional US-share order would fail the whole `kis.account.overseas.open_orders()` fetch;
  fractional quantities now parse correctly.
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
  bond). On the same `kis.account.domestic.bonds.buyable(...)` the limit-price-basis parameter
  is also renamed `price` → `limit_price`, matching the domestic stock and pension `buyable`
  siblings (overseas `buyable(price=...)` is a separate required argument and is unchanged).

- Several closed-choice parameters that were bare `str` are now typed, matching the sibling
  that already types them: `account.domestic.derivatives.open_orders(side=...)` is `SideFilter`
  (like every other `side` filter), the domestic credit-buyable `credit_type` is `CreditType`,
  `overseas.ranking.by_new_highlow(extreme=...)` is `Literal["high","low"]`, and the MCP
  `place_order` (`side`) and `ranking_change` (`direction`) tools type their closed
  choices (like the `quote` tool). Runtime behavior is unchanged — the same values are accepted;
  the annotations just let the type checker reject an invalid literal. The MCP `place_order` tool
  no longer exposes a `region` parameter (it was single-valued `"domestic"`); region is fixed to
  domestic internally, matching its dry-run twin `order_preview`.
- `calendar.dividends(dividend_kind=...)` renamed to `settlement: DividendSettlement` — the
  filter selects 결산/중간 (final/interim), which is a different axis from the cash/stock
  "dividend kind", and it now reuses the `DividendSettlement` literal the dividend ranking
  already uses. The overseas-derivative `OverseasDerivativePosition.exercise_reserved` and
  `OverseasDerivativeMargin.net_risk_applied` fields are now `bool` (were the raw `"Y"`/`"N"`
  string).

- Consistency renames of lone-outlier fields: `RealizedProfitBalance.total_value` →
  `total_evaluation` (every other class maps `tot_evlu_amt` that way);
  `OverseasCurrentPrice.traded_amount` → `trading_amount` (the sibling ranked-stock spelling),
  and `OverseasStockSearchMatch.amount` → `trading_amount` (the same `avol` 거래대금, now decodable
  and consistent with the ranked-stock/current-price siblings);
  `OverseasPeriodProfitRow.return_rate` / `OverseasPeriodProfit.total_return_rate` →
  `return_percent` / `total_return_percent` (matching `OverseasBalance` and the domestic trade
  profit). The realtime/overseas `OverseasDerivative` handle attribute `market` → `product`
  (it holds `"future"`/`"option"`, a product kind, not a board/market), and
  `overseas.index(kind=...)` is typed `Literal["index","fx","bond","gold"]`.
- More lone-outlier field renames: `BrokerActivity.member_number` → `member_code` (the
  sibling `BrokerDailyActivity` already uses `member_code`); `OverseasProductInfo.country` →
  `country_name` (the `natn_name` → `country_name` convention the other overseas entities
  follow); `OverseasDerivativeDetail.tradable` → `trade_status` (it holds the `stat_tp` status
  code, not a bool, and `tradable` collided with the sibling `is_tradable: bool`); and the
  realtime `StockOrderBook.expected_qty` → `expected_volume` (the `ANTC_CNQN` spelling every
  other realtime entity uses). The `reference.rights(date_basis=...)` /
  `reference.collateral_stocks(sort=...)` closed-choice parameters are now typed `Literal`
  (behavior unchanged).
- Display-name fields that carry a raw `*_name` key now take the `_name` suffix the sibling
  fields already use (so the name reads as a display string, not a code): `trade_type` →
  `trade_type_name` on `RealizedProfitPosition`, `DomesticDerivativeSettlementPosition`,
  `TradeProfit`, and `OverseasAlgoOrder`; `IntegratedMargin.integrated_margin_type` →
  `integrated_margin_type_name`; and `transaction_type` → `transaction_type_name` on
  `DomesticDerivativeFill` and `OverseasDerivativeTransaction`. The bond-account
  `BondOpenOrder.revise_cancel_type` → `revise_cancel_type_name` (`rvse_cncl_dvsn_name`, a
  display string) while the overseas twin that holds the raw CODE is renamed
  `OverseasDerivativeDailyOrder.revise_cancel_type` → `revise_cancel_type_code` (so one identifier
  no longer means a name on one side and a code on the other); and `BondOpenOrder.order_division`
  → `order_type_code` (`ord_dvsn_cd`), pairing with the same file's `BondFill.order_type_name`.
- The two bare `date` fields on the overseas derivative account are renamed to the concept
  each carries (both from the `dt` key): `OverseasDerivativeFill.date` → `fill_date` (체결일자)
  and `OverseasDerivativeDailyOrder.date` → `receipt_date` (주문접수일자), so they no longer
  collide with the `order_date` (`ord_dt`) that sits beside them.
- Two pension settlement fields are renamed to match their `DomesticBalance` twins (identical
  wire keys): `next_day_estimated_settlement_amount` → `settlement_cash_d1` (`nxdy_excc_amt`) and
  `prior_settlement` → `settlement_cash_d2` (`prvs_rcdl_excc_amt`), so 정산(excc) cash no longer
  reads differently across modules or collides with the pension 결제(sttl) `*_settlement_amount`
  fields. The overseas search filter parameter `search_stocks(amount=...)` → `trading_amount=...`,
  matching its own result field `OverseasStockSearchMatch.trading_amount`.
- `OverseasDerivative.bars(...)` takes `interval` as the first positional argument (default
  `"1d"`) with `exchange` kept required keyword-only, matching every sibling `bars()` — previously
  `interval` was keyword-only, so `bars("1d", exchange=...)` raised `TypeError`.
- The `product` axis on the overseas-derivatives surface now means one thing everywhere. The
  `derivatives_market_hours(kind=...)` free-form string becomes a validated
  `product: DerivativeProductFilter` (`"all"`/`"future"`/`"option"` → wire `OPT_YN` `%`/`N`/`Y`,
  unknown values rejected), its result field `OverseasDerivativeMarketHours.kind` →
  `futures_option_type_name` (`fuop_dvsn_name`); and `futures_open_interest(product=...)` — whose
  argument is the CFTC product identifier, not the future/option filter — is renamed
  `symbol`, with `OverseasFuturesOpenInterest.product` → `symbol`.
- `option_board_futures(market_class="")` now reaches the KOSPI200 main board (a blank
  `FID_COND_MRKT_CLS_CODE` returns the A01xxx KOSPI200 monthly contracts with no MKI/KQI
  board rows). The engine previously rejected a blank
  `market_class`; it now accepts the documented set `""`/`MKI`/`WKM`/`WKI`/`KQI` and rejects
  anything else.
- Closed-choice params typed: `account.overseas.positions(fuop=...)` → `product`
  typed `DerivativeProductFilter = Literal["all","future","option"]` (engine maps to
  `FUOP_DVSN` 00/01/02 per the official OTFM1412R definition); `account.domestic.night_margin(
  margin_division=...)` typed `NightMarginDivision = Literal["consignment","maintenance"]`
  (engine maps to `MGNA_DVSN_CD` 01/02 = 위탁/유지 per CTFN7107R); the MCP `order_preview` tool
  `side` is now typed `Side` to match `place_order`. Wire codes unchanged.
- `OverseasDerivativeOrder.new_liquidation` → `new_liquidation_type`: the field
  holds the 신규/청산 open-vs-close classifier code (`new_lqd_dvsn_cd`), and the bare name read
  like a boolean; the `_type` suffix matches the sibling `futures_option_type`.
- MCP `place_order` now accepts and honors `division`: `order_preview` advertised
  and echoed a `division` (e.g. `immediate_limit` 최유리지정가) that the send path silently dropped,
  so an approved preview was sent as a plain limit/market order. `division` is now threaded through
  `make_stock_order_plan` → `plan_place_order` → `run_place_order` → `_execute_stock_order` (validated
  against `DomesticDivision`, echoed in the approval ticket), so preview, ticket, and the actual order
  agree.
- `ForeignNetBuyPoint.foreign_net_buy` and the realtime `MemberActivity.foreign_net_buy_volume`
  are renamed `foreign_broker_net_buy_volume` — the `glob_ntby_qty` 외국계(foreign
  member firm) net-buy quantity had three public names; `foreign_broker_net_buy_volume` (already
  on `BrokerTradeTick`) wins, disambiguating 외국계 from 외국인 and carrying the shares unit.
  `foreign_net_buy_change` (`frgn_ntby_qty_icdc`) keeps its name. For the same reason the two
  sibling 외국계 fields on `MemberActivity` are renamed `foreign_sell_volume` →
  `foreign_broker_sell_volume` and `foreign_buy_volume` → `foreign_broker_buy_volume`
  (`glob_total_seln_qty` / `glob_total_shnu_qty`), so all three 외국계 fields share the
  `foreign_broker_` prefix and never read as 외국인(frgn_) investor figures.
- Value-kind suffix normalization. Field suffixes now follow one rule by financial concept
  (not by whether the value is scaled x100): `_percent` = a change / return / holding-share
  expressed as a headline percentage; `_rate` = a named financial rate (an exchange/price rate,
  or an interest/yield/discount/dividend/margin/divergence rate); `_ratio` = a named financial
  ratio or proportion (debt/current/quick/reserve ratio, a share-of-own-activity, a loan/balance
  or collateral/credit requirement proportion). Five return/holding fields that held a percentage
  move `_rate` → `_percent`: `RealizedProfitBalance`'s `asset_change_rate` /
  `realized_return_rate` / `real_eval_return_rate`, the overseas `OverseasPresentBalance` /
  `OverseasSettlementBalance` `eval_return_rate`, and the ELW `lp_holding_rate` (the two `eval`
  fields are additionally spelled out below, so their final shipped names are
  `real_evaluation_return_percent` and `evaluation_return_percent`). The three overseas-collateral
  requirement proportions move `_rate` →
  `_ratio`: `OverseasCollateralStock.loan_rate` / `maintenance_rate` / `collateral_rate`
  (대출비율/담보유지비율/담보확보비율) → `loan_ratio` / `maintenance_ratio` / `collateral_ratio`
  (a per-stock requirement is a ratio, and `maintenance_ratio` now matches the derivative-account
  `maintenance_ratio`). True rates (`exchange_rate`, `coupon_rate`) are unchanged; wire keys
  unchanged.
- Same-concept suffix unification (one wire concept had two public names). `CreditEligibleStock`'s
  `credit_rate` → `credit_ratio` (the same `crdt_rate` 신용비율 that `StockStatus.credit_ratio`
  already carries). The overseas `OverseasBalance.return_percent` → `total_return_percent` (it
  decodes `tot_pftrt` 총수익률, which is `total_return_percent` on every other entity, while
  `return_percent` is reserved for the non-total `pftrt`). The index 거래량/거래대금 비중 fields
  `RecentIndexPoint.volume_share` and `CategoryIndex.volume_share` / `amount_share` →
  `volume_share_percent` / `amount_share_percent` (the same `acml_vol_rlim` that
  `VolumeAtPrice.volume_share_percent` already carries; a share-of-market is a percentage).
  `CapitalReduction.reduction_rate` → `reduction_ratio` (감자비율, matching the sibling
  corporate-action `MergerSplit.merge_ratio`). Wire keys unchanged.
- Naming unification: `DomesticReservedOrder.received_date` → `receipt_date`
  (the `rsvn_ord_rcit_dt` concept is `receipt_date` on `ExecutionReport`, the store, the
  overseas reserved order, and the CLI); `DomesticPosition.security_name` /
  `SellableQuantity.security_name` / `EarningsEstimate.security_name` and
  `BrokerTradeTick.symbol_name` → `name` (the `prdt_name` / `hts_kor_isnm` family is `name`
  on ~25 other entities); `StockFill.cancelled` → `canceled` (US spelling, matching
  `OrderStatus.CANCELED` and `OverseasReservedOrder.canceled`).
- Closed-choice typing: `TopViewedStock.market` is now `Literal["KOSPI","KOSDAQ"]`
  and the engine decodes the raw `J`/`Q` wire code to `KOSPI`/`KOSDAQ` (fail-closed on an
  unknown code; the raw value stays in `_raw`) instead of surfacing the magic code;
  `OverseasDerivativeOrder.fuop` is renamed `futures_option_type` (the raw `fuop_dvsn` stem
  was not decodable as a field name); and `account.overseas.present_balance(nation=...)` is
  typed `BalanceNation = Literal["all","US","HK","CN","JP","VN"]` (the set the engine already
  enforces). `present_balance`'s `market_code` stays `str` pending a primary-source value set
  (its `inquiry` is now the typed `BalanceInquiry`, per the top of this entry).
- `ProgramTradeActivity` and `BrokerDailyActivity` rename their gross-count fields
  `sell_quantity` / `buy_quantity` → `sell_volume` / `buy_volume`, so all three of
  `buy_volume` / `sell_volume` / `net_buy_volume` use one `volume` vocabulary (matching the
  sibling `InvestorActivity` and the program-trade point records) instead of mixing
  `quantity` for the gross counts with `volume` for the net.

- `market.broker_opinions(broker=...)` renamed to `member_code` — the input is a member-firm
  code (the sibling broker fetchers already call it `member_code`), whereas the result field
  `BrokerOpinion.broker` keeps its name (it holds the firm's name). `market.investor_net_buy_stocks(direction=...)`
  renamed to `side: TradeSide` (reusing the dividend/bulk-trade `TradeSide`), dropping a local
  duplicate `TradeDirection` literal.

- `DomesticDerivativeOpenOrder.order_time` is now `time | None` (was the raw `"HHMMSS"` string),
  parsed like every other `ord_tmd` field. `SavedScreenStock`'s price fields drop their
  redundant `_price`/`price_` affixes to match every other quote record: `current_price` →
  `price`, `price_change` → `change`, `open_price`/`high_price`/`low_price` → `open`/`high`/`low`.
  The realtime `StockTick` entity moved from the lone `realtime/messages.py` module into
  `realtime/parsers/domestic_stock.py` beside its parser (import it from `kis_trader.realtime`
  as before); `realtime/messages.py` is removed.

- The localized order-kind label field (`ord_dvsn_name` / `nmpr_type_name`, e.g. `"지정가"`) is
  renamed `order_type` → `order_type_name` on `DomesticOpenOrder`, `StockFill`, `BondOpenOrder`,
  `DomesticDerivativeOpenOrder` and the pension order entity, matching the sibling `DomesticReservedOrder` and
  no longer colliding with the typed `order_type: OrderType` on the request path (which is
  unchanged).
- The overseas order entities' `quantity` field is renamed `order_quantity` to match the
  `order_quantity` that the domestic, bond, derivative, and realtime order records already use:
  `OverseasOpenOrder`, `OverseasAlgoOrder`, `OverseasReservedOrder`, and the pension order entity.
  `OverseasAlgoExecution.quantity` (a fill record) is renamed `filled_quantity`, since it holds
  the executed quantity, not an order quantity. For the same reason `OverseasTransaction.quantity`
  (a daily-trade record decoding the fill-quantity key `ccld_qty`) is also renamed `filled_quantity`
  — every other field decoding a 체결수량 key (`ccld_qty` / `ft_ccld_qty` / `fm_ccld_qty` /
  `CCLD_QTY`) across the package already uses `filled_quantity`, so the bare name was the lone
  outlier for that concept. Wire keys and behavior are unchanged.
- `OverseasIndex.symbol` → `OverseasIndex.code`, and the `kis.overseas.index(symbol=...)` factory
  parameter → `code`. An index is a non-traded classifier, not a tradable instrument, so it uses
  `code` like `DomesticIndex`/`kis.domestic.index(code)` and the realtime `index`/`bond_index`;
  the handle previously contradicted its own docstring ("``symbol`` 은 지수코드").
- `RankingQueries` → `DomesticRankingQueries`. It has a distinct `OverseasRankingQueries` sibling,
  so the bare name misread as region-neutral; the prefix matches the `Domestic*`/`Overseas*`
  qualifier convention. `kis.domestic.ranking` and behavior are unchanged.
- Eight pure calendar-day fields that were typed `datetime` (a KST-aware midnight) are now
  `date`, matching the ~90 other `*_date` fields and fixing a silent `datetime == date -> False`
  mismatch in set/dedup/join keys: `StockProfile.listing_date`; `ELWListing.listing_date` /
  `.last_trade_date`; `OverseasDerivativeQuote.expiry_date` / `.last_trade_date`; and
  `OverseasDerivativeDetail.listing_date` / `.expiry_date` / `.last_trade_date`. The two local
  datetime-returning `_optional_date` parser copies are removed in favor of the shared fail-closed
  `parse_optional_kst_date` (empty / "00000000" sentinel -> None). Wire keys unchanged.
- `TradingDay.date` is now `date` (was `datetime`, a fabricated KST-aware midnight) -- the same
  calendar-day value-type fix as above, which the trading-calendar entity had been missed from; it
  now matches the sibling `FuturesMarketSchedule.today` and avoids the `datetime == date -> False`
  mismatch in set/dedup/join keys. Wire key (`bass_dt`) unchanged.
- Naming-unify residue cleared. `OverseasBuyableAmount.max_quantity` /
  `integrated_max_quantity` → `max_orderable_quantity` / `integrated_max_orderable_quantity`
  (the wire keys are `max_ord_psbl_qty` = orderable, distinct from the `max_buy_qty` =
  `max_buyable_quantity` that other entities decode). The truncated `eval_` prefix is spelled out:
  `OverseasPresentBalance` / `OverseasSettlementBalance`'s `eval_return_percent` →
  `evaluation_return_percent`, and
  `RealizedProfitBalance.real_eval_pnl` / `real_eval_return_percent` → `real_evaluation_pnl` /
  `real_evaluation_return_percent`. The realtime execution-notice entities' `symbol_name` →
  `name`, matching the symbol+`name` convention of the order/fill record family (the overseas
  news headline keeps `symbol_name`, its `category_name` pairing). `margin_detail(currency=...)`
  is now keyword-only, matching its `deposit(*, currency=...)` twin.
- Realtime `change` / `change_percent` now carry the direction sign, matching the REST `Quote`.
  Every realtime tick/notice parser (domestic stock + NAV, index, bond, ELW, derivatives,
  overseas) previously returned the raw magnitude unsigned, with the direction living only in the
  separate `change_sign` field (`ETFNav.nav_change` and the expected-conclusion `change` fields
  included); a downtick's `change` / `change_percent` is now negative (magnitude times the sign).
  This is a value-semantics change, not a rename: code that read these as an unsigned magnitude
  must drop its own sign handling, and `change_sign` is retained for the raw vendor code. The
  values route through the same abs-normalizing, idempotent `_apply_change_sign` the REST path
  uses, so an unknown sign code fails closed instead of silently flipping direction.
- Realtime market-data quantity fields are now `int` (were `Decimal`), matching the REST surface
  which already parses these as integers. Order-book residual quantities (`best_*_quantity` /
  `total_*_quantity` and the ELW 10-level `ask_quantities` / `bid_quantities` ladders), trade and
  accumulated volumes, the index breadth counts (`rising_count` / `unchanged_count` /
  `falling_count` / `upper_limit_count` / `lower_limit_count`), program-trade and investor-flow
  volumes, the 외국계 member volumes, expected-conclusion volumes, derivative `open_interest`, and
  the ELW `lp_holding` / `lp_net_sell_volume` across all six realtime parsers (domestic stock,
  index, bond, ELW, derivatives, overseas) are integers — an exchange's aggregate depth and volume
  are whole units, so the previous `Decimal` (a byproduct of routing every realtime numeric through
  one `_decimal`) was the lone outlier against the REST `OrderBook` / `Quote` int typing. The new
  shared `_int` is fail-closed like `_decimal` (empty → 0 for stream continuity; a fractional or
  non-finite value is never truncated but raises and drops that frame, matching REST `required_int`).
  This is a value-type change: code comparing these to a `Decimal` still works (`1500 == Decimal(1500)`),
  but code that asserted the field *is* a `Decimal`, or fed it to a `Decimal`-only API, must adjust.
  The order/fill quantity fields on the execution-notice entities stay `Decimal` (they match the REST
  `Order.quantity` / `StockFill` surface, which is `Decimal` to carry US fractional-share orders);
  prices stay `Decimal` throughout.

### Added

- A `kis-trader` console script (alias of `kis-mcp`, both launching the MCP server) so the MCP
  registry record works: a uvx launcher runs a command named like the `server.json` identifier
  (`uvx --from "kis-trader[mcp]" kis-trader`), which now resolves. For hand use, `kis-mcp` is the
  clearer alias. The main CLI stays `kis`.
- The Claude Code plugin auto-wires the MCP server. `.claude-plugin/plugin.json` now declares an
  `mcpServers` entry (`uvx --from "kis-trader[mcp]" kis-mcp`) plus a `userConfig` that prompts for a
  saved profile name and the trading environment, so installing the plugin offers the MCP server
  without hand-editing a client config; no secrets are stored in the plugin (credentials stay in the
  `kis config` store). It also ships PreToolUse hooks: a secret guard that denies a Bash/Write/Edit
  whose input contains a real KIS app key/secret/approval key or a Bearer token, and an order guard
  that asks for confirmation on a command that looks like a live (`real`) order (best-effort
  defense-in-depth; the authoritative real-order gate stays in the library and the MCP server).
- Stock handles (`DomesticStock`/`OverseasStock`) now have a readable `__repr__`
  (`DomesticStock(symbol='005930', exchange='KRX')`).
- The closed-choice `Literal` types used in public method signatures are now exported from
  the package root (`from kis_trader import ...`), so users can name/annotate them under
  `py.typed` — previously only a handful (`Side`, `OrderType`, `VolumeMetric`, ...) were.
  Added: `DomesticQuoteExchange`, the ranking filters/metrics (`TradeSide`,
  `DisparityExtreme`, `DividendSettlement`, `DividendKind`, `DividendMarket`,
  `ExpectedCloseFilter`, `ExpectedCloseMarket`, `FinancialAnalysis`, `FiscalQuarter`,
  `NearHighLowSide`, `AfterHoursBalanceSide`, `CreditBalanceMetric`, `QuoteBalanceMetric`,
  `ProfitAssetMetric`, `ValuationMetric`, `ShortSaleWindow`), the ELW sorts (`ELWRight`,
  `UnderlyingSort`, `ChangeSort`, `IndicatorSort`, `SensitivitySort`, `VolumeSort`,
  `QuickChangeSort`, `QuickChangeWindow`), the index query types (`IntradayInterval`,
  `ExpectedInterval`, `ExpectedSession`), the domestic-market filters
  (`DomesticMarketFilter`, `DomesticMarket`, `CreditMarketFilter`, `CreditSort`, `ForeignBrokerSort`,
  `InvestorType`, `NetBuyBasis`, `BrokerOpinionFilter`), the overseas-balance filters
  (`BalanceInquiry`, `BalanceNation`), the derivative Literals (`DerivativeMarket`,
  `DerivativeProduct`, `DerivativeProductFilter`, `NightMarginDivision`), the derivative division
  (`DerivativeDivision`), the futures-board filter (`FuturesBoardMarket`), the ELW trend intervals
  (`TrendInterval`, `SensitivityInterval`, `IndicatorInterval`), and the instrument types
  (`SecurityType`, `DomesticListingIndex`). `Right` and `Session` stay off the root (name
  collisions) and are reachable via `kis_trader.order`.

### Removed

- `DomesticBoard`, `StockVenue`, `OverseasVenue`, `resolve_market`, and the public
  market-identifier on `Order` (now derived from `region` + `asset_class` inside the
  engine and fingerprint codec). `OverseasZone` is added for the overseas realtime feed.

### Unchanged (noted to be explicit)

- `Quote.market` and `OrderBook.market` keep the name `market`: they are shared snapshot
  types whose value spans asset classes (a stock exchange, a derivative market, a bond
  market division), so the generic `market` fits them, while the precise `exchange` name
  is reserved for stock-specific handles and the order layer.

### Fixed

- Overseas realtime stock feeds now deliver. The subscription `tr_key` was the bare master RSYM
  (e.g. `NASAAPL`), but the delayed feed requires a `"D"` prefix (`DNASAAPL`); without it the server
  sent nothing, so `realtime.overseas.stock(...).trades()` / `.order_book()` silently received zero
  frames. The overseas stock handle now prefixes `"D"`. And the `HDFSASP0` order-book layout had a
  duplicated level-3 block (77 fields); the real frame is 71 (an 11-field header + 10 levels x 6), so
  every order-book frame failed to decode — the duplicate is removed. Both verified live against the
  US session (trades and the 10-level order book now parse). `HDFSCNT0` (trades, 26 fields) was
  already correct.
- Overseas ranking tolerates a blank `tamt`: `OverseasRankedStock.trading_amount` is `Decimal | None`
  (was a `required` read that raised and broke the whole `ranking.by_change`/`by_volume` query when a
  ranked row returned an empty trading amount, observed live against NASDAQ).
- An order that fails authentication (HTTP 401) no longer stays in-flight. A 401 is rejected at the
  gateway's auth step before the order engine, so it provably never reached the matching engine; the
  send/change paths now clear the in-flight claim and re-raise `KISAuthError` unchanged instead of
  promoting it to `OrderTimeoutError`, so after fixing the token the caller can resubmit the same
  `client_order_id` directly (no `reconcile` needed, no double-order risk). Rate-limit (429) and the
  HTTP-500 app-key throttle still stay in-flight — their acceptance is genuinely unknown, so they keep
  requiring a reconcile.
- The HTTP transport's `request()` no longer lets a non-`KISError` escape. Transport-layer `requests`
  exceptions outside `Timeout`/`ConnectionError` (e.g. `TooManyRedirects`, `RetryError`, and the
  `ChunkedEncodingError`/`ContentDecodingError` that are `RequestException`- not `ConnectionError`-derived)
  now raise `TransportTimeoutError` (result-unknown, so a write is never silently resent), and
  request-preparation errors that provably never hit the socket
  (`InvalidURL`/`MissingSchema`/`InvalidSchema`/`InvalidHeader`/`URLRequired`) raise a plain `KISError`; a non-JSON `200`
  body already mapped to `KISError`. All of these keep `except KISError` catching every transport failure.
- Realtime subscribe/unsubscribe no longer leaks a coroutine if the background loop stops between the
  liveness check and scheduling: `_submit_wire`/`_schedule_wire`/`_call_async` now close the coroutine on
  the resulting `RuntimeError` (the subscription stays in `_desired` for re-send on reconnect) instead of
  leaving it un-awaited.
- Overseas non-US order modify/cancel used the US-only TR `TTTT1004U` for every exchange, so a resting
  Hong Kong / Japan / Shanghai / Shenzhen / Vietnam order could not be cancelled or modified. The
  `order-rvsecncl` TR is now selected per exchange (`TTTT1004U` US, `TTTS1003U` HK, `TTTS0309U` JP,
  `TTTS0302U` SH, `TTTS0306U` SZ, `TTTS0312U` VN, with paper twins), matching the per-exchange placement
  table. SH/SZ/VN support cancel only, so a modify on those markets is now rejected fail-closed.
- Paper-mode domestic derivative execution notices subscribed the real-mode TR `H0IFCNI0`; they now use
  `H0IFCNI9` in paper (the night sessions have no paper feed and are rejected). `RealtimeMessage` routing
  for the unified market-operation feed `H0UNMKO0` (no symbol field) now uses an empty `tr_key` so its
  callbacks actually fire.
- `account.domestic.realized_profit_balance()` raised `KISError` only on some corruption; it now
  fail-closes when the response carries no account summary (`output2`) instead of returning a result with
  every summary field zero (which read as "no realized P&L").
- Pension `present_balance()` sent `INQR_DVSN`, which is not a parameter of the inquire-present-balance
  endpoint; it now sends the spec's required `USER_DVSN_CD`.
- Bond parsers no longer crash on fields the vendor legitimately blanks: `BondIssuance` coupon/discount/
  redemption/YTM rate and interest-payment-months are now `Decimal | None` / `int | None`,
  `BondValuation.average_yield` is `Decimal | None`, and bond buyable quantity/CMA value tolerate a blank
  (0) — a coupon bond, a market-basis buyable query, or a non-trading valuation day no longer raises.
- An HTTP 500 carrying `msg_cd=EGW00201` (KIS app-key throttle) is now raised as `KISRateLimitError`
  after retries instead of a generic `KISError`. Overseas news timestamp parse failures raise `KISError`
  with context instead of a bare `ValueError`.
- User realtime `on=` callbacks run on a dedicated worker thread, so a slow/blocking callback can no
  longer starve the websocket keepalive (which dropped the socket and silently reconnected, losing
  ticks). Reconnect/resubscribe is now logged.
- Credential `save()` / `set_default()` / `set_hts_id()` hold a per-file lock across the read-modify-write,
  so concurrent writers can no longer drop each other's profile.
- MCP: `modify_order` now applies the real-order symbol allowlist (resolved from the original order), and
  the modify/cancel confirmation ticket shows the order's symbol and side. The order store's cross-request
  change guard is now atomic with the claim (two concurrent modify/cancel on one order can no longer both
  reach the wire).

### Changed (naming)

- `ranking.by_volume(metric=...)`: the `"trading_volume"` metric is renamed `"average_volume"`, because
  it selects KIS `FID_BLNG_CLS_CODE` 0 which is the *average* volume ranking (the previous name
  misdescribed the result). `VolumeMetric` now reads `["average_volume", "cumulative_trading_amount",
  "volume_growth", "turnover"]`.

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
