# 시세 보기

종목 하나는 `kis.domestic.stock("종목코드")` 로 잡고, 거기서 시세를 조회합니다.

```python
stock = kis.domestic.stock("005930")  # 삼성전자
```

## 현재가

```python
quote = stock.quote()
print(quote.current_price, quote.change_percent)  # 예: 71500  0.70
```

`quote()` 가 돌려주는 `Quote` 의 필드:

| 필드 | 뜻 | 단위 |
|---|---|---|
| `current_price` | 현재가 | 원 |
| `open` · `high` · `low` | 시가·고가·저가 | 원 |
| `previous_close` | 전일 종가 | 원 |
| `change` | 전일대비 (부호 포함) | 원 |
| `change_percent` | 등락률 | % |
| `volume` | 누적 거래량 | 주 |
| `week_52_high` · `week_52_low` | 52주 최고·최저 (없으면 `None`) | 원 |
| `as_of` | 조회 시각 | datetime |

## 차트 (봉)

`bars(interval, start=, end=)` 하나로 분봉·일봉·주봉·월봉을 다 봅니다.

```python
stock.bars("1d", start="20240101", end="20240630")  # 일봉
stock.bars("1wk", start="20230101")                 # 주봉
stock.bars("1m", max_bars=120)                      # 당일 1분봉 최근 120개
```

과거→현재 순으로 옵니다. 각 봉(`Bar`)은 `timestamp / open / high / low / close / volume`.

```python
bars = stock.bars("1d", start="20240101")
for bar in bars[-5:]:  # 최근 5봉
    print(f"{bar.timestamp:%Y-%m-%d}  종가 {bar.close}  거래량 {bar.volume}")
```

특정 날짜의 분봉은 `stock.minute_bars_on("20240102")`.

## 호가

```python
order_book = stock.order_book()
best_bid = order_book.bids[0]                                        # 최우선 매수호가
best_ask = order_book.asks[0]                                        # 최우선 매도호가
print(best_bid.price, best_bid.quantity)
print(order_book.total_bid_quantity, order_book.total_ask_quantity)  # 총 매수/매도 잔량
```

`bids` · `asks` 는 각각 (가격 `price`, 잔량 `quantity`) 호가 단계 튜플입니다.

## 체결·최근가

```python
stock.trades()         # 최근 체결 내역
stock.recent_prices()  # 최근 가격 추이
```

## 여러 종목을 한 번에

```python
for quote in kis.domestic.quotes(["005930", "000660", "035720"]):
    print(quote.symbol, quote.current_price, quote.change_percent)

# 보드가 다르면(KRX/NXT) 튜플로 지정
kis.domestic.quotes([("KRX", "005930"), ("NXT", "000660")])
```

::: {.callout-note}
**KRX vs NXT** — KRX는 한국거래소 정규시장, NXT(넥스트레이드)는 2025년 출범한 대체거래소(ATS,
정규거래소 밖의 또 다른 매매 장)입니다. 같은 종목이 두 곳에서 거래되며, 보드를 안 주면 KRX 기준입니다.
:::

## 시간외

```python
stock.after_hours_quote()         # 시간외 현재가
stock.after_hours_daily()         # 시간외 단일가 일자별 종가
stock.after_hours_conclusions()   # 시간외 시간별 체결
stock.after_hours_order_book()    # 시간외 호가창
```

## 상태·과거 분봉

```python
stock.status()                    # 현재가 + 거래·규제·경고 상태
stock.minute_bars_on("20240102")  # 특정 과거일의 1분봉
```

지수는 [시장·지수](market.md), 해외는 [해외주식](overseas.md), ETF·ELW·선물옵션·채권은 각 상품 챕터를 보세요.

::: {.callout-tip}
필드가 더 궁금하면 `help(type(quote))` — 각 필드의 한국어 설명과 KIS 원본 키(TR-ID 포함)가 나옵니다.
원본 응답 전체는 `quote._raw` 로 접근.
:::
