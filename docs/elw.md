# ELW

주식워런트증권(ELW)의 지표·민감도·LP 흐름, 그리고 스크리닝·순위.

## 한 종목

```python
elw = kis.domestic.elw("58J297")  # ELW 종목코드

elw.quote()                       # 현재가
elw.indicator_trend()             # 투자지표(레버리지·기어링·내재가치·패리티) 추이
elw.sensitivity_trend()           # 민감도(그릭스) 추이
elw.volatility_trend()            # 내재변동성 추이
elw.lp_flows()                    # 일별 LP(유동성공급자) 매매 흐름
```

## 스크리너

```python
screener = kis.domestic.elw_screener

screener.underlyings()                               # ELW 가 상장된 기초자산 목록
screener.by_underlying("005930")                     # 한 기초자산(삼성전자)에 상장된 ELW
screener.comparables("005930")                       # 비교대상 ELW (코드/이름)
screener.newly_listed(date="20260814")               # 신규상장 ELW (기준일은 최근 영업일 -- 과거분은 미보관)
screener.expiring(start="20240101", end="20240630")  # 만기예정 ELW
```

## 순위

```python
ranking = kis.domestic.elw_ranking

ranking.by_volume()                 # 거래량
ranking.by_indicator()              # 투자지표(레버리지 등)
ranking.by_sensitivity()            # 민감도(델타 등)
ranking.quick_change()              # 당일 급변
```
