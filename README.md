# kis-openapi

A clean Python client for the Korea Investment & Securities (KIS) Open API.

```python
from kis_openapi import ...  # design in progress
```

**Audience: Korean KIS users.** Public **identifiers** use industry-standard English
terms (as used across international market-data and brokerage APIs); the explanatory
**docstrings are written in Korean** for the audience, and carry the underlying KIS URLs
and TR-ids. (This audience choice is deliberate — see the source's docstring language.)

Design goals: standard English identifiers, semantic grouping of related vendor endpoints
into single clean functions (with the underlying KIS URLs in each docstring), market data
including analyst opinions/estimates where KIS provides them, domestic and overseas stock
orders and balances across account types, and an order path built to a safety standard
(client-side idempotency, no write-retries, conservative reconciliation).
