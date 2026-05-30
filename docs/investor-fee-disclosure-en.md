# Investor Fee Disclosure

**Deribit Options Program** | Version 1.2 | Performance fee 10% | Management fee 1.0% p.a.

> Binding terms are governed by the signed investment management agreement.

---

## 1. Fee overview

| Item | Rate / rule | Notes |
|------|-------------|-------|
| Management fee | 1.0% p.a. (quarterly) | On average AUM_mgmt |
| Performance fee | 10% | Only on profits above HWM |
| Billing unit | Per investor (consolidated) | All **strategy** sub-accounts merged for NAV |
| High water mark | Required | **Tied to NAV_perf only** |
| Collection | **Quarter-end spot sale** + **external on-chain payment** | Manager trades spot to USDC/USDT/USDE in strategy subs; investor pays to manager's external addresses after confirmation |

---

## 2. Two bases

### NAV_perf (performance NAV) — excludes collateral spot

- **Definition**: Sum of USDC-equivalent equity across **strategy** sub-accounts minus agreed collateral spot  
- **Used for**: Period P&L, HWM, 10% performance fee  
- **Excludes**: Investor-owned BTC/ETH collateral and its price moves  

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

## 5. Collection: quarter-end conversion + external payment

Management and performance fees are **not** deducted directly from strategy sub-accounts.

### 5.1 Manager (after settlement)

1. **Invoice**: USDC-equivalent invoice with management and performance fee detail.  
2. **Spot sale (if needed)**: Trade relevant spot profits to **USDC, USDT, or USDE** in the **strategy** sub-account (stablecoins remain there for Deribit UI reconciliation).  
3. **Reconciliation**: Confirm the invoice with the investor.

The manager does **not** require the **main-account** API and does **not** perform Deribit **internal transfers** between sub-accounts (main-account authorization required).

### 5.2 Investor (payment)

1. Receive and confirm the invoice with the manager.  
2. Send the amount to the manager's **external on-chain addresses** (see `config/platform/fee-payout-addresses.toml`; a formal list is provided at onboarding).  
3. Typical assets: **USDC, USDT, USDE**; **network** must match manager instructions (often Ethereum ERC-20).  
4. Payment may be via Deribit **main-account withdraw** or an external wallet/exchange.

### 5.3 Strategy sub-account API permissions

| Permission | Strategy sub-account |
|------------|----------------------|
| Read | ✅ Required |
| Trade | ✅ Required (options + quarter-end spot sale to stablecoins) |
| Wallet | ❌ **Off** (manager has no main-account API) |

### 5.4 Currency and late payment

- Invoices are in **USDC equivalent** at settlement; on-chain payment should use the specified **USDC, USDT, or USDE** to avoid disputes.  
- Late reconciliation or failure to pay on-chain may trigger a pause on new entries per the investment management agreement.

---

## 6. Settlement cycle (summary)

| Item | Suggested terms |
|------|-----------------|
| Regular settlement | Quarter-end (UTC) |
| Invoice | Within ~10 business days after quarter-end |
| Investor payment | Within ~5 business days after reconciliation, to manager's external addresses |
| Redemption | Accrued fees on effective redemption date |

---

## Appendix: Does HWM include spot?

**No.** HWM follows NAV_perf only; spot drawdowns do not directly lower HWM. Management fee has no HWM and uses AUM_mgmt including spot.

---

**Generate PDFs**: `python3 scripts/generate_investor_fee_disclosure_pdf.py`
