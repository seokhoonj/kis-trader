# 시세 보기

종목 하나는 `kis.domestic.stock("종목코드")` 로 잡고, 거기서 시세를 조회한다.

```python
s = kis.domestic.stock("005930")   # 삼성전자
```

## 현재가

```python
q = s.quote()

q.current_price    # 현재가
q.open, q.high, q.low, q.previous_close
q.change           # 전일대비 (부호 포함)
q.change_percent   # 등락률 %
q.volume           # 누적 거래량
```

## 차트 (봉)

`bars(interval, start=, end=)` 하나로 분봉·일봉·주봉·월봉을 다 본다.

```python
s.bars("1d", start="20240101", end="20240630")  # 일봉
s.bars("1wk", start="20230101")                  # 주봉
s.bars("1m", max_bars=120)                        # 당일 1분봉 최근 120개
```

과거→현재 순으로 정렬돼 온다. 각 봉:

```python
bars = s.bars("1d", start="20240101")
b = bars[-1]                      # 가장 최근
b.timestamp, b.open, b.high, b.low, b.close, b.volume
```

특정 날짜의 분봉은:

```python
s.minute_bars_on("20240102")
```

## 호가

```python
ob = s.order_book()
ob.bids     # 매수 호가 (가격·잔량) 리스트
ob.asks     # 매도 호가
```

## 체결 내역

```python
s.trades()          # 최근 체결
s.recent_prices()   # 최근 가격 추이
```

## 여러 종목 한 번에

```python
kis.domestic.quotes(["005930", "000660", "035720"])   # 리스트로 한 방에

# 보드가 다르면 (KRX/NXT) 튜플로
kis.domestic.quotes([("KRX", "005930"), ("NXT", "123456")])
```

## 시간외

```python
s.after_hours_quote()         # 시간외 현재가
s.after_hours_daily()         # 시간외 일별
s.after_hours_conclusions()   # 시간외 체결
```

## 지수·해외

```python
kis.domestic.index("0001").quote()      # KOSPI
kis.domestic.index("1001").quote()      # KOSDAQ

kis.overseas.stock("AAPL").quote()      # 거래소 자동 (NAS)
kis.overseas.stock("AAPL").current_price()
kis.overseas.stock("AAPL").bars("1d")
```

::: {.callout-tip}
필드 이름·단위가 궁금하면 `help(type(q))` — 각 필드의 한국어 설명과 KIS 원본 키가 나온다.
:::
