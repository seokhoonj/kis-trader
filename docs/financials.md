# 재무·실적

종목의 재무제표·비율과 실적 추정. 결산기별은 **최근→과거** 순이고, `quarterly=True` 면 분기·아니면 연간입니다.

```python
stock = kis.domestic.stock("005930")

stock.profile()                         # 종목 기본정보 (이름·상장주식수·업종·상장일)
stock.income_statement()                # 손익계산서 (연간)
stock.income_statement(quarterly=True)  # 손익계산서 (분기)
stock.balance_sheet()                   # 대차대조표
stock.financial_ratios()                # 주요 재무비율 (ROE·EPS·BPS·부채비율…)
stock.growth_ratios()                   # 성장성 (매출·영업이익·자산 증가율)
stock.profitability_ratios()            # 수익성 (ROA·ROE·순이익률…)
stock.stability_ratios()                # 안정성 (부채비율·유동비율·당좌비율…)
stock.other_ratios()                    # 기타 (EVA·EBITDA·EV/EBITDA)
```

## 추정·의견

```python
stock.earnings_estimate()                                 # 월간 추정 손익·투자지표 (리서치 추정 대상 종목만)
stock.investor_estimate()                                 # 장중 외국인/기관 순매수 추정 (확정 아닌 가추정)

stock.analyst_opinions(start="20240101", end="20240630")  # 애널리스트 의견·목표주가·증권사명 시계열(넓은 구간도 전량)
```
