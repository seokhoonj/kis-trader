# 채권

채권 핸들은 `kis.domestic.bond(표준코드)`.

```python
b = kis.domestic.bond("KR6095572D97")

b.profile()                                     # 기본/발행 정보 (발행일·만기·표면금리·만기수익률·통화)
b.issuance()                                    # 상세 발행조건·발행기관·신용등급·거래상태
b.daily_prices()                                # 날짜별 현재가·등락·OHLCV (과거→현재)

b.valuations(start="20240101", end="20240630")  # 평가기관별 단가·수익률 일별 시계열
```
