# 순위·조건검색

## 오늘의 순위

`kis.domestic.ranking.*` — 급등주·거래량·시총 같은 시장 전체 순위.

```python
r = kis.domestic.ranking

r.by_change(top="gainers")  # 상승률 상위
r.by_change(top="losers")   # 하락률 상위
r.by_volume()               # 거래량 상위
r.by_market_cap()           # 시가총액 상위
```

각 행(`RankedStock`)은 `rank / symbol / name / price / change / change_percent / volume`:

```python
for row in r.by_change(top="gainers")[:10]:
    print(f"{row.rank:2d}. {row.name:10s} {row.price:>8,}  {row.change_percent:>6}%")
```

순위별 고유 지표(공매도량·신용잔고 등)는 각 결과의 `_raw` 에 있다.

더 있는 순위:

```python
r.by_short_sale(window="1d")  # 공매도 상위
r.by_credit_balance()         # 신용잔고 상위
r.by_near_high_low()          # 신고가/신저가 근접
r.by_dividend()               # 배당률 상위
r.by_disparity()              # 이격도
r.by_volume_power()           # 체결강도
r.by_views()                  # HTS 조회 상위
```

## 조건검색 (HTS 저장조건)

HTS에 저장해둔 조건검색을 불러 실행한다.

```python
kis.domestic.saved_screens()               # 저장된 조건 목록
kis.domestic.saved_screen_stocks(seq="0")  # 특정 조건의 종목들
```

## 관심종목

```python
kis.domestic.watchlist_groups()    # 관심종목 그룹
kis.domestic.watchlist(group="…")  # 그룹 안 종목
```

## 재무·가치 순위

재무비율·밸류에이션으로 정렬된 순위도 있다.

```python
r.by_finance_ratio()  # 재무비율
r.by_valuation()      # 밸류에이션(PER/PBR 등)
r.by_profit_asset()   # 수익성·자산
```

## ELW 스크리너

```python
kis.domestic.elw_screener.search(…)         # 조건 검색
kis.domestic.elw_screener.by_underlying(…)  # 기초자산별
kis.domestic.elw_ranking.by_volume()        # ELW 거래량 순위
```
