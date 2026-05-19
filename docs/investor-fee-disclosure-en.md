# Investor Fee Disclosure

**Deribit Options Program** | Version 1.0 | Performance fee 10% | Management fee 1.0% p.a.

> Binding terms are governed by the signed investment management agreement.

---

## 1. Fee overview

| Item | Rate / rule | Notes |
|------|-------------|-------|
| Management fee | 1.0% p.a. (quarterly) | On average AUM_mgmt |
| Performance fee | 10% | Only on profits above HWM |
| Billing unit | Per investor (consolidated) | All authorized sub-accounts merged |
| High water mark | Required | **Tied to NAV_perf only** |

---

## 2. Two bases

### NAV_perf (performance NAV) — excludes collateral spot

- **Definition**: Sum of USDC-equivalent sub-account equity minus agreed collateral spot  
- **Used for**: Period P&L, HWM, 10% performance fee  
- **Excludes**: Investor-owned BTC/ETH collateral and its price moves  

### AUM_mgmt — includes collateral spot

- **Definition**: NAV_perf + collateral spot (USDC equivalent)  
- **Used for**: 1% management fee (spot uses margin and custody resources)  

---

## 3. Multi-currency valuation

At settlement (23:59 UTC), each book equity × Deribit index → USDC equivalent.

```
NAV_perf = Σ sub-account USDC-equivalent equity − collateral spot (USDC equivalent)
AUM_mgmt = NAV_perf + collateral spot (USDC equivalent)
```

---

## 4. Performance fee and HWM

```
Distributable profit = max(0, NAV_perf,end − HWM − net subscription adjustment)
Performance fee = distributable profit × 10%
HWM tracks NAV_perf only, not spot
```

---

## 5. USDC collection

Yes. Invoices are in USDC equivalent; deduct USDC first, then convert BTC/ETH at the settlement index if needed (including coin-denominated strategy profits).

---

## Appendix: Does HWM include spot?

**No.** HWM follows NAV_perf only; spot drawdowns do not directly lower HWM. Management fee has no HWM and uses AUM_mgmt including spot.

---

**Generate PDFs**: `python3 scripts/generate_investor_fee_disclosure_pdf.py`
