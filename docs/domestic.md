# `kis.domestic` — 국내

국내(KRX/NXT) 주식·지수·채권·ELW·지수파생 시세와 계좌, 시장 전체 조회를 담는다.
각 메서드의 파라미터·KIS URL·TR-id·필드 설명은 해당 메서드의 한국어 docstring에 있다.

## 종목/계약 핸들

핸들은 한 종목(또는 계약)에 대한 조회·주문을 시킨다.

```python
kis.domestic.stock("005930")      # -> DomesticStock
kis.domestic.index("0001")        # -> Index (KOSPI)
kis.domestic.bond("KR6000012345") # -> Bond
kis.domestic.elw("58J297")        # -> ELW
kis.domestic.futures("101W09")    # -> FuturesContract
kis.domestic.option("201W09")     # -> OptionContract
```

### `DomesticStock`

| 갈래 | 메서드 |
|---|---|
| 시세 | `quote` · `bars` · `order_book` · `trades` · `recent_prices` · `after_hours_quote`/`after_hours_daily`/`after_hours_conclusions`/`after_hours_order_book` |
| 수급/체결 | `investor_flows` · `detailed_investor_history` · `investor_estimate` · `broker_activity`/`broker_daily_activity`/`broker_trade_ticks` · `foreign_net_buy_trend` |
| 프로그램 | `program_trades` · `daily_program_trades` |
| 종목정보 | `profile` · `status` |
| 재무 | `financial_ratios` · `profitability_ratios` · `growth_ratios` · `stability_ratios` · `other_ratios` · `income_statement` · `balance_sheet` · `earnings_estimate` · `analyst_opinions` |
| 분석 | `short_sale_trend` · `credit_balance_trend` · `loan_trend` · `daily_trade_volume` · `volume_profile` · `trade_amount_bands` · `expected_price_trend` · `intraday_executions` |
| ETF | `nav` · `nav_history` · `nav_intraday` · `nav_comparison` · `etf_components` · `etf_order_book` |
| 주문가능/주문 | `buyable` · `sellable` · `credit_buyable` · `buy`/`sell` · `credit_buy`/`credit_sell` · `reserve_buy`/`reserve_sell` |

`bars(interval, start=, end=, max_bars=)` 는 `"1m"`(당일 분봉) / `"1d"`/`"1wk"`/`"1mo"`(기간봉)를
한 verb로 흡수한다. 분봉의 특정일 조회는 `minute_bars_on(...)`.

### `Index` / `Bond` / `ELW`

지수·채권·ELW 핸들도 각자 `quote`/`bars`/… 를 가진다(지원 축은 상품마다 다르다 — docstring 참조).

### `FuturesContract` / `OptionContract`

`quote` · `order_book` · `bars` · `expected_execution_trend` 를 공유하고, **선물에만** `underlying_quote()`
(기초자산 나란히 조회)가 있다. 옵션에서 `underlying_quote` 를 부르면 타입체커가 먼저 잡는다.

## 계좌 — `kis.domestic.account`

```python
kis.domestic.account.balance()        # 예수금/평가 요약
kis.domestic.account.positions()      # 보유 종목 (평가손익)
kis.domestic.account.portfolio()      # 잔고 + 요약
kis.domestic.account.assets()         # 계좌 자산현황
kis.domestic.account.open_orders()    # 미체결/정정취소가능
kis.domestic.account.trade_profits(start=, end=)     # 종목별 실현손익
kis.domestic.account.daily_profits(start=, end=)     # 일별 실현손익
kis.domestic.account.realized_profit_balance()       # 실현손익 포함 잔고
kis.domestic.account.integrated_margin()             # 통합증거금
kis.domestic.account.rights(start=, end=)            # 계좌 권리 내역
kis.domestic.account.reserved_orders(start=, end=)   # 예약주문 목록
kis.domestic.account.cancel_reserved_order(sequence) # 예약 취소
kis.domestic.account.modify_reserved_order(sequence, …)
```

## 시장 전체

### 순위 — `kis.domestic.ranking`

24종. 대표: `by_change(top=…)`(등락률) · `by_volume`(거래량) · `by_market_cap`(시가총액) ·
`by_disparity`(이격도) · `by_quote_balance`(호가잔량) · `by_volume_power`(체결강도) ·
`by_short_sale`(공매도) · `by_credit_balance`(신용잔고) · `by_near_high_low`(신고신저근접) ·
`by_dividend`(배당률) · `by_finance_ratio`/`by_valuation`/`by_profit_asset` ·
`by_views`(HTS 조회상위) · `by_expected_execution_change`(예상체결 등락) ·
`by_overtime_change`/`by_overtime_volume`/`by_overtime_expected_change`(시간외).

### 시장 조회 — `kis.domestic.market`

`investor_flows` · `investor_snapshot` · `investor_net_buy_stocks` · `program_trades`/
`program_investor_trades`/`program_flow` · `broker_opinions` · `foreign_broker_trades` ·
`credit_eligible_stocks` · `lendable_stocks` · `limit_stocks` · `interest_rates` ·
`funds` · `news` · `vi_events` · `trading_calendar` · `futures_market_schedule`.

### 캘린더 — `kis.domestic.calendar`

`dividends` · `ipo_subscriptions` · `rights_offerings` · `bonus_issues` ·
`capital_reductions` · `merger_splits` · `shareholder_meetings` · `mandatory_deposits` ·
`listings` · `par_value_changes` · `forfeited_shares` · `appraisal_rights`.

### ELW — `kis.domestic.elw_ranking` / `kis.domestic.elw_screener`

- `elw_ranking`: `by_change` · `by_volume` · `by_sensitivity` · `by_indicator` · `quick_change`
- `elw_screener`: `search` · `by_underlying` · `underlyings` · `comparables` · `expiring` · `newly_listed`

## 직접 verb

`kis.domestic.quotes([...])`(멀티종목 시세) · `product_info(...)` · `saved_screens`/
`saved_screen_stocks`(HTS 저장조건) · `watchlist`/`watchlist_groups`(관심종목) ·
`option_expiries`/`option_board`/`option_board_futures`(옵션 만기/전광판).
