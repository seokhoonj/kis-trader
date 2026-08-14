# 기업행위·일정

예탁결제원 기업행위 일정. 각 조회는 기간 `[start, end]`(YYYYMMDD)을 받고, `symbol` 을 주면 그 종목만.
배당 일정은 [순위·조건검색](screening.md)의 조건검색 절에도 있습니다.

```python
c = kis.domestic.calendar

c.capital_reductions(start="20240101", end="20240630")  # 자본감소(감자)
c.forfeited_shares(start="20240101", end="20240630")    # 실권주
c.appraisal_rights(start="20240101", end="20240630")    # 주식매수청구
c.par_value_changes(start="20240101", end="20240630")   # 액면교체
c.mandatory_deposits(start="20240101", end="20240630")  # 의무예치
```
