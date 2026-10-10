"""
config.py — all paths, processing dates, thresholds and SCD2 settings.
Edit here to point at the real hackathon data or tune business rules.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- Input folders (spec names) ---------------------------------------
DAY1_DIR = os.path.join(BASE_DIR, "RetailBank_SourceData")
DAY2_DIR = os.path.join(BASE_DIR, "RetailBank_SourceData_Day2")

DAY1_FILES = {
    "branches":     "branches.csv",
    "customers":    "customers.csv",
    "products":     "products.json",
    "transactions": "transactions.csv",
}
DAY2_FILES = {
    "branches":     "branches_day2.csv",
    "customers":    "customer_updates_day2.csv",
    "products":     "products_day2.json",
    "transactions": "transactions_day2.csv",
}

# ---- Output -----------------------------------------------------------
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
KPI_DIR    = os.path.join(OUTPUT_DIR, "kpis")
DQ_DIR     = os.path.join(OUTPUT_DIR, "data_quality")
CURATED_DIR = os.path.join(OUTPUT_DIR, "curated")
DB_PATH    = os.path.join(OUTPUT_DIR, "retailbank.db")

# ---- Processing "as-of" dates per batch -------------------------------
# Transactions dated beyond the batch's processing date are quarantined.
PROCESSING_DATE = {
    "DAY1": "2026-03-31",
    "DAY2": "2026-04-01",
}
# SCD2 open/close dates use the batch load date.
LOAD_DATE = {"DAY1": "2026-03-31", "DAY2": "2026-04-01"}
SCD_OPEN_END = "9999-12-31"

# ---- Business rules ---------------------------------------------------
HOME_CURRENCY       = "INR"       # non-home currency rows are flagged for review
DORMANT_DAYS        = 90          # KPI 4: no txns in last N days
HIGH_RISK_CATEGORIES = ("Credit Card", "Loan")   # KPI 4 high-risk dormant
SUSPICIOUS_AMOUNT   = 100_000.0   # KPI 5: amount threshold
BURST_COUNT         = 3           # KPI 5: N+ txns ...
BURST_WINDOW_MIN    = 10          # ... within this many minutes (same account)
NIGHT_START_HOUR    = 0           # KPI 5: 12 AM - 5 AM window
NIGHT_END_HOUR      = 5
TOP_CUSTOMERS_N     = 5           # KPI 1
VERIFIED_KYC        = "verified"  # canonical "good" KYC value (KPI 8)

# Dimension attributes that trigger SCD Type 2 history when they change.
SCD2_TRACKED = {
    "dim_customer": ["kyc_status", "address"],
    "dim_product":  ["price", "is_active", "category"],
    "dim_branch":   ["manager_name", "region"],
}

# Statuses that represent a realised/settled transaction for revenue.
SETTLED_STATUSES = ("completed",)
