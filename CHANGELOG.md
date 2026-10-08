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
- The Domestic-qualifier sweep is completed (council P2): the remaining domestic entity types
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
  (`DerivativeQuote`, `ExpectedExecutionTrend`, `FuturesBoardQuote`, `OptionBoardRow`) and
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
- More lone-outlier field renames: `BrokerActivity.member_number` → `member_code` (the
  sibling `BrokerDailyActivity` already uses `member_code`); `OverseasProductInfo.country` →
  `country_name` (the `natn_name` → `country_name` convention the other overseas entities
  follow); `OverseasDerivativeDetail.tradable` → `trade_status` (it holds the `stat_tp` status
  code, not a bool, and `tradable` collided with the sibling `is_tradable: bool`); and the
  realtime `StockOrderBook.expected_qty` → `expected_volume` (the `ANTC_CNQN` spelling every
  other realtime entity uses). The `reference.rights(date_basis=...)` /
  `reference.collateral_stocks(sort=...)` closed-choice parameters are now typed `Literal`
  (behavior unchanged).
- `option_board_futures(market_class="")` now reaches the KOSPI200 main board (council
  D7, live-probe confirmed: a blank `FID_COND_MRKT_CLS_CODE` returns the A01xxx KOSPI200
  monthly contracts with no MKI/KQI board rows). The engine previously rejected a blank
  `market_class`; it now accepts the documented set `""`/`MKI`/`WKM`/`WKI`/`KQI` and rejects
  anything else.
- Closed-choice params typed (council P3): `account.overseas.positions(fuop=...)` → `product`
  typed `DerivativeProductFilter = Literal["all","future","option"]` (engine maps to
  `FUOP_DVSN` 00/01/02 per the official OTFM1412R definition); `account.domestic.night_margin(
  margin_division=...)` typed `NightMarginDivision = Literal["consignment","maintenance"]`
  (engine maps to `MGNA_DVSN_CD` 01/02 = 위탁/유지 per CTFN7107R); the MCP `order_preview` tool
  `side` is now typed `Side` to match `place_order`. Wire codes unchanged.
- `OverseasDerivativeOrder.new_liquidation` → `new_liquidation_type` (council P3): the field
  holds the 신규/청산 open-vs-close classifier code (`new_lqd_dvsn_cd`), and the bare name read
  like a boolean; the `_type` suffix matches the sibling `futures_option_type`.
- MCP `place_order` now accepts and honors `division` (council P2): `order_preview` advertised
  and echoed a `division` (e.g. `immediate_limit` 최유리지정가) that the send path silently dropped,
  so an approved preview was sent as a plain limit/market order. `division` is now threaded through
  `make_stock_order_plan` → `plan_place_order` → `run_place_order` → `_execute_stock_order` (validated
  against `DomesticDivision`, echoed in the approval ticket), so preview, ticket, and the actual order
  agree.
- `ForeignNetBuyPoint.foreign_net_buy` and the realtime `StockForeignBroker.foreign_net_buy_volume`
  are renamed `foreign_broker_net_buy_volume` (council P2) — the `glob_ntby_qty` 외국계(foreign
  member firm) net-buy quantity had three public names; `foreign_broker_net_buy_volume` (already
  on `BrokerTradeTick`) wins, disambiguating 외국계 from 외국인 and carrying the shares unit.
  `foreign_net_buy_change` (`frgn_ntby_qty_icdc`) keeps its name.
- rate/percent normalization (council P2, verified x100 against the official KIS repo
  definitions and a CTLN4050R response example): eight `_rate` fields that hold a x100
  percentage are renamed `_percent` — `RealizedProfitBalance` /`AccountReport`'s
  `asset_change_rate` / `realized_return_rate` / `real_eval_return_rate`, the overseas
  balance `eval_return_rate`, the ELW `lp_holding_rate`, and the overseas collateral
  `loan_rate` / `maintenance_rate` / `collateral_rate` (대출비율/담보유지비율/담보확보비율)
  → `*_percent`. True rates (`exchange_rate`, `coupon_rate`) are unchanged; wire keys
  unchanged.
- Naming unification (council P2): `DomesticReservedOrder.received_date` → `receipt_date`
  (the `rsvn_ord_rcit_dt` concept is `receipt_date` on `ExecutionReport`, the store, the
  overseas reserved order, and the CLI); `Position.security_name` /
  `SellableQuantity.security_name` / `EarningsEstimate.security_name` and
  `BrokerTradeTick.symbol_name` → `name` (the `prdt_name` / `hts_kor_isnm` family is `name`
  on ~25 other entities); `StockFill.cancelled` → `canceled` (US spelling, matching
  `OrderStatus.CANCELED` and `OverseasReservedOrder.canceled`).
- Closed-choice typing (council P2): `TopViewedStock.market` is now `Literal["KOSPI","KOSDAQ"]`
  and the engine decodes the raw `J`/`Q` wire code to `KOSPI`/`KOSDAQ` (fail-closed on an
  unknown code; the raw value stays in `_raw`) instead of surfacing the magic code;
  `OverseasDerivativeOrder.fuop` is renamed `futures_option_type` (the raw `fuop_dvsn` stem
  was not decodable as a field name); and `account.overseas.present_balance(nation=...)` is
  typed `BalanceNation = Literal["all","US","HK","CN","JP","VN"]` (the set the engine already
  enforces). `present_balance`'s `inquiry` / `market_code` stay `str` pending a primary-source
  value set.
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

- `DerivativeOpenOrder.order_time` is now `time | None` (was the raw `"HHMMSS"` string),
  parsed like every other `ord_tmd` field. `SavedScreenStock`'s price fields drop their
  redundant `_price`/`price_` affixes to match every other quote record: `current_price` →
  `price`, `price_change` → `change`, `open_price`/`high_price`/`low_price` → `open`/`high`/`low`.
  The realtime `StockTick` entity moved from the lone `realtime/messages.py` module into
  `realtime/parsers/domestic_stock.py` beside its parser (import it from `kis_trader.realtime`
  as before); `realtime/messages.py` is removed.

- The localized order-kind label field (`ord_dvsn_name` / `nmpr_type_name`, e.g. `"지정가"`) is
  renamed `order_type` → `order_type_name` on `OpenOrder`, `StockFill`, `BondOpenOrder`,
  `DerivativeOpenOrder` and the pension order entity, matching the sibling `ReservedOrder` and
  no longer colliding with the typed `order_type: OrderType` on the request path (which is
  unchanged).
- The overseas order entities' `quantity` field is renamed `order_quantity` to match the
  `order_quantity` that the domestic, bond, derivative, and realtime order records already use:
  `OverseasOpenOrder`, `OverseasAlgoOrder`, `OverseasReservedOrder`, and the pension order entity.
  `OverseasAlgoExecution.quantity` (a fill record) is renamed `filled_quantity`, since it holds
  the executed quantity, not an order quantity. `OverseasTransaction.quantity` stays bare (it is
  the record's only quantity, with no sibling axis). Wire keys and behavior are unchanged.

### Added

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
  `ExpectedInterval`, `ExpectedMarket`, `ExpectedSession`), the market filters
  (`MarketFilter`, `Market`, `CreditMarketFilter`, `CreditSort`, `ForeignBrokerSort`,
  `InvestorType`, `NetBuyBasis`, `BrokerOpinionFilter`), and the derivative division
  (`DerivativeDivision`). `Right` and `Session` stay off the root (name collisions) and are
  reachable via `kis_trader.order`.

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
