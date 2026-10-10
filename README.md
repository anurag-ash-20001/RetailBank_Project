# RetailBank — Customer Transaction Analytics

An end-to-end, re-runnable data pipeline for the RetailBank hackathon. It ingests
raw branch / customer / product / transaction feeds, profiles and cleanses them,
models a curated star schema with **SCD Type 2** dimensions, computes all **13
KPIs** from the brief, and absorbs a **Day-2 incremental drop** (new records,
dimension changes, and late transaction corrections) without reprocessing history
or double-counting.

**Cloud choice: AWS** (service mapping in section 8). The local build uses Python
+ pandas + SQLite so it runs anywhere with no cloud account; the same logic maps
onto managed AWS services.

The only dependency is **pandas** — SQL runs on the standard-library `sqlite3`.

---

## 1. Quick start

### Windows (PowerShell, in VS Code)
```powershell
cd RetailBank_Project
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
pip install -r requirements.txt

python generate_sample_data.py        # creates sample data (skip when using real data)
python main.py
```

### macOS / Linux
```bash
cd RetailBank_Project
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 generate_sample_data.py
python3 main.py
```

> **Using the real hackathon data:** skip `generate_sample_data.py`. Put the real
> files in `RetailBank_SourceData/` (branches.csv, customers.csv, products.json,
> transactions.csv) and `RetailBank_SourceData_Day2/` (branches_day2.csv,
> customer_updates_day2.csv, products_day2.json, transactions_day2.csv), then run
> `python main.py`. Processing dates and thresholds are in `config.py`.

---

## 2. Project layout

```
RetailBank_Project/
├── RetailBank_SourceData/        # Day-1 raw feeds (bronze/raw zone)
├── RetailBank_SourceData_Day2/   # Day-2 incremental drop
├── output/
│   ├── curated/                  # dim_* + fact_transaction (gold layer)
│   ├── kpis/                     # KPIs 1-13 (final, after incremental)
│   ├── kpis_day1_baseline/       # KPIs 1-10 on Day-1 only (for comparison)
│   ├── data_quality/             # ingestion metadata, rejects, (scorecard = KPI 10)
│   └── retailbank.db             # SQLite warehouse
├── config.py        # paths, processing dates, thresholds, SCD2 tracked attributes
├── ingest.py        # raw readers + ingestion metadata + defective-JSON recovery
├── clean.py         # per-entity profiling, standardization, validation, quarantine
├── model.py         # conformed dims (surrogate keys, SCD2) + transaction fact
├── kpis.py          # KPIs 1-10 (SQL + pandas)
├── incremental.py   # Day-2 SCD2 upserts, late corrections, KPIs 11-13
├── main.py          # orchestrator
├── generate_sample_data.py
├── requirements.txt
└── README.md
```

---

## 3. Architecture (raw → cleansed → curated → KPI)

```
 RetailBank_SourceData/*            RetailBank_SourceData_Day2/*
          │                                   │
          ▼                                   ▼
   Ingest (bronze)  —— ingestion metadata (file, rows, ts, batch id)
          │
          ▼
   Cleanse / validate  —— quarantine failed rows w/ reason code  ──► data_quality/
          │               (standardize-then-dedupe, referential checks, etc.)
          ▼
   Curated star schema (gold)   →   SQLite warehouse
     dim_customer (SCD2) · dim_product (SCD2) · dim_branch (SCD2)
     dim_account · fact_transaction (grain = 1 row / transaction_id)
          │
          ▼
   KPIs 1-10 (SQL incl. window functions + pandas)   ──► kpis/
          │
          ▼
   Day-2: SCD2 dimension upserts + late fact corrections (idempotent)
          │
          ▼
   Re-run KPIs 1-10 + incremental KPIs 11-13 (deltas vs Day 1) ──► kpis/
```

---

## 4. Data model — grain & keys

| Table | Natural key | Surrogate | History | Notes |
|-------|-------------|-----------|---------|-------|
| `fact_transaction` | `transaction_id` | — | — | **Grain: one row per transaction.** FKs → account_id, branch_id, product_id. Measures: amount, net_amount (refunds netted), is_refund, is_settled. |
| `dim_customer` | `customer_id` | `customer_sk` | **SCD2** on `kyc_status`, `address` | effective_from / effective_to / is_current |
| `dim_product` | `product_id` | `product_sk` | **SCD2** on `price`, `is_active`, `category` | |
| `dim_branch` | `branch_id` | `branch_sk` | **SCD2** on `manager_name`, `region` | |
| `dim_account` | `account_id` | — | rebuilt each run | resolves shared accounts via `primary_customer_id` |

**Shared-account resolution:** when two customers share an `account_id`, the
account's transactions are attributed to the **primary holder** (lowest
customer_id). `dim_account.is_shared` / `num_holders` retain the fact that it's
joint. This is the documented resolution for the "two customers share the same
account_id" case.

---

## 5. Data cleansing & validation (spec 5.2)

Failures are **quarantined** to `data_quality/rejected_records_*.csv` with a reason
code — never silently dropped. Value problems that still leave a usable record are
**kept and flagged** instead.

- **Branches** — standardize region/type casing; dedupe re-sent rows; flag missing
  manager names and invalid contact numbers.
- **Customers** — standardize gender & KYC (mixed casing/abbreviations/blanks);
  validate email/phone (flag); validate DOB (future/implausible → nulled + flagged);
  standardize registration_date across formats; dedupe exact + near-duplicates
  (standardize-then-dedupe collapses trailing-whitespace/casing re-sends).
- **Products** — recover the JSON structural defect (trailing comma / concatenated
  segments); dedupe product_id; correct negative prices (abs + flag); standardize
  category casing; normalize `is_active` (bool/string/missing/null → boolean).
- **Transactions** — parse amounts arriving blank/negative/zero/comma-formatted/
  `₹`- or `$`-prefixed; flag non-INR currency for review; reconcile date vs
  timestamp disagreement (timestamp's date wins, flagged); standardize multiple
  date formats, status, payment_method, is_refund; **referential integrity** on
  account/branch/product → orphans quarantined; **future-dated** beyond the
  processing date quarantined; exact-duplicate and conflicting-`transaction_id`
  rows quarantined.

---

## 6. The 13 KPIs → output files

| # | KPI (spec) | File(s) in `output/kpis/` |
|---|------------|---------------------------|
| 1 | Top-5 customers by **net** transaction volume (refunds netted, ties by count) | `kpi_01_top_customers_net_volume` |
| 2 | Monthly transaction trend per branch & region, **MoM growth %** (window fn) | `kpi_02_monthly_trend_branch_region` |
| 3 | Product-wise revenue contribution + **period-over-period** (quarter) | `kpi_03_product_revenue_contribution` |
| 4 | Dormant / inactive accounts (90d) + **high-risk dormant** (Credit Card/Loan) | `kpi_04_dormant_accounts` |
| 5 | High-value / suspicious monitoring (>₹1L **OR** 3+/10min **OR** 12–5 AM), reason per txn | `kpi_05_suspicious_transactions` |
| 6 | Customer segmentation **RFM-lite** → Platinum/Gold/Silver/Bronze | `kpi_06_customer_segmentation_rfm` |
| 7 | Branch performance ranking within region (`RANK`/`DENSE_RANK`), top & bottom | `kpi_07_branch_rank_within_region`, `kpi_07b_branch_top_bottom_per_region` |
| 8 | KYC risk exposure (% & value of txns by non-Verified customers) | `kpi_08_kyc_risk_exposure` |
| 9 | Refund rate by product and by branch | `kpi_09_refund_rate_by_product`, `kpi_09b_refund_rate_by_branch` |
| 10 | Data Quality Scorecard (received/valid/rejected + top-3 reasons, per file & load) | `kpi_10_data_quality_scorecard` |
| 11 | Day-over-day incremental reconciliation + **deltas to KPI 1 & KPI 7** | `kpi_11_incremental_reconciliation`, `kpi_11a_delta_to_kpi1_net_volume`, `kpi_11b_delta_to_kpi7_branch_rank` |
| 12 | Customer KYC status transition report (SCD2 change detection) | `kpi_12_kyc_status_transitions` |
| 13 | New account activation (first-time-transacting accounts + new customers) | `kpi_13_new_account_activation`, `kpi_13b_new_customers_zero_prior_history` |

---

## 7. Incremental processing (spec 5.4)

- **Dimensions** — new natural keys inserted; a change to a business-meaningful
  (SCD2-tracked) attribute closes the current version and opens a new one
  (history preserved); other changes update the current version in place.
- **Transactions** — a late-arriving row that re-uses an existing `transaction_id`
  **updates the existing fact row**; genuinely new ids are inserted. No duplicate facts.
- **Idempotent** — the curated layer is rebuilt from the cleansed transaction set
  each run, so re-running a batch produces identical output (verified).

---

## 8. AWS service mapping (chosen platform)

| Capability | Local (this build) | AWS |
|-----------|---------------------|-----|
| Landing / raw storage | `RetailBank_SourceData*/` | Amazon S3 (raw/bronze) |
| Data processing (ETL) | `clean.py` / `model.py` (pandas) | AWS Glue (PySpark) |
| Curated warehouse | SQLite (`retailbank.db`) | Amazon Redshift / S3 + Athena + Glue Data Catalog |
| Orchestration | `main.py` | AWS Step Functions / MWAA |
| Event / trigger | manual run | AWS Lambda |
| Data quality | `clean.py` + KPI 10 scorecard | AWS Glue Data Quality |

---

## 9. Key assumptions (stated per spec 10)

- "Transaction value / revenue" = **settled** (`status='completed'`) transactions in
  **INR**; refunds netted via `net_amount`. Non-INR rows are kept and flagged, and
  excluded from INR revenue KPIs.
- Shared accounts attributed to the primary holder (lowest customer_id).
- Future-dated transactions (beyond the batch processing date) are quarantined.
- Negative product prices are corrected (absolute value) and flagged, so products
  stay referenceable by their transactions.
- Processing dates: Day 1 = 2026-03-31, Day 2 = 2026-04-01 (`config.py`).
