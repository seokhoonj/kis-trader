# ETF·ETN

ETF/ETN의 **NAV(순자산가치)**·구성종목·호가. 시장 체결가·차트는 일반 종목처럼 `quote()`·`bars()` 를 씁니다.

```python
stock = kis.domestic.stock("069500")  # KODEX 200

stock.nav()                           # NAV 스냅샷 (NAV·괴리율·추적오차율·순자산총액)
stock.nav_comparison()                # 당일 시장가 vs NAV OHLC 비교
stock.etf_components()                # 구성종목(PDF, 설정 구성내역) + ETF 요약
stock.etf_order_book()                # 10단계 호가 + LP(유동성공급자) 잔량·잔량증감·중간가
```

## NAV 추이

```python
stock.nav_history(start="20240101", end="20240630")  # 일별 NAV-가격 추이 (괴리율)
stock.nav_intraday(interval_minutes=1)               # 분별 시장가-NAV 비교 (최근 30개)
```
