# Investor Fee Disclosure

**Deribit Options Program** | Version 1.1 | Performance fee 10% | Management fee 1.0% p.a.

> Binding terms are governed by the signed investment management agreement.

---

## 1. Fee overview

| Item | Rate / rule | Notes |
|------|-------------|-------|
| Management fee | 1.0% p.a. (quarterly) | On average AUM_mgmt |
| Performance fee | 10% | Only on profits above HWM |
| Billing unit | Per investor (consolidated) | All **strategy** sub-accounts merged for NAV |
| High water mark | Required | **Tied to NAV_perf only** |
| Collection | Dedicated **Fee sub-account** | Investor transfers amounts due; manager uses API with wallet read/write |

---

## 2. Two bases

### NAV_perf (performance NAV) — excludes collateral spot

- **Definition**: Sum of USDC-equivalent equity across **strategy** sub-accounts minus agreed collateral spot  
- **Used for**: Period P&L, HWM, 10% performance fee  
- **Excludes**: Investor-owned BTC/ETH collateral and its price moves  
- **Excludes** Fee sub-account balance (not part of NAV_perf / HWM)

### AUM_mgmt — includes collateral spot

- **Definition**: NAV_perf + collateral spot (USDC equivalent)  
- **Used for**: 1% management fee  

---

## 3. Multi-currency valuation

At settlement (23:59 UTC), each **strategy** sub-account book equity × Deribit index → USDC equivalent.

```
NAV_perf = Σ strategy sub-account USDC-equivalent equity − collateral spot (USDC equivalent)
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

## 5. Collection: dedicated Fee sub-account

Management and performance fees are **not** deducted directly from strategy sub-accounts.

### 5.1 What is the Fee sub-account?

- A separate Deribit **sub-account** (suggested name: `fee`) used only to hold settled fees owed.  
- **Strategy bots connect only to strategy sub-accounts**; they do **not** trade on the Fee sub-account.  
- After quarterly settlement, the manager issues a USDC-equivalent invoice; the investor **internal-transfers** the amount from main or strategy sub-accounts **into the Fee sub-account**.

### 5.2 Investor obligations

1. Create the Fee sub-account separately from strategy subs.  
2. After receiving the invoice, transfer **USDC** (or as agreed in the IMA) into the Fee sub-account by the due date.  
3. Create a **dedicated API key** on the Fee sub-account (see 5.3) and deliver it securely to the manager.  
4. **Strategy** API keys must **not** enable wallet / withdrawal permissions.

### 5.3 Fee sub-account API permissions (different from strategy)

| Permission | Fee sub-account | Strategy sub-account |
|------------|-----------------|----------------------|
| Read | ✅ Required | ✅ Required |
| Trade | ❌ Off | ✅ Required |
| Wallet read/write | ✅ **Required** (manager checks balance and transfers out) | ❌ **Off** |

After funds arrive in the Fee sub-account, the manager may transfer internally to their designated account via API or the Deribit UI—**not** using the investor’s strategy API keys.

### 5.4 Currency and late payment

- Invoices are in **USDC equivalent** at settlement; USDC transfer is preferred.  
- Late payment may trigger a pause on new entries per the investment management agreement.

---

## 6. Settlement cycle (summary)

| Item | Suggested terms |
|------|-----------------|
| Regular settlement | Quarter-end (UTC) |
| Invoice | Within ~10 business days after quarter-end |
| Investor transfer | Into Fee sub-account within ~5 business days after invoice |
| Redemption | Accrued fees on effective redemption date |

---

## Appendix: Does HWM include spot?

**No.** HWM follows NAV_perf only; spot drawdowns do not directly lower HWM. Management fee has no HWM and uses AUM_mgmt including spot.

---

**Generate PDFs**: `python3 scripts/generate_investor_fee_disclosure_pdf.py`
