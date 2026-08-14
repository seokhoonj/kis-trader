# 시세 보기

종목 하나는 `kis.domestic.stock("종목코드")` 로 잡고, 거기서 시세를 조회합니다.

```python
s = kis.domestic.stock("005930")  # 삼성전자
```

## 현재가

```python
q = s.quote()
print(q.current_price, q.change_percent)  # 예: 71500  0.70
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
s.bars("1d", start="20240101", end="20240630")  # 일봉
s.bars("1wk", start="20230101")                 # 주봉
s.bars("1m", max_bars=120)                      # 당일 1분봉 최근 120개
```

과거→현재 순으로 옵니다. 각 봉(`Bar`)은 `timestamp / open / high / low / close / volume`.

```python
bars = s.bars("1d", start="20240101")
for b in bars[-5:]:  # 최근 5봉
    print(f"{b.timestamp:%Y-%m-%d}  종가 {b.close}  거래량 {b.volume}")
```

특정 날짜의 분봉은 `s.minute_bars_on("20240102")`.

## 호가

```python
ob = s.order_book()
best_bid = ob.bids[0]  # 최우선 매수호가
best_ask = ob.asks[0]  # 최우선 매도호가
print(best_bid.price, best_bid.quantity)
print(ob.total_bid_quantity, ob.total_ask_quantity)  # 총 매수/매도 잔량
```

`bids` · `asks` 는 각각 (가격 `price`, 잔량 `quantity`) 호가 단계 튜플입니다.

## 체결·최근가

```python
s.trades()         # 최근 체결 내역
s.recent_prices()  # 최근 가격 추이
```

## 여러 종목을 한 번에

```python
for q in kis.domestic.quotes(["005930", "000660", "035720"]):
    print(q.symbol, q.current_price, q.change_percent)

# 보드가 다르면(KRX/NXT) 튜플로 지정
kis.domestic.quotes([("KRX", "005930"), ("NXT", "000660")])
```

::: {.callout-note}
**KRX vs NXT** — KRX는 한국거래소 정규시장, NXT(넥스트레이드)는 2025년 출범한 대체거래소(ATS,
정규거래소 밖의 또 다른 매매 장)입니다. 같은 종목이 두 곳에서 거래되며, 보드를 안 주면 KRX 기준입니다.
:::

## 시간외

```python
s.after_hours_quote()        # 시간외 현재가
s.after_hours_daily()        # 시간외 일별
s.after_hours_conclusions()  # 시간외 체결
```

## 지수·해외

```python
kis.domestic.index("KOSPI").quote()   # 이름으로 (업종코드 "0001" 도 가능)
kis.domestic.index("KOSDAQ").quote()  # 이름으로 (업종코드 "1001" 도 가능)

kis.overseas.stock("AAPL").quote()    # 거래소 자동 (NAS)
kis.overseas.stock("AAPL").bars("1d")
```

::: {.callout-tip}
필드가 더 궁금하면 `help(type(q))` — 각 필드의 한국어 설명과 KIS 원본 키(tr-id 포함)가 나옵니다.
원본 응답 전체는 `q._raw` 로 접근.
:::
