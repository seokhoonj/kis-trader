# 지수

지수 핸들은 `kis.domestic.index(이름_또는_코드)` — 이름(`"KOSPI"`)이나 업종코드(`"0001"`) 둘 다 됩니다.

```python
i = kis.domestic.index("KOSPI")

i.quote()                       # 지수 현재가
i.bars("1d", start="20240101")  # 지수 차트 (일/주/월)
i.categories()                  # 시장 요약 + 하위 업종 지수 목록
i.daily_history()               # 스냅샷 + 최근 100건 일/주/월 통계
i.intraday()                    # 당일 시간대별 (1m/5m/10m)
i.ticks()                       # 당일 10초 시계열
```

## 예상체결 지수

```python
i.expected_snapshot()  # 동시호가 대표 예상체결 지수 + 시장별 목록
i.expected_trend()     # 장 시작 전·마감 예상체결 지수 추이
```
