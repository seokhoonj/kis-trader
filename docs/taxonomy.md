# 함수 지도 {.unnumbered}

전체 공개 API를 **한 장으로 조망**하는 지도입니다. 각 메서드의 상세 사용법은 왼쪽 차례의 주제별
장에 있습니다. 여기서는 "무엇을 어디서 부르는가"의 전체 구조를 봅니다.

## 두 축으로 읽기

패키지는 **두 축이 겹친 격자**입니다.

- **기능축** — 시세 · 시장·검색 · 주문 · 계좌 · 실시간
- **시장축** — 국내주식 · 국내지수 · 국내파생(선물·옵션) · 국내채권 · ELW · 해외주식 · 해외파생 · 연금

축마다 **의존성**이 다른 것이 핵심입니다.

| 기능 | 필요한 것 | 무엇이 자산군을 정하나 |
|------|-----------|------------------------|
| 시세 · 실시간 | 앱키(토큰)만 — 계좌 무관 | 심볼/코드 |
| 주문 · 계좌 | 계좌(CANO + 상품코드) | 상품코드(01 주식 · 03 국내파생 · 08 해외파생) |

조직 원리도 기능마다 다릅니다. **심볼을 다루는 것(시세·발주·종목 핸들)은 지역으로 나뉘고**
(`kis.domestic` / `kis.overseas`), **계좌·주문 상태는 기능으로 통합**됩니다
(`kis.account` 상품코드 분기, `kis.orders` 자산 공통 라이프사이클).

## 진입점 한눈에

```text
KISClient(profile="main")            # 세션
├─ .domestic     국내 시세 · 발주 · 시장·검색      (심볼 축)
├─ .overseas     해외 시세 · 발주 · 시장·검색      (심볼 축)
├─ .account      계좌 조회 (상품코드로 종류 자동 분기; IRP는 .account.pension 퇴직연금 조회)
├─ .orders       주문 라이프사이클 (reconcile / cancel / modify)
└─ .realtime()   실시간 웹소켓 → .domestic / .overseas
```

## 격자: 기능 × 시장

각 칸은 그 조합의 진입 호출입니다(핸들은 `(code)` 생략 표기).

| | 국내주식 | 국내지수 | 국내파생 | 국내채권 | ELW | 해외주식 | 해외파생 | 연금 |
|---|---|---|---|---|---|---|---|---|
| **시세** | `domestic.stock` | `domestic.index` | `domestic.futures`·`option` | `domestic.bond` | `domestic.elw` | `overseas.stock` | `overseas.futures`·`option` | — |
| **시장·검색** | `domestic.ranking`·`market` | `domestic.index` | `domestic.option_board` | — | `domestic.elw_ranking`·`elw_screener` | `overseas.ranking`·`news` | `overseas.derivatives_market_hours` | — |
| **주문** | `domestic.stock(…).buy/sell` | — | `domestic.futures(…).buy/sell` | `domestic.bond(…).buy/sell` | `domestic.stock(…).buy/sell` | `overseas.stock(…).buy/sell` | `overseas.futures(…).buy/sell` | — |
| **계좌** | `account.domestic` | — | `account` (상품 03) | `account.domestic.bonds` | `account.domestic` | `account.overseas` | `account` (상품 08) | `account.pension` (IRP 29) |
| **실시간** | `realtime().domestic.stock` | `.index` | `.futures`·`option` | `.bond`·`bond_index` | `.elw` | `realtime().overseas.stock` | `.overseas.futures`·`option` | — |

체결통보는 종목이 아니라 HTS 아이디 단위라 `realtime().domestic.execution_notices` /
`realtime().overseas.execution_notices` 하위에 따로 있습니다.

---

## 세션 · 클라이언트 — `KISClient`

```python
KISClient(*, profile=None, app_key=None, app_secret=None, account=None,
          environment=None, hts_id=None, account_password=None, config_dir=None,
          transport=None, token_cache_dir=None, throttle=True, requests_per_second=None,
          store=None, orderable=True, allow_credit=False, risk=None, ...)
```

- `KISClient(profile="main")` — 프로필의 저장된 자격증명으로 세션을 엽니다. 프로필 생략 시
  `KIS_DEFAULT_PROFILE` → `credentials.json`의 `default_profile` → 첫 항목 순으로 해석합니다.
- 상품코드 게이트: DC가입자(55)는 생성 거부, IRP(29)는 조회 전용(주문 차단), 연금저축(22)은 주문 가능.

| 진입 | 반환 | 설명 |
|------|------|------|
| `kis.domestic` | `DomesticNamespace` | 국내 자산 시세·발주·시장·검색 |
| `kis.overseas` | `OverseasNamespace` | 해외 자산 시세·발주·시장·검색 |
| `kis.account` | `StockAccount \| DomesticDerivativesAccount \| OverseasDerivativesAccount` | 상품코드로 종류 자동 분기 |
| `kis.orders` | `OrdersNamespace` | 주문 라이프사이클(자산 무관) |
| `kis.account.pension` | `PensionAccount` | IRP(29) 퇴직연금 조회(조회전용) |
| `kis.instrument(symbol, *, exchange=None)` | `InstrumentRecord` | 해외 심볼 마스터 조회(거래소·통화·이름·실시간심볼) |
| `kis.realtime(*, customer_type="P", reconnect=True)` | `RealtimeClient` | 실시간 웹소켓 클라이언트 |
| `kis.transport` | `Transport` | 저수준 전송(내부용) |
| `kis.environment` | `Environment` | 실전(real)/모의(paper) |
| `kis.hts_id` | `str \| None` | 조건검색·관심종목의 기본 사용자 아이디 |
| `kis.revoke_token()` | `None` | 현재 접근 토큰 폐기 |

---

## 시세 — 계좌 무관(토큰만)

### 국내주식 — `kis.domestic.stock(code, *, market=None)` → `DomesticStock`

**기본 시세**

- `quote()` → `Quote` — 현재가 스냅샷
- `profile()` → `StockProfile` — 종목 기본정보(상장주식수·자본금·업종·상장일)
- `status()` → `StockStatus` — 현재가와 거래·규제·경고 상태
- `intraday_executions(*, at="235959")` → `IntradayExecutions` — 기준시각 이전 당일 체결·최우선호가
- `bars(interval="1d", *, start=None, end=None, adjusted=True, max_bars=None)` → `list[Bar]` — OHLCV 봉
- `recent_prices(*, interval="1d", adjusted=True)` → `list[RecentPricePoint]` — 최근 30개 일·주·월 + 수급 보조
- `minute_bars_on(day, *, max_bars=None)` → `list[Bar]` — 특정 과거일의 1분봉
- `order_book()` → `OrderBook` — 10단계 호가창
- `trades()` → `list[Trade]` — 최근 체결(time & sales)

**투자자 수급**

- `investor_flows()` → `list[InvestorFlow]` — 일자별 개인/외국인/기관 매매동향
- `detailed_investor_history(*, as_of=None)` → `DetailedInvestorHistory` — 세부 투자자별 일별 순매수
- `broker_activity()` → `BrokerActivitySummary` — 매도/매수 상위 회원사 비중
- `broker_daily_activity(member_code, *, start, end)` → `list[BrokerDailyActivity]` — 회원사 일별 매매
- `broker_trade_ticks(*, member_code="99999", min_volume=0)` → `BrokerTradeTicks` — 회원사 실시간 매매 틱
- `foreign_net_buy_trend()` → `list[ForeignNetBuyPoint]` — 장중 외국계 순매수 추이
- `investor_estimate()` → `list[InvestorEstimate]` — 장중 외국인/기관 순매수 추정

**시간외 단일가**

- `after_hours_quote()` → `AfterHoursQuote` · `after_hours_conclusions()` → `list[AfterHoursConclusion]`
- `after_hours_daily()` → `list[AfterHoursDailyPrice]` · `after_hours_order_book()` → `OrderBook`

**프로그램매매**

- `program_trades()` → `list[ProgramTradePoint]` — 장중 시간대별 흐름
- `daily_program_trades(*, as_of=None)` → `list[DailyProgramTradePoint]` — 일별 추이

**재무제표·비율** (모두 `*, quarterly=False`)

- `balance_sheet()` → `list[BalanceSheet]` · `income_statement()` → `list[IncomeStatement]`
- `financial_ratios()` → `list[FinancialRatio]` · `profitability_ratios()` → `list[ProfitabilityRatio]`
- `stability_ratios()` → `list[StabilityRatio]` · `growth_ratios()` → `list[GrowthRatio]`
- `other_ratios()` → `list[OtherRatio]`

**시세분석(신용·공매도·대차·의견·추정·매물대)**

- `credit_balance_trend(*, as_of=None)` → `list[CreditBalancePoint]` — 일별 신용잔고
- `short_sale_trend(*, start=None, end=None)` → `list[ShortSalePoint]` — 일별 공매도
- `volume_profile()` → `VolumeProfile` — 가격대별 거래량(매물대)
- `loan_trend(*, start=None, end=None)` → `list[LoanPoint]` — 일별 대차거래
- `analyst_opinions(*, start=None, end=None)` → `list[AnalystOpinion]` — 투자의견·목표주가
- `earnings_estimate()` → `EarningsEstimate` — 추정 손익·투자지표
- `daily_trade_volume(*, start=None, end=None)` → `list[DailyTradeVolumePoint]` — 일별 매수/매도 체결량
- `expected_price_trend(*, exclude_zero_volume=False)` → `list[ExpectedPricePoint]` — 동시호가 예상 체결가
- `trade_amount_bands()` → `list[TradeAmountBand]` — 체결금액대별 매매비중

**ETF/ETN**

- `nav()` → `ETFNAV` · `nav_comparison()` → `ETFNAVComparison` · `nav_intraday(*, interval_minutes=1)` → `list[ETFNAVMinutePoint]`
- `etf_order_book()` → `ETFOrderBook` · `etf_components()` → `ETFComponents` · `nav_history(*, start, end)` → `list[ETFNAVHistoryPoint]`

주문·여력 메서드는 [주문](#orders) 절에 있습니다.

### 국내지수 — `kis.domestic.index(code)` → `Index`

- `quote()` → `IndexQuote` — 지수 현재가(레벨·시고저·등락종목수)
- `bars(interval="1d", *, start=None, end=None, max_bars=None)` → `list[Bar]` — 지수 봉
- `intraday(*, interval="1m")` → `list[IndexIntradayPoint]` — 당일 시간대별(1m/5m/10m)
- `ticks()` → `list[IndexIntradayPoint]` — 당일 10초 시계열
- `daily_history(*, interval="1d", as_of=None)` → `IndexDailyHistory` — 스냅샷 + 최근 100건 통계
- `expected_trend(*, session="open", interval="10s")` → `list[ExpectedIndexPoint]` — 동시호가 예상지수 추이
- `expected_snapshot(*, market="all", session="open")` → `ExpectedIndexSnapshot` — 대표 예상지수 + 시장별
- `categories()` → `IndexCategories` — 시장 지수 요약 + 하위 업종(시장 지수 전용)

지수 코드: `0001` KOSPI 종합 · `1001` KOSDAQ 종합 · `2001` KOSPI200.

### 국내채권 — `kis.domestic.bond(code)` → `Bond`

- `profile()` → `BondProfile` · `issuance()` → `BondIssuance` — 기본/발행 정보
- `quote()` → `BondQuote` · `bars(interval="1d")` → `list[Bar]` · `daily_prices()` → `list[BondDailyPrice]`
- `valuations(*, start, end)` → `list[BondValuation]` — 평가기관별 단가·수익률 시계열
- `order_book()` → `OrderBook` (5단계) · `trades()` → `list[Trade]`

### ELW — `kis.domestic.elw(code)` → `ELW`

기본 시세는 `kis.domestic.stock(code)`로, 이 핸들은 **ELW 고유 지표**(그릭스·변동성)만 얹습니다.

- `quote()` → `ELWQuote` — 기초자산가·내재변동성·이론가·괴리율·머니니스
- `sensitivity_trend(interval="day")` → `list[ELWSensitivityPoint]` — 민감도(그릭스)
- `volatility_trend(interval="day", *, minutes=1, include_past=False)` → `list[ELWVolatilityPoint]`
- `indicator_trend(interval="day", *, minutes=1, include_past=False)` → `list[ELWIndicatorPoint]` — 레버리지·기어링·패리티
- `lp_flows()` → `list[ELWLPFlow]` — 일별 LP 매매

### 국내선물 — `kis.domestic.futures(code)` → `FuturesContract`

- `quote()` → `DerivativeQuote` — 가격·미결제약정·베이시스·이론가·괴리율
- `order_book()` → `OrderBook` (5단계)
- `expected_execution_trend()` → `ExpectedExecutionTrend` — 예상체결 요약 + 시각별 추이
- `bars(interval="1d", *, start=None, end=None, max_bars=None)` → `list[Bar]`
- `underlying_quote()` → `UnderlyingQuote` — 선물+기초자산 베이시스(선물 전용)

### 국내옵션 — `kis.domestic.option(code, *, right=None)` → `OptionContract`

- `quote()` → `DerivativeQuote` · `order_book()` → `OrderBook` (5단계)
- `expected_execution_trend()` → `ExpectedExecutionTrend` · `bars(interval="1d", …)` → `list[Bar]`
- `right`(call/put)은 발주에 필요하며 조회만 할 땐 생략합니다.

**파생 전광판·보조 조회** — `kis.domestic`

- `option_expiries()` → `list[OptionExpiry]` — 상장 만기 월물
- `option_board(expiry, *, underlying="KOSPI200")` → `OptionBoard` — 콜/풋 전광판(행사가별 시세·그릭스)
- `option_board_futures(*, market_class="MKI")` → `list[FuturesBoardQuote]` — 전광판 하단 선물 시세
- `derivative_margin_rates(base_date, *, underlying_id="")` → `list[DerivativeMarginRate]` — 증거금율(모의 미지원)

### 해외주식 — `kis.overseas.stock(symbol, *, exchange=None)` → `OverseasStock`

`exchange` 생략 시 종목 마스터로 거래소를 자동 해석합니다(다중 거래소 심볼은 명시 필요).

- `quote()` → `Quote` · `current_price()` → `OverseasCurrentPrice`
- `bars(interval="1d", *, start=None, end=None, adjusted=True, max_bars=None)` → `list[Bar]`
- `order_book()` → `OrderBook` (미국 10단계 / 그 외 1단계) · `trades()` → `list[Trade]`

### 해외지수·환율·금 — `kis.overseas.index(symbol, *, kind="index")` → `OverseasIndex`

`kind` = `index` / `fx` / `bond` / `gold`.

- `bars(interval="1d", *, start=None, end=None, max_bars=None)` → `list[Bar]`

### 해외선물·옵션 — `kis.overseas.futures(srs_cd)` / `kis.overseas.option(srs_cd)` → `OverseasDerivative`

- `quote()` → `OverseasDerivativeQuote` · `order_book()` → `OrderBook` (5단계)
- `bars(*, exchange, interval="1d", max_bars=40)` → `list[Bar]` · `trades(*, exchange, max_trades=40)` → `list[Trade]`
- `detail()` → `OverseasDerivativeDetail` — 틱사이즈·계약크기·증거금·만기

### 다종목 · 검색 · 상품정보

- `kis.domestic.quotes(symbols, *, market="KRX")` → `list[Quote]` — 최대 30종목 현재가
- `kis.domestic.search(query, *, market="all")` → `list[DomesticListing]` — 이름/코드로 종목 찾기
- `kis.domestic.product_info(symbol, *, product_type="300")` → `ProductInfo`
- `kis.overseas.quotes(symbols)` → `list[Quote]` — 최대 10종목(원소는 `(exchange, symbol)`)
- `kis.overseas.search_stocks(exchange, *, price=None, …)` → `OverseasStockSearch` — 범위 조건 검색
- `kis.overseas.product_info(exchange, symbol)` → `OverseasProductInfo`
- `kis.instrument(symbol, *, exchange=None)` → `InstrumentRecord` — 해외 심볼 마스터

---

## 시장·검색 — 시장 전체 분석

### 국내 순위 — `kis.domestic.ranking` → `RankingQueries`

- `by_change(*, direction="gainers")` · `by_volume(*, metric="cumulative_trading_amount")` · `by_market_cap()`
- `by_disparity(*, extreme="highest", period=20)` · `by_quote_balance(*, metric="net_buy")` · `by_volume_power()`
- `by_bulk_trades(*, side="buy")` · `by_interest()` · `by_preferred_disparity()`
- `by_finance_ratio(*, analysis="profitability", year, quarter="annual")` · `by_valuation(*, metric="per", year, …)`
- `by_profit_asset(*, metric="net_income", year, …)` · `by_company_trades(*, side="buy", start, end)`
- `by_dividend(*, kind="cash", start, end, …)` · `by_short_sale(*, window="1d")` · `by_credit_balance(*, metric="margin_ratio", days=2)`
- `by_near_high_low(*, side="high")` · `by_expected_execution_change(*, direction="gainers")` · `by_expected_close(*, filter="all", …)`
- `by_overtime_change(*, direction="gainers")` · `by_overtime_volume()` · `by_overtime_expected_change(*, direction="gainers")`
- `by_after_hour_balance(*, side="ask")` · `by_views()`

모두 `list[RankedStock]`(일부 전용 결과형) 반환.

### 국내 시장분석 — `kis.domestic.market` → `MarketQueries`

- `investor_flows(*, market="KOSPI", as_of=None)` · `investor_snapshot(*, market_code, industry_code)`
- `investor_net_buy_stocks(*, market="all", basis="volume", direction="buy", investor="all")`
- `program_investor_trades(*, market="KOSPI")` · `program_trades(*, market="KOSPI", start=None, end=None)` · `program_flow(*, market="KOSPI")`
- `funds(*, as_of=None)` · `interest_rates()` · `lendable_stocks(*, market="all", symbol="")` · `credit_eligible_stocks(*, market="all", …)`
- `broker_opinions(*, broker, opinion="all", start=None, end=None)` · `foreign_broker_trades(*, sort="amount")`
- `vi_events(*, as_of=None)` · `limit_stocks()` · `trading_calendar(*, base_date=None)` · `futures_market_schedule()` · `news(*, symbol="", date=None)`

### 국내 캘린더 — `kis.domestic.calendar` → `CalendarQueries`

모두 `*, start, end`(+ 대부분 `symbol=None`)를 받습니다.

- `dividends(…, dividend_kind="all")` · `ipo_subscriptions()` · `rights_offerings(…, offering_date_basis="subscription")`
- `bonus_issues()` · `capital_reductions()` · `merger_splits()` · `shareholder_meetings()`
- `mandatory_deposits()` · `listings()` · `par_value_changes()` · `forfeited_shares()` · `appraisal_rights()`

### ELW 순위·스크리너 — `kis.domestic.elw_ranking` / `kis.domestic.elw_screener`

- `elw_ranking`: `by_volume(*, sort=…)` · `by_change(*, sort=…)` · `by_sensitivity(*, sort="delta")` · `by_indicator(*, sort="leverage")` · `quick_change(*, sort="price_surge", window="day")`
- `elw_screener`: `underlyings(*, sort="name")` · `by_underlying(underlying)` · `comparables(underlying)` · `newly_listed(*, date)` · `expiring(*, start, end)` · `search(*, underlying="", issuer="")`

### HTS 저장 화면 · 관심종목 — `kis.domestic`

- `saved_screens(user_id=None)` · `saved_screen_stocks(sequence, *, user_id=None)`
- `watchlist_groups(user_id=None)` · `watchlist(group_code, *, user_id=None)`

### 해외 순위 — `kis.overseas.ranking` → `OverseasRankingQueries`

모두 `*, exchange`를 받고 `list[RankedOverseasStock]` 반환.

- `by_volume` · `by_amount` · `by_trade_growth` · `by_market_cap` · `by_change(*, exchange, top="gainers")`
- `by_volume_surge` · `by_buy_strength` · `by_turnover` · `by_price_fluctuation(*, top="risers")` · `by_new_highlow(*, extreme="high", sustained=True)`

### 해외 시장·뉴스·권리 — `kis.overseas`

- `derivatives_market_hours(*, product_group="", asset_class="", exchange="", kind="%")` — 해외 파생 장운영시간
- `futures_open_interest(product, *, as_of, mode="quantity")` · `settlement_dates()`
- `futures_details(symbols)` · `option_details(symbols)` — 명세 배치(최대 32/30, 실전만)
- `industries(exchange)` · `industry_stocks(exchange, industry_code, *, min_volume=0)`
- `collateral_stocks(symbol, country, *, sort="name", …)`
- `news(…)` · `breaking_news(…)` · `rights(*, start, end, …)` · `corporate_actions(country, symbol, *, start=None, end=None)`

---

## 주문 — 계좌 필요 {#orders}

발주는 **종목 핸들**에서 하고, 접수 이후 관리는 **`kis.orders`**(자산 공통)에서 합니다.

### 발주 (종목 핸들)

| 자산 | 핸들 | 메서드 |
|------|------|--------|
| 국내주식 | `kis.domestic.stock(code)` | `buy` · `sell` · `credit_buy` · `credit_sell` |
| 국내선물 | `kis.domestic.futures(code)` | `buy` · `sell` (`night=` 야간) |
| 국내옵션 | `kis.domestic.option(code, right=)` | `buy` · `sell` (`night=` 야간) |
| 국내채권 | `kis.domestic.bond(code)` | `buy` · `sell`(lot 지목) — 실전 전용 |
| ELW | `kis.domestic.stock(code)` | `buy` · `sell` (주식과 동일 경로) |
| 해외주식 | `kis.overseas.stock(symbol)` | `buy` · `sell` · `overnight_buy/sell` · `reserve_buy/sell` |
| 해외파생 | `kis.overseas.futures/option(srs_cd)` | `buy` · `sell` — 실전 전용 |

대표 시그니처:

- `DomesticStock.buy(*, quantity, limit_price=None, time_in_force="day", division=None, client_order_id=None)` → `ExecutionReport` (`limit_price` 없으면 시장가, `division`은 KRX 주문구분)
- `DomesticStock.credit_buy(*, quantity, credit_type, limit_price=None, loan_date=None, …)` → `ExecutionReport` (모의 미지원, `allow_credit=True` 필요)
- `FuturesContract.buy(*, quantity, limit_price=None, order_type=None, night=False, …)` → `ExecutionReport`
- `Bond.buy(*, quantity, limit_price, client_order_id=None)` → `ExecutionReport` (지정가 전용, 실전 전용)
- `OverseasStock.reserve_buy(*, quantity, limit_price=None, end_date=None, currency="HKD", …)` → `ExecutionReport` (미국/아시아 자동 라우팅)
- `OverseasStock.overnight_buy(*, quantity, limit_price, …)` → `ExecutionReport` (미국 오버나이트, 실전 전용)
- `OverseasDerivative.buy(*, quantity, limit_price=None, stop_price=None, order_type=None, …)` → `ExecutionReport` (실전 전용)

**여력 조회**(발주 전) — 핸들에 함께 있습니다:

- `DomesticStock.buyable(*, limit_price=None)` · `credit_buyable(*, credit_type="21", …)` · `sellable()`
- `FuturesContract.orderable(side, *, limit_price=None)` · `night_orderable(side, …)` (옵션도 동일)
- `OverseasAccount.buyable(...)` · `OverseasDerivativesAccount.orderable(...)`

### 라이프사이클 — `kis.orders` → `OrdersNamespace`

`client_order_id`로 동작하는 자산 무관 표면입니다.

- `reconcile(client_order_id)` → `ExecutionReport | None` — 접수 불확실 주문을 서버 조회로 확정(모호하면 미확정)
- `cancel(client_order_id, *, quantity=None, request_id=None)` → `ExecutionReport` — 취소(해외·야간·해외파생은 전량만)
- `modify(client_order_id, *, limit_price, quantity=None, request_id=None)` → `ExecutionReport` — 가격/수량 정정(새 ODNO 재바인딩)

**예약주문 정정·취소**는 계좌 뷰에 있습니다:
`kis.account.domestic.reserved_orders/cancel_reserved_order/modify_reserved_order`,
`kis.account.overseas.reserved_orders/cancel_reserved_order`.

---

## 계좌 — 계좌 필요

### 진입: 상품코드 자동 분기 — `kis.account`

| 상품코드 | 계좌 종류 | 반환 |
|----------|-----------|------|
| 01 위탁 · 22 연금저축 · 29 IRP · ISA(=01) | 국내주식 계좌 | `StockAccount` |
| 03 국내선물옵션 | 파생 계좌 | `DomesticDerivativesAccount` |
| 08 해외선물옵션 | 해외파생 계좌 | `OverseasDerivativesAccount` |

IRP(29)는 조회 전용, 연금저축(22)은 주문 가능, DC가입자(55)는 API 자체가 불가입니다.

### 국내주식 계좌 — `kis.account.domestic` → `DomesticAccount`

- `balance()` → `Balance` · `positions()` → `list[Position]` · `portfolio()` → `Portfolio` · `assets()` → `AccountAssets`
- `realized_profit_balance()` → `RealizedProfitBalance` (모의 미지원)
- `integrated_margin(include_cma=False, won_basis=True)` → `IntegratedMargin` (모의 미지원)
- `trade_profits(start, end, symbol=None, sort="recent")` → `TradeProfitHistory` (모의 미지원)
- `daily_profits(start, end, symbol=None, sort="recent")` → `DailyProfitHistory` (모의 미지원)
- `rights(start, end)` → `list[AccountRight]` · `open_orders()` → `list[OpenOrder]` (모의 미지원)
- `reserved_orders(start, end, process="all")` · `cancel_reserved_order(sequence, order_date=None)` · `modify_reserved_order(sequence, symbol, side, quantity, …)` (모의 미지원)

### 국내채권 계좌 — `kis.account.domestic.bonds` → `DomesticBondAccount` (실전 전용)

- `balance()` → `list[BondPosition]` · `buyable(code, price=None)` → `BondBuyable`
- `open_orders(order_date)` → `list[BondOpenOrder]` · `fills(start, end, side="all", symbol=None, unfilled_only=False)` → `BondFillHistory`

### 해외주식 계좌 — `kis.account.overseas` → `OverseasAccount`

- `positions(market=None)` → `list[OverseasPosition]` · `balance(market)` → `OverseasBalance` · `buyable(symbol, exchange, price)` → `OverseasBuyableAmount`
- `foreign_margin()` → `list[OverseasForeignMargin]` (모의 미지원)
- `present_balance(won_basis=True, nation="all", …)` → `OverseasPresentBalance`
- `settlement_balance(basis_date, won_basis=True, …)` → `OverseasSettlementBalance` (모의 미지원)
- `period_profit(start, end, …)` → `OverseasPeriodProfit` · `transactions(start, end, symbol=None, side="all")` → `list[OverseasTransaction]` (모의 미지원)
- `open_orders(market=None)` → `list[OverseasOpenOrder]` · `algo_orders()` · `algo_executions(order_id, order_date, …)` (모의 미지원)
- `reserved_orders(start, end)` · `cancel_reserved_order(reserved_order_id, receipt_date)`

### 국내선물옵션 계좌 — `kis.account` (상품 03) → `DomesticDerivativesAccount`

- `balance()` → `DerivativeBalance` · `open_orders(order_date=None, side="all", symbol=None)` → `list[DerivativeOpenOrder]` (모의 지원)
- `valuation_pl()` · `settlement_pl(base_date)` · `base_date_fills(order_date, …)` · `commissions(start, end)` (실전 전용)
- `deposit()` → `DerivativeDeposit` (실전 전용)
- `night_balance()` (계좌비밀번호 필요) · `night_margin(margin_division="01")` (실전 전용)

### 해외선물옵션 계좌 — `kis.account` (상품 08) → `OverseasDerivativesAccount` (실전 전용)

- `deposit(currency="USD", date=None)` · `margin_detail(currency="USD", date=None)`
- `positions(fuop="00")` · `today_orders()` · `daily_fills(start, end)` · `daily_orders(start, end)`
- `period_pnl(start, end)` · `transactions(start, end)` · `orderable(symbol, side, price=None, exercise_reserved=False)`

### 통합잔고 — `kis.account.balance()` → `IntegratedBalance` (`StockAccount`, 실전 전용)

국내주식 + 국내채권 + 해외주식 잔고를 새 와이어 없이 하나로 합성합니다(채권은 매입금액 기준).

### 퇴직연금 — `kis.account.pension` → `PensionAccount` (IRP 29, 조회전용, 실전 전용)

- `deposit()` → `PensionDeposit` · `buyable(symbol, limit_price=None)` → `PensionBuyableAmount`
- `balance()` → `PensionBalance` · `present_balance()` → `PensionPresentBalance` · `orders(only_unfilled=False)` → `list[PensionOrder]`

---

## 실시간 — 계좌 무관(토큰만)

`kis.realtime()` → `RealtimeClient`. 등록한 뒤 `start()`, 콜백 또는 반복으로 소비하고 `stop()` 합니다.

- 원(raw) 표면: `subscribe(tr_id, tr_key, *, on=None)` · `unsubscribe(tr_id, tr_key)` · `stream(*, timeout=None)` · `start(*, timeout=15.0)` · `stop(*, timeout=5.0)` (컨텍스트 매니저 지원)
- 타입드 표면: `.domestic` / `.overseas` — 계약을 호출하면 그 타입만 흐르는 `RealtimeSubscription[T]`를 돌려줍니다.

### 국내 — `rt.domestic`

| 핸들 | 잎(leaf) → 구독 타입 | TR |
|------|----------------------|----|
| `stock(code)` | `trades(venue="KRX")` → `StockTick` | H0STCNT0 |
| | `order_book(venue="KRX")` → `StockOrderBook` | H0STASP0 |
| `futures(code, kind="index")` | `trades()` → `FuturesTick` · `order_book()` → `DerivativeOrderBook` | H0IFCNT0 / H0IFASP0 |
| `option(code, kind="index")` | `trades()` → `OptionTick` · `order_book()` → `DerivativeOrderBook` | H0IOCNT0 / H0IOASP0 |
| `index(code)` | `trades()` → `IndexTick` · `expected_conclusion()` → `IndexExpectedConclusion` · `program_trade()` → `IndexProgramTrade` | H0UPCNT0 / H0UPANC0 / H0UPPGM0 |
| `elw(code)` | `trades()` → `ELWTick` · `order_book()` → `ELWOrderBook` · `expected_conclusion()` → `ELWExpectedConclusion` | H0EWCNT0 / H0EWASP0 / H0EWANC0 |
| `bond(code)` | `trades()` → `BondTick` · `order_book()` → `BondOrderBook` | H0BJCNT0 / H0BJASP0 |
| `bond_index(code)` | `trades()` → `BondIndexTick` | H0BICNT0 |

- `venue`: `KRX` / `NXT` / `unified` — `kind`(선물): `index` / `commodity` / `stock` / `night` — `kind`(옵션): `index` / `stock` / `night`
- `execution_notices` (HTS 아이디 단위): `.stock(hts_id)` → `StockExecutionNotice` · `.derivative(hts_id, session="regular")` → `DerivativeExecutionNotice` (`session`: `regular` / `night_futures` / `night_option`)

### 해외 — `rt.overseas`

| 핸들 | 잎(leaf) → 구독 타입 | TR |
|------|----------------------|----|
| `stock(symbol, exchange=None)` | `trades()` → `DelayedTradeTick` | HDFSCNT0 |
| | `order_book(venue="global")` → `OverseasOrderBook \| AsiaDelayedOrderBook` | HDFSASP0 / HDFSASP1 |
| `futures(srs_cd)` / `option(srs_cd)` | `trades()` → `FuturesTradeTick` · `order_book()` → `FuturesOrderBook` | HDFFF020 / HDFFF010 |

- 해외주식 `tr_key`는 RSYM이라 종목 마스터로 심볼→실시간심볼을 해석합니다(`kis.realtime()`가 주입).
- `venue`: `global`(10호가) / `asia`(아시아 1호가)
- `execution_notices`: `.stock(hts_id)` → `OverseasExecutionNotice` · `.derivative_orders(hts_id)` → `FuturesOrderNotice` · `.derivative_fills(hts_id)` → `FuturesExecutionNotice`

### 구독 객체 — `RealtimeSubscription[T]`

```python
sub = rt.domestic.stock("005930").trades()
for tick in sub:        # 이 계약·이 타입만 (StockTick)
    ...
sub.close()             # 구독 해제 + 반복 종료
with rt.domestic.index("2001").trades() as sub: ...   # 컨텍스트 매니저
rt.domestic.futures("101W09").trades(on=cb)           # 콜백도 가능(항상 sub 반환)
```

`for x in sub` · `close()` · 컨텍스트 매니저 · `.tr_id` · `.tr_key`.
