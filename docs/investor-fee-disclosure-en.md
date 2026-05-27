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
| Collection | Dedicated **Fee sub-account** + **main-account withdrawal** | Manager converts spot profit to USDC/USDT, transfers to Fee sub for reconciliation; investor withdraws from main account to manager's address after confirmation |

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

## 5. Collection: Fee sub-account reconciliation + main-account withdrawal

Management and performance fees are **not** deducted directly from strategy sub-accounts.

### 5.1 What is the Fee sub-account?

- A separate Deribit **sub-account** (suggested name: `fee_acc`; at least 5 characters—`fee` is rejected) used as a **reconciliation rail** after quarterly settlement.  
- **Strategy bots connect only to strategy sub-accounts**; they do **not** trade on the Fee sub-account.  
- Fee sub-account balance is excluded from NAV_perf / HWM.

### 5.2 Quarterly collection workflow

1. **Invoice**: After quarter-end, the manager issues a USDC-equivalent invoice (management + performance fee detail).  
2. **Convert and transfer (manager)**: If spot profits must be converted to pay fees, the manager **trades** the relevant portion to **USDC or USDT** in the strategy sub-account, then **internal-transfers** to the Fee sub-account via the **strategy** API (Wallet=read_write).  
3. **Reconciliation (both parties)**: The manager verifies the Fee sub-account balance via API (Account=read); the investor confirms the balance matches the invoice in the Deribit UI.  
4. **Payment (investor)**: After confirmation, the investor **withdraws** from the **main account** to the manager's specified on-chain address (currency, chain, and amount per invoice and manager instructions).

### 5.3 Investor obligations

1. Create the Fee sub-account separately from strategy subs.  
2. Create a **dedicated API key** on the Fee sub-account (see 5.4) and deliver it securely to the manager (for balance verification).  
3. After receiving the invoice, reconcile with the manager and confirm in writing or by agreed channel.  
4. **Withdraw from the main account** to the manager's specified address to complete payment.  
5. **Strategy** API keys must enable **Wallet read/write** (internal transfer to Fee sub-account). **Main-account** API keys must **not** be delivered to the manager.

### 5.4 API permissions (strategy sub-account vs Fee sub-account)

| Permission | Strategy sub-account | Fee sub-account |
|------------|----------------------|-----------------|
| Read | ✅ Required | ✅ Required |
| Trade | ✅ Required | ❌ Off |
| Wallet read/write | ✅ **Required** (API transfer to Fee sub) | ❌ **Off** |

The manager uses **strategy** API wallet permissions for internal transfers; the **Fee** sub-account API is Account=read only for balance verification. **Actual collection** is completed by the investor withdrawing from the **main account**.

### 5.5 Currency and late payment

- Invoices are in **USDC equivalent** at settlement; the reconciliation sub-account should hold **USDC or USDT** to avoid conversion disputes.  
- Late reconciliation confirmation or failure to pay from the main account may trigger a pause on new entries per the investment management agreement.

---

## 6. Settlement cycle (summary)

| Item | Suggested terms |
|------|-----------------|
| Regular settlement | Quarter-end (UTC) |
| Invoice | Within ~10 business days after quarter-end |
| Investor payment | Within ~5 business days after reconciliation, withdraw from **main account** to manager's address |
| Redemption | Accrued fees on effective redemption date |

---

## Appendix: Does HWM include spot?

**No.** HWM follows NAV_perf only; spot drawdowns do not directly lower HWM. Management fee has no HWM and uses AUM_mgmt including spot.

---

**Generate PDFs**: `python3 scripts/generate_investor_fee_disclosure_pdf.py`
