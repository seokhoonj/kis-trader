# `kis.overseas` — 해외

해외 주식·지수·파생 시세와 계좌, 해외 순위/참조 데이터를 담는다. 각 메서드의 파라미터·
KIS URL·TR-id·필드 설명은 해당 메서드의 한국어 docstring에 있다.

## 종목/계약 핸들

```python
kis.overseas.stock("AAPL")        # -> OverseasStock (거래소는 종목마스터로 자동 해석: NAS)
kis.overseas.index("SPX")         # -> OverseasIndex
kis.overseas.futures("ESU24")     # -> OverseasDerivative (market="future")
kis.overseas.option(...)          # -> OverseasDerivative (market="option")
```

### `OverseasStock`

| 갈래 | 메서드 |
|---|---|
| 시세 | `quote` · `current_price` · `bars` · `order_book` · `trades` |
| 주문 | `buy`/`sell` · `daytime_buy`/`daytime_sell`(미국 주간거래) · `reserve_buy`/`reserve_sell`(미국 예약) |

거래소는 종목마스터로 자동 해석되므로 심볼만 주면 된다. 아시아 시장 보유/미체결은 계좌
쪽에서 `market=` 로 지정하거나 `market=None` 으로 전체 합산한다.

## 계좌 — `kis.overseas.account`

```python
kis.overseas.account.balance(market=…)         # 통화별 요약(합산 불가라 시장 지정)
kis.overseas.account.positions(market=None)    # 보유 (None=7개 시장그룹 전체 합산)
kis.overseas.account.open_orders(market=None)  # 미체결 (None=전체)
kis.overseas.account.buyable(symbol, exchange=, price=)   # 매수가능
kis.overseas.account.present_balance(…)        # 체결기준 현재잔고 (3블록)
kis.overseas.account.settlement_balance(…)     # 결제기준 잔고
kis.overseas.account.period_profit(start=, end=, …)      # 기간 실현손익
kis.overseas.account.transactions(start=, end=, …)       # 일별 거래내역
kis.overseas.account.foreign_margin()          # 통화별 외화 예수금/증거금
kis.overseas.account.reserved_orders(start=, end=)       # 미국 예약주문 목록
kis.overseas.account.cancel_reserved_order(reserved_order_id, receipt_date)
kis.overseas.account.algo_orders() / .algo_executions(order_id, …)   # 알고주문
```

## 시장 전체

### 순위 — `kis.overseas.ranking`

10종: `by_change`(등락률) · `by_volume`(거래량) · `by_amount`(거래대금) ·
`by_market_cap`(시가총액) · `by_turnover`(회전율) · `by_buy_strength`(체결강도) ·
`by_price_fluctuation`(변동률) · `by_volume_surge`(거래량 급증) · `by_trade_growth`(거래증가) ·
`by_new_highlow`(신고신저).

### 참조/파생 데이터 (직접 verb)

```python
kis.overseas.quotes([("NAS","AAPL"), …])   # 멀티종목 시세
kis.overseas.search_stocks(**filters)      # 종목 검색(가격·규모 등 범위필터)
kis.overseas.product_info(…)
kis.overseas.news(…) / .breaking_news(…)
kis.overseas.corporate_actions(…) / .rights(…) / .settlement_dates(…)
kis.overseas.collateral_stocks(…)          # 담보가능 종목
kis.overseas.industries(…) / .industry_stocks(…)
kis.overseas.futures_details([...]) / .option_details([...])   # 배치 상품기본정보
kis.overseas.futures_open_interest(…)      # 미결제추이(CFTC)
kis.overseas.derivatives_market_hours(…)
```
