"""
kpis.py — the 13 KPIs from spec section 6.

Core KPIs 1-10 run on the SQLite warehouse. KPIs 1 and 7 are also exposed on
their own because KPI 11 (incremental reconciliation) needs their Day-1 vs
Day-1+2 deltas. KPIs 11-13 live in incremental.py.

Revenue / transaction-value conventions (documented assumptions):
  * "Transaction value" = settled (status='completed') transactions in INR.
  * Refunds (is_refund=true) are netted via net_amount for volume KPIs.
  * Customer-level KPIs attribute an account's transactions to its primary
    holder (dim_account.primary_customer_id), resolving shared accounts.
"""
import sqlite3
import pandas as pd
from datetime import date, datetime, timedelta

import config as cfg


def connect(db_path):
    return sqlite3.connect(db_path)


def _q(conn, sql):
    return pd.read_sql_query(sql, conn)


def _load_fact(conn):
    f = _q(conn, "SELECT * FROM fact_transaction")
    for c in ["currency_review", "is_refund", "is_settled"]:
        f[c] = f[c].astype(str).str.lower().isin(["1", "true", "t"])
    return f


# ===========================================================================
# KPI 1 — Top-N customers by net transaction volume (refunds netted)
# ===========================================================================
def kpi1_net_volume(conn, limit=None):
    df = _q(conn, """
        SELECT c.customer_id, c.name,
               ROUND(SUM(f.net_amount),2) AS net_volume,
               COUNT(*) AS txn_count
        FROM fact_transaction f
        JOIN dim_account  a ON f.account_id = a.account_id
        JOIN dim_customer c ON a.primary_customer_id = c.customer_id AND c.is_current = 1
        WHERE f.currency = 'INR' AND f.is_settled = 1
        GROUP BY c.customer_id, c.name
        ORDER BY net_volume DESC, txn_count DESC
    """)
    return df.head(limit) if limit else df


# ===========================================================================
# KPI 2 — Monthly transaction trend per branch & region (MoM growth %)
# ===========================================================================
def kpi2_monthly_trend(conn):
    return _q(conn, """
        WITH m AS (
          SELECT b.region, f.branch_id, b.branch_name, f.txn_month,
                 ROUND(SUM(f.net_amount),2) AS value, COUNT(*) AS volume
          FROM fact_transaction f
          JOIN dim_branch b ON f.branch_id = b.branch_id AND b.is_current = 1
          WHERE f.currency = 'INR' AND f.is_settled = 1
          GROUP BY b.region, f.branch_id, b.branch_name, f.txn_month)
        SELECT region, branch_id, branch_name, txn_month, value, volume,
               ROUND(100.0 * (value - LAG(value) OVER (PARTITION BY branch_id ORDER BY txn_month))
                     / NULLIF(LAG(value) OVER (PARTITION BY branch_id ORDER BY txn_month), 0), 2)
                     AS mom_growth_pct
        FROM m
        ORDER BY region, branch_id, txn_month
    """)


# ===========================================================================
# KPI 3 — Product-wise revenue contribution + period-over-period (quarter)
# ===========================================================================
def kpi3_product_revenue(conn):
    base = _q(conn, """
        SELECT p.product_id, p.product_name, p.category, f.txn_quarter,
               ROUND(SUM(f.net_amount),2) AS revenue
        FROM fact_transaction f
        JOIN dim_product p ON f.product_id = p.product_id AND p.is_current = 1
        WHERE f.currency = 'INR' AND f.is_settled = 1
        GROUP BY p.product_id, p.product_name, p.category, f.txn_quarter
    """)
    if base.empty:
        return base
    total_rev = _q(conn, """
        SELECT p.product_id, ROUND(SUM(f.net_amount),2) AS revenue_total
        FROM fact_transaction f
        JOIN dim_product p ON f.product_id = p.product_id AND p.is_current = 1
        WHERE f.currency='INR' AND f.is_settled=1
        GROUP BY p.product_id""")
    pivot = base.pivot_table(index=["product_id", "product_name", "category"],
                             columns="txn_quarter", values="revenue",
                             aggfunc="sum", fill_value=0.0).reset_index()
    quarters = sorted([c for c in pivot.columns if str(c).startswith(("20", "19"))])
    pivot = pivot.merge(total_rev, on="product_id", how="left")
    grand = pivot["revenue_total"].sum()
    pivot["share_pct"] = (100.0 * pivot["revenue_total"] / grand).round(2) if grand else 0.0
    # period-over-period: latest quarter vs prior quarter
    if len(quarters) >= 2:
        prev, curr = quarters[-2], quarters[-1]
        prev_s = pivot[prev].astype(float)
        growth = (100.0 * (pivot[curr].astype(float) - prev_s) / prev_s).where(prev_s != 0)
        pivot["pop_growth_pct"] = pd.to_numeric(growth, errors="coerce").round(2)
    else:
        pivot["pop_growth_pct"] = pd.NA
    return pivot.sort_values("revenue_total", ascending=False)


# ===========================================================================
# KPI 4 — Dormant / inactive accounts (+ high-risk dormant)
# ===========================================================================
def kpi4_dormant_accounts(conn, processing_date):
    proc = date.fromisoformat(processing_date)
    cutoff = proc - timedelta(days=cfg.DORMANT_DAYS)
    accounts = _q(conn, "SELECT account_id, primary_customer_id FROM dim_account")
    last = _q(conn, """SELECT account_id, MAX(txn_date) AS last_txn_date, COUNT(*) AS txns
                       FROM fact_transaction GROUP BY account_id""")
    cats = _q(conn, """
        SELECT DISTINCT f.account_id, p.category
        FROM fact_transaction f JOIN dim_product p
          ON f.product_id = p.product_id AND p.is_current = 1""")
    hr = cats[cats["category"].isin(cfg.HIGH_RISK_CATEGORIES)]["account_id"].unique()
    cat_map = cats.groupby("account_id")["category"].apply(lambda s: ", ".join(sorted(set(s))))

    df = accounts.merge(last, on="account_id", how="left")
    df["txns"] = df["txns"].fillna(0).astype(int)
    df["last_txn_date"] = df["last_txn_date"].fillna("")
    def days_since(d0):
        return (proc - date.fromisoformat(d0)).days if d0 else None
    df["days_since_last"] = df["last_txn_date"].map(days_since)
    df["is_dormant"] = df.apply(
        lambda r: (r["last_txn_date"] == "") or (date.fromisoformat(r["last_txn_date"]) < cutoff),
        axis=1)
    df["high_risk_dormant"] = df["is_dormant"] & df["account_id"].isin(hr)
    df["categories"] = df["account_id"].map(cat_map).fillna("")
    return df[df["is_dormant"]].sort_values(
        ["high_risk_dormant", "days_since_last"], ascending=[False, False]).reset_index(drop=True)


# ===========================================================================
# KPI 5 — High-value / suspicious transaction monitoring (risk reason per txn)
# ===========================================================================
def kpi5_suspicious(conn):
    f = _load_fact(conn)
    f = f[f["currency"] == "INR"].copy()
    reasons = {tid: [] for tid in f["transaction_id"]}

    # rule A: amount above threshold
    for tid in f.loc[f["amount"] > cfg.SUSPICIOUS_AMOUNT, "transaction_id"]:
        reasons[tid].append(f">₹{int(cfg.SUSPICIOUS_AMOUNT):,} high-value")

    # rule C: late-night window (12 AM - 5 AM)
    night = f[(f["txn_hour"] >= cfg.NIGHT_START_HOUR) & (f["txn_hour"] < cfg.NIGHT_END_HOUR + 1)
              & (f["txn_hour"] <= cfg.NIGHT_END_HOUR)]
    for tid in night["transaction_id"]:
        reasons[tid].append("odd-hour (12AM-5AM)")

    # rule B: >=N txns from same account within a 10-minute window
    f["dt"] = pd.to_datetime(f["txn_datetime"])
    win = timedelta(minutes=cfg.BURST_WINDOW_MIN)
    for acct, grp in f.sort_values("dt").groupby("account_id"):
        times = grp["dt"].tolist(); tids = grp["transaction_id"].tolist()
        lo = 0
        for hi in range(len(times)):
            while times[hi] - times[lo] > win:
                lo += 1
            if hi - lo + 1 >= cfg.BURST_COUNT:
                for k in range(lo, hi + 1):
                    if "rapid burst (3+/10min)" not in reasons[tids[k]]:
                        reasons[tids[k]].append("rapid burst (3+/10min)")

    flagged = {tid: r for tid, r in reasons.items() if r}
    if not flagged:
        return pd.DataFrame(columns=["transaction_id", "account_id", "amount",
                                     "txn_datetime", "status", "risk_reason"])
    out = f[f["transaction_id"].isin(flagged)][
        ["transaction_id", "account_id", "amount", "txn_datetime", "status"]].copy()
    out["risk_reason"] = out["transaction_id"].map(lambda t: "; ".join(flagged[t]))
    return out.sort_values("amount", ascending=False).reset_index(drop=True)


# ===========================================================================
# KPI 6 — Customer segmentation (RFM-lite: Platinum/Gold/Silver/Bronze)
# ===========================================================================
def _tertile_score(series, reverse=False):
    """Rank into 3 tertiles -> score 1..3 (reverse=True gives low values the high score)."""
    r = series.rank(method="first")
    try:
        t = pd.qcut(r, 3, labels=[1, 2, 3]).astype(int)
    except ValueError:                      # too few distinct values
        t = pd.cut(r, 3, labels=[1, 2, 3]).astype(int)
    return (4 - t) if reverse else t


def kpi6_rfm(conn, processing_date):
    proc = date.fromisoformat(processing_date)
    df = _q(conn, """
        SELECT c.customer_id, c.name,
               MAX(f.txn_date) AS last_txn, COUNT(*) AS frequency,
               ROUND(SUM(f.net_amount),2) AS monetary
        FROM fact_transaction f
        JOIN dim_account  a ON f.account_id = a.account_id
        JOIN dim_customer c ON a.primary_customer_id = c.customer_id AND c.is_current = 1
        WHERE f.currency='INR' AND f.is_settled=1
        GROUP BY c.customer_id, c.name""")
    if df.empty:
        return df
    df["recency_days"] = df["last_txn"].map(lambda d0: (proc - date.fromisoformat(d0)).days)
    df["R"] = _tertile_score(df["recency_days"], reverse=True)   # recent -> high
    df["F"] = _tertile_score(df["frequency"])
    df["M"] = _tertile_score(df["monetary"])
    df["rfm_total"] = df["R"] + df["F"] + df["M"]
    def seg(t):
        if t >= 8: return "Platinum"
        if t >= 6: return "Gold"
        if t >= 4: return "Silver"
        return "Bronze"
    df["segment"] = df["rfm_total"].map(seg)
    return df.sort_values("rfm_total", ascending=False).reset_index(drop=True)


# ===========================================================================
# KPI 7 — Branch performance ranking within region
# ===========================================================================
def kpi7_branch_rank(conn):
    return _q(conn, """
        WITH b AS (
          SELECT b.region, f.branch_id, b.branch_name,
                 ROUND(SUM(f.net_amount),2) AS revenue, COUNT(*) AS txns
          FROM fact_transaction f
          JOIN dim_branch b ON f.branch_id = b.branch_id AND b.is_current = 1
          WHERE f.currency='INR' AND f.is_settled=1
          GROUP BY b.region, f.branch_id, b.branch_name)
        SELECT region, branch_id, branch_name, revenue, txns,
               RANK()       OVER (PARTITION BY region ORDER BY revenue DESC) AS rank_in_region,
               DENSE_RANK() OVER (PARTITION BY region ORDER BY revenue DESC) AS dense_rank_in_region,
               COUNT(*)     OVER (PARTITION BY region) AS branches_in_region
        FROM b
        ORDER BY region, rank_in_region
    """)


def kpi7_top_bottom(rank_df):
    if rank_df.empty:
        return rank_df
    rows = []
    for region, g in rank_df.groupby("region"):
        top = g.loc[g["revenue"].idxmax()]
        bot = g.loc[g["revenue"].idxmin()]
        rows.append({"region": region, "position": "TOP", **top[["branch_id", "branch_name", "revenue"]]})
        rows.append({"region": region, "position": "BOTTOM", **bot[["branch_id", "branch_name", "revenue"]]})
    return pd.DataFrame(rows)


# ===========================================================================
# KPI 8 — KYC risk exposure
# ===========================================================================
def kpi8_kyc_exposure(conn):
    df = _q(conn, f"""
        SELECT c.kyc_status,
               COUNT(*) AS txn_count,
               ROUND(SUM(f.net_amount),2) AS txn_value
        FROM fact_transaction f
        JOIN dim_account  a ON f.account_id = a.account_id
        JOIN dim_customer c ON a.primary_customer_id = c.customer_id AND c.is_current = 1
        WHERE f.currency='INR' AND f.is_settled=1
        GROUP BY c.kyc_status""")
    if df.empty:
        return df
    tot_c, tot_v = df["txn_count"].sum(), df["txn_value"].sum()
    df["pct_of_count"] = (100.0 * df["txn_count"] / tot_c).round(2)
    df["pct_of_value"] = (100.0 * df["txn_value"] / tot_v).round(2) if tot_v else 0.0
    df["kyc_risk"] = df["kyc_status"] != cfg.VERIFIED_KYC
    return df.sort_values("txn_value", ascending=False).reset_index(drop=True)


# ===========================================================================
# KPI 9 — Refund rate by product and by branch
# ===========================================================================
def _refund_rate(conn, dim, key, name_col, join):
    return _q(conn, f"""
        SELECT {key}, {name_col},
               COUNT(*) AS total_txns,
               SUM(CASE WHEN f.is_refund=1 THEN 1 ELSE 0 END) AS refund_txns,
               ROUND(SUM(f.amount),2) AS total_value,
               ROUND(SUM(CASE WHEN f.is_refund=1 THEN f.amount ELSE 0 END),2) AS refund_value,
               ROUND(100.0*SUM(CASE WHEN f.is_refund=1 THEN 1 ELSE 0 END)/COUNT(*),2) AS refund_rate_pct
        FROM fact_transaction f
        JOIN {join}
        WHERE f.currency='INR'
        GROUP BY {key}, {name_col}
        ORDER BY refund_rate_pct DESC""")


def kpi9_refund_by_product(conn):
    return _refund_rate(conn, "dim_product", "p.product_id", "p.product_name",
                        "dim_product p ON f.product_id=p.product_id AND p.is_current=1")


def kpi9_refund_by_branch(conn):
    return _refund_rate(conn, "dim_branch", "b.branch_id", "b.branch_name",
                        "dim_branch b ON f.branch_id=b.branch_id AND b.is_current=1")


# ===========================================================================
# KPI 10 — Data Quality Scorecard  (built from cleaning metadata)
# ===========================================================================
def kpi10_dq_scorecard(dq_records):
    """dq_records: list of dq dicts (each already tagged with a 'batch_id')."""
    rows = []
    for d in dq_records:
        top3 = sorted(d["reasons"].items(), key=lambda kv: kv[1], reverse=True)[:3]
        top3_str = "; ".join(f"{r} ({n})" for r, n in top3) if top3 else "-"
        rows.append({
            "batch_id": d.get("batch_id", ""), "entity": d["entity"],
            "records_received": d["received"], "records_valid": d["valid"],
            "records_rejected": d["rejected"], "duplicates_removed": d["duplicates_removed"],
            "pass_rate_pct": round(100.0 * d["valid"] / d["received"], 2) if d["received"] else 0.0,
            "top_3_rejection_reasons": top3_str,
        })
    return pd.DataFrame(rows)


# ===========================================================================
# Orchestrator for baseline KPIs 1-10
# ===========================================================================
def run_core_kpis(db_path, processing_date, dq_records):
    conn = connect(db_path)
    try:
        rank = kpi7_branch_rank(conn)
        out = {
            "kpi_01_top_customers_net_volume": kpi1_net_volume(conn, cfg.TOP_CUSTOMERS_N),
            "kpi_02_monthly_trend_branch_region": kpi2_monthly_trend(conn),
            "kpi_03_product_revenue_contribution": kpi3_product_revenue(conn),
            "kpi_04_dormant_accounts": kpi4_dormant_accounts(conn, processing_date),
            "kpi_05_suspicious_transactions": kpi5_suspicious(conn),
            "kpi_06_customer_segmentation_rfm": kpi6_rfm(conn, processing_date),
            "kpi_07_branch_rank_within_region": rank,
            "kpi_07b_branch_top_bottom_per_region": kpi7_top_bottom(rank),
            "kpi_08_kyc_risk_exposure": kpi8_kyc_exposure(conn),
            "kpi_09_refund_rate_by_product": kpi9_refund_by_product(conn),
            "kpi_09b_refund_rate_by_branch": kpi9_refund_by_branch(conn),
            "kpi_10_data_quality_scorecard": kpi10_dq_scorecard(dq_records),
        }
        return out
    finally:
        conn.close()
