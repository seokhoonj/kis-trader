# 종목 수급

한 종목의 투자자·프로그램·공매도·신용·대차·회원사 수급과 예상체결·거래분포.

```python
stock = kis.domestic.stock("005930")

stock.investor_flows()             # 일자별 개인/외국인/기관 매매동향
stock.detailed_investor_history()  # 세부 투자주체별 매수·매도·순매수 일별
stock.foreign_net_buy_trend()      # 장중 외국계 순매수 추이
stock.program_trades()             # 장중 프로그램매매 흐름
stock.daily_program_trades()       # 프로그램매매 일별 추이
```

## 공매도·신용·대차

```python
stock.short_sale_trend(start="20240101", end="20240630")  # 공매도 추이
stock.credit_balance_trend()                              # 신용잔고(융자/대주) 추이
stock.loan_trend(start="20240101", end="20240630")        # 대차거래(대여) 추이
```

## 회원사·체결분포

```python
stock.broker_activity()     # 매도/매수 상위 회원사(증권사) 비중
stock.broker_trade_ticks()  # 회원사 실시간 매매 체결 틱
stock.broker_daily_activity("99999", start="20240101", end="20240131")  # 한 회원사 일별

stock.expected_price_trend()    # 동시호가 예상체결가 추이
stock.intraday_executions()     # 당일 체결·최우선호가·체결강도
stock.volume_profile()          # 가격대별 거래량 분포(매물대)
stock.trade_amount_bands()      # 체결금액대별 매매비중
stock.daily_trade_volume(start="20240101", end="20240630")  # 일별 매수/매도 체결량
```
