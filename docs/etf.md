# ETF·ETN

ETF/ETN의 **NAV(순자산가치)**·구성종목·호가. 시장 체결가·차트는 일반 종목처럼 `quote()`·`bars()` 를 씁니다.

```python
s = kis.domestic.stock("069500")  # KODEX 200

s.nav()                           # NAV 스냅샷 (NAV·괴리율·추적오차율·순자산총액)
s.nav_comparison()                # 당일 시장가 vs NAV OHLC 비교
s.etf_components()                # 구성종목(PDF) + ETF 요약
s.etf_order_book()                # 10단계 호가 + LP 잔량·잔량증감·중간가
```

## NAV 추이

```python
s.nav_history(start="20240101", end="20240630")  # 일별 NAV-가격 추이 (프리미엄/디스카운트)
s.nav_intraday(interval_minutes=1)               # 분별 시장가-NAV 비교 (최근 30개)
```
