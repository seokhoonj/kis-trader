# 계좌·잔고·손익

계좌 조회는 `kis.domestic.account.*` 에 모여 있다. 세션을 `account=` 로 열어야 한다.

```python
a = kis.domestic.account
```

## 예수금·자산 요약

```python
a.balance()      # 예수금, 총평가, 손익 요약
a.assets()       # 총자산·순자산·예수금·대출·외화
```

## 보유 종목

```python
for p in a.positions():
    p.symbol, p.name
    p.quantity            # 보유 수량
    p.average_price       # 평균 매입가
    p.current_price       # 현재가
    p.evaluation_profit   # 평가손익
    p.profit_rate         # 수익률 %
```

보유종목 + 요약을 한 번에:

```python
pf = a.portfolio()
pf.positions      # 보유 종목
pf.summary        # 요약
```

## 실현손익

```python
a.trade_profits(start="20240101", end="20240630")   # 종목별 실현손익
a.daily_profits(start="20240101", end="20240630")    # 일별 실현손익
a.realized_profit_balance()                           # 실현손익 포함 잔고
```

## 미체결·주문가능

```python
a.open_orders()                          # 미체결/정정취소 가능 주문
kis.domestic.stock("005930").buyable()   # 매수 가능 수량·금액
kis.domestic.stock("005930").sellable()  # 매도 가능 수량
```

## 권리·증거금

```python
a.rights(start="20240101", end="20240630")   # 배정/신청/환불된 권리
a.integrated_margin()                          # 통합증거금
```

## 해외 계좌

해외는 통화·시장이 얽혀 있어 살짝 다르다.

```python
oa = kis.overseas.account
oa.positions(market=None)   # None = 전체 시장 합산
oa.balance(market="NAS")    # 통화별 요약이라 시장 지정
oa.present_balance()        # 체결기준 현재잔고
oa.period_profit(start="20240101", end="20240630")   # 기간 실현손익
oa.transactions(start="20240101", end="20240630")     # 거래내역
```
