# ELW

주식워런트증권(ELW)의 지표·민감도·LP 흐름, 그리고 스크리닝·순위.

## 한 종목

```python
w = kis.domestic.elw("58J297")  # ELW 종목코드

w.quote()                       # 현재가
w.indicator_trend()             # 투자지표(레버리지·기어링·내재가치·패리티) 추이
w.sensitivity_trend()           # 민감도(그릭스) 추이
w.volatility_trend()            # 내재변동성 추이
w.lp_flows()                    # 일별 LP(유동성공급자) 매매 흐름
```

## 스크리너

```python
sc = kis.domestic.elw_screener

sc.underlyings()                               # ELW 가 상장된 기초자산 목록
sc.by_underlying("005930")                     # 한 기초자산(삼성전자)에 상장된 ELW
sc.comparables("005930")                       # 비교대상 ELW (코드/이름)
sc.newly_listed(date="20260814")               # 신규상장 ELW (기준일은 최근 영업일 -- 과거분은 미보관)
sc.expiring(start="20240101", end="20240630")  # 만기예정 ELW
```

## 순위

```python
r = kis.domestic.elw_ranking

r.by_volume()                 # 거래량
r.by_indicator()              # 투자지표(레버리지 등)
r.by_sensitivity()            # 민감도(델타 등)
r.quick_change()              # 당일 급변
```
