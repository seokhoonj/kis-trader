# 기업행위·일정

예탁결제원 기업행위·일정(시장 전체). 모든 조회는 기간 `[start, end]`(YYYYMMDD)을 받고, `symbol` 을
주면 그 종목만 봅니다. 계좌에 배정된 **내** 권리 내역은 [계좌](account.md)의 `account.rights(...)`.

```python
c = kis.domestic.calendar

c.dividends(start="20240101", end="20241231")             # 배당
c.ipo_subscriptions(start="20240101", end="20241231")     # 공모주 청약
c.rights_offerings(start="20240101", end="20241231")      # 유상증자
c.bonus_issues(start="20240101", end="20241231")          # 무상증자
c.shareholder_meetings(start="20240101", end="20241231")  # 주주총회
c.merger_splits(start="20240101", end="20241231")         # 합병·분할
c.listings(start="20240101", end="20241231")              # 상장
```

## 자본·권리 변동

```python
c.capital_reductions(start="20240101", end="20240630")  # 자본감소(감자)
c.par_value_changes(start="20240101", end="20240630")   # 액면교체
c.appraisal_rights(start="20240101", end="20240630")    # 주식매수청구
c.forfeited_shares(start="20240101", end="20240630")    # 실권주
c.mandatory_deposits(start="20240101", end="20240630")  # 의무예치
```
