# 기업행위·일정

예탁결제원 기업행위·일정 조회입니다. 각 메서드는 `start`·`end`(YYYYMMDD 문자열 또는 `date`)를
조회기간 파라미터로 전달하고, `symbol` 을 주면 종목 필터로 전달합니다. 실제 조회 범위와 제공 내역은
해당 API의 응답 기준을 확인하세요. 계좌에 배정된 **내** 권리 내역은 [계좌](account.md)의 `kis.account.domestic.rights(...)`.

```python
calendar = kis.domestic.calendar

calendar.dividends(start="20240101", end="20241231")             # 배당
calendar.ipo_subscriptions(start="20240101", end="20241231")     # 공모주 청약
calendar.rights_offerings(start="20240101", end="20241231")      # 유상증자
calendar.bonus_issues(start="20240101", end="20241231")          # 무상증자
calendar.shareholder_meetings(start="20240101", end="20241231")  # 주주총회
calendar.merger_splits(start="20240101", end="20241231")         # 합병·분할
calendar.listings(start="20240101", end="20241231")              # 상장
```

## 자본·권리 변동

```python
calendar.capital_reductions(start="20240101", end="20240630")  # 자본감소(감자)
calendar.par_value_changes(start="20240101", end="20240630")   # 액면변경
calendar.appraisal_rights(start="20240101", end="20240630")    # 주식매수청구
calendar.forfeited_shares(start="20240101", end="20240630")    # 실권주
calendar.mandatory_deposits(start="20240101", end="20240630")  # 의무예치
```
