# RetailBank — Customer Transaction Analytics

An end-to-end, re-runnable data pipeline for the RetailBank hackathon. It ingests
raw branch / customer / product / transaction feeds, profiles and cleanses them,
models a curated star schema with **SCD Type 2** dimensions, computes all **13
KPIs** from the brief, and absorbs a **Day-2 incremental drop** (new records,
dimension changes, and late transaction corrections) without reprocessing history
or double-counting.

**Cloud choice: Microsoft Azure.** The pipeline runs as an Azure Container Apps
Job over Azure Blob Storage, with a Streamlit dashboard on Azure Container Apps.

**Live dashboard:** https://retailbank-dashboard.calmstone-b05d2c28.uaenorth.azurecontainerapps.io
(the first load after it has been idle takes 10–20 s while it starts).

The same code runs locally with no cloud account. The pipeline's only dependency
is **pandas**; SQL runs on the standard-library `sqlite3`. See section 9 for the
full Azure deployment procedure.

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

### Dashboard (local)
After `python main.py` has produced `output/`:
```powershell
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py          # opens http://localhost:8501
```
Locally the dashboard reads `output/`. If `AZURE_STORAGE_ACCOUNT_URL` is set, it
reads the latest Azure run from Blob Storage instead, using your `az login`.

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
│
├── azure_run.py            # Azure wrapper: Blob raw → main.py → Blob output
├── Dockerfile              # pipeline image
├── requirements-azure.txt  # azure-identity, azure-storage-blob
├── dashboard/
│   ├── app.py              # Streamlit dashboard (KPIs 1-13, data quality)
│   ├── Dockerfile          # dashboard image
│   └── requirements.txt
├── deploy_azure.ps1        # one-shot Azure provisioning + deployment
├── .github/workflows/build-images.yml   # builds both images → ghcr.io
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

## 8. Azure architecture (chosen platform)

```
  GitHub repo ──push──► GitHub Actions ──build──► ghcr.io images
                                                   │ (pipeline, dashboard)
                                                   ▼ pulled by
 ┌──────────────────────── Azure resource group: rg-retailbank ────────────────────────┐
 │                                                                                     │
 │  Blob Storage                Container Apps environment                             │
 │  ┌──────────────┐  read   ┌──────────────────────────────┐                          │
 │  │ raw/         │────────►│ Job: retailbank-pipeline     │  azure_run.py:           │
 │  │  Day-1 feeds │         │  (manual trigger)            │  download raw → main.py  │
 │  │  Day-2 feeds │         └──────────────┬───────────────┘  → upload output         │
 │  │              │  write                 │                                          │
 │  │ output/      │◄───────────────────────┘                                          │
 │  │  runs/<ts>/  │  read   ┌──────────────────────────────┐                          │
 │  │  latest/     │────────►│ App: retailbank-dashboard    │◄── https (public URL)    │
 │  └──────────────┘         │  Streamlit, scales to zero   │                          │
 │                           └──────────────────────────────┘                          │
 │  Managed identity id-retailbank → "Storage Blob Data Contributor" (no keys/secrets) │
 └─────────────────────────────────────────────────────────────────────────────────────┘
```

| Capability | Local (this build) | Azure (deployed) |
|-----------|---------------------|-----|
| Landing / raw storage | `RetailBank_SourceData*/` | Blob Storage container `raw` |
| Data processing (ETL) | `clean.py` / `model.py` (pandas) | Container Apps Job running the same code |
| Curated + KPI output | `output/` + SQLite | Blob container `output` (`runs/<timestamp>/` history + `latest/`) |
| Orchestration / trigger | `python main.py` | `az containerapp job start` (manual; can be scheduled) |
| Reporting | CSV files | Streamlit dashboard on Container Apps |
| Security | n/a | User-assigned managed identity + Azure RBAC; no storage keys anywhere |
| Logs | console | Log Analytics workspace (auto-created) |
| Data quality | `clean.py` + KPI 10 scorecard | same, shown on the dashboard's *Data quality* tab |

For production scale, the same layers map to Azure Data Factory (orchestration),
Azure Databricks / Synapse Spark (processing) and Azure SQL / Synapse (warehouse).

---

## 9. Deploying on Azure — complete procedure

The whole deployment is scripted in [`deploy_azure.ps1`](deploy_azure.ps1). You can
re-run it safely: it reuses existing resources and updates the apps to the latest
images.

### 9.1 Prerequisites

| Need | How |
|------|-----|
| Azure subscription | Any subscription works, including **Azure for Students** (see 9.9 for its limits). |
| Azure CLI | `winget install Microsoft.AzureCLI` (Windows) or https://aka.ms/installazurecli. In VS Code, restart VS Code afterwards or call it by full path: `& "C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"`. |
| GitHub repo | This code pushed to a **public** GitHub repository, so the built images are public. |
| Docker | **Not needed.** GitHub Actions builds the images. |

### 9.2 Sign in to Azure

```powershell
az login                                   # opens a browser
az account show --query "{name:name, id:id}" -o table   # confirm the subscription
# if you have several: az account set --subscription "<name or id>"
```

Check which regions your subscription allows. Student subscriptions are restricted:
```powershell
az policy assignment list --query "[].parameters.listOfAllowedLocations.value" -o json
```

### 9.3 Build the container images (GitHub Actions)

Pushing to `main` runs [`.github/workflows/build-images.yml`](.github/workflows/build-images.yml),
which builds and publishes:

- `ghcr.io/<github-user>/retailbank-pipeline:latest` (and `:<commit-sha>`)
- `ghcr.io/<github-user>/retailbank-dashboard:latest` (and `:<commit-sha>`)

```powershell
git push origin main
```

Wait for the run to show a green tick under the repo's **Actions** tab (about 2–3 min).
A public repo publishes public images. If yours are private, open
GitHub → your profile → **Packages** → each package → **Package settings** →
**Change visibility → Public**.

### 9.4 Provision and deploy

From the project folder:

```powershell
# allowed region for your subscription; images from your GitHub account
.\deploy_azure.ps1 -Location uaenorth -Registry ghcr.io/<github-user>
```

*(If PowerShell blocks the script: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.)*

The script runs these steps in order (about 5–10 min the first time):

1. Installs the `containerapp` CLI extension and registers the Azure resource providers.
2. Creates resource group **`rg-retailbank`**.
3. Creates a storage account **`retailbank<suffix>`** with blob containers **`raw`** and **`output`**.
   The suffix is derived from your subscription ID, so it's unique and stable.
4. Grants **you** the *Storage Blob Data Contributor* role, so you can upload with `--auth-mode login`.
5. Creates the managed identity **`id-retailbank`** and grants it the same role on the storage account.
6. Creates the Container Apps environment **`cae-retailbank`** in *WorkloadProfiles* mode,
   with an auto-created Log Analytics workspace.
7. Creates the job **`retailbank-pipeline`** (manual trigger, 1 vCPU / 2 GiB, 30-min timeout).
8. Creates the dashboard app **`retailbank-dashboard`** (external HTTPS ingress on port 8501,
   0–1 replicas).

At the end it prints the dashboard URL, the storage account name, and the
commands for the next two steps.

### 9.5 Upload the input data

```powershell
$sa = "retailbank<suffix>"     # printed by the deploy script
az storage blob upload-batch --account-name $sa -d raw/RetailBank_SourceData      -s RetailBank_SourceData      --auth-mode login --overwrite
az storage blob upload-batch --account-name $sa -d raw/RetailBank_SourceData_Day2 -s RetailBank_SourceData_Day2 --auth-mode login --overwrite
```

Run `python generate_sample_data.py` first if you're using sample data. If the
upload says *"not authorized"*, wait 1–2 min for the role assignment to take
effect and retry.

### 9.6 Run the pipeline

```powershell
az containerapp job start -g rg-retailbank -n retailbank-pipeline

# watch status (Running → Succeeded, ~1 min)
az containerapp job execution list -g rg-retailbank -n retailbank-pipeline `
    --query "[].{name:name, status:properties.status, start:properties.startTime}" -o table
```

Results land in the `output` container under `runs/<UTC timestamp>/` (kept per run)
and `latest/` (overwritten each run). To download them:

```powershell
az storage blob download-batch --account-name $sa -s output -d .\azure_output --pattern "latest/*" --auth-mode login
```

### 9.7 Open the dashboard

Open the URL printed by the script:
```powershell
az containerapp show -g rg-retailbank -n retailbank-dashboard --query properties.configuration.ingress.fqdn -o tsv
```

The dashboard reads `output/latest/` and caches it for 5 minutes. After a new
pipeline run, click **Refresh data**. The tabs are Customers, Branches & regions,
Products, Risk & KYC, Data quality and Day-2 changes.

### 9.8 Updating after code changes

1. `git push`. GitHub Actions rebuilds both images.
2. When the Actions run is green, re-run `.\deploy_azure.ps1 -Location uaenorth -Registry ghcr.io/<github-user>`.
   This creates a new dashboard revision and points the job at the new image.
   To pin an exact build, add `-ImageTag <commit-sha>`.
3. Start the job again (9.6) if the pipeline logic changed.

To process new data, upload the new files to `raw/` (9.5) and start the job (9.6).

### 9.9 Troubleshooting & Azure for Students notes

| Symptom | Cause / fix |
|---------|-------------|
| `RequestDisallowedByPolicy` / region not allowed | Student subscriptions only allow certain regions. Ours allowed `malaysiawest, polandcentral, indonesiacentral, eastasia, uaenorth`. Pass one with `-Location`. |
| `TasksOperationsNotAllowed` from `az acr build` | Azure for Students blocks Azure Container Registry builds. This is why the images are built by GitHub Actions and pulled from ghcr.io. |
| `'Job' resources are not supported on express environments` | Newer CLIs create *Express* environments by default. The script passes `--environment-mode WorkloadProfiles`. Delete an express environment with `az containerapp env delete -g rg-retailbank -n cae-retailbank --yes` and re-run. |
| `az` not recognized in the VS Code terminal | Restart VS Code after installing the CLI, or use the full path (9.1). |
| Upload or blob access *not authorized* | The RBAC role assignment hasn't taken effect yet. Wait 1–2 min and retry. |
| Dashboard shows *"No pipeline output found yet"* | Run the job (9.6), then click **Refresh data**. |
| Job failed | Check its logs: Azure portal → `retailbank-pipeline` → **Execution history** → **Console logs**. |
| Dashboard errors | `az containerapp logs show -g rg-retailbank -n retailbank-dashboard --tail 50` |
| Image pull failed | The ghcr.io package is private. Make it public (9.3). |

### 9.10 Cost and clean-up

Expected cost is close to **$0/month** at hackathon data sizes:

- The job only consumes resources while it runs (about 1 min).
- The dashboard scales to zero when idle.
- Blob Storage and Log Analytics cost cents.
- No container registry is used.

To remove everything:
```powershell
az group delete -n rg-retailbank --yes
```

> **Access:** the dashboard URL is public. The data is synthetic. For real data,
> enable Container Apps authentication (Microsoft sign-in) on `retailbank-dashboard`.

---

## 10. Key assumptions (stated per spec 10)

- "Transaction value / revenue" = **settled** (`status='completed'`) transactions in
  **INR**; refunds netted via `net_amount`. Non-INR rows are kept and flagged, and
  excluded from INR revenue KPIs.
- Shared accounts attributed to the primary holder (lowest customer_id).
- Future-dated transactions (beyond the batch processing date) are quarantined.
- Negative product prices are corrected (absolute value) and flagged, so products
  stay referenceable by their transactions.
- Processing dates: Day 1 = 2026-03-31, Day 2 = 2026-04-01 (`config.py`).
