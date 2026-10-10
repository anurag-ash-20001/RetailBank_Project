"""
incremental.py — Day-2 processing (spec 5.4) and KPIs 11-13.

  * Dimension changes are applied as SCD Type 2 upserts: a business-meaningful
    change (config.SCD2_TRACKED) closes the current version and opens a new one;
    a non-tracked change updates the current version in place; a new natural key
    is inserted.
  * Late-arriving transaction corrections (a re-used transaction_id) UPDATE the
    existing fact row rather than adding a new fact — no double counting.
  * The whole thing is rebuilt from the cleaned transaction set each run, so a
    given batch is idempotent / re-runnable without creating duplicates.
"""
import pandas as pd

import config as cfg
import clean as cl
from model import build_fact, build_dim_account


def _norm(v):
    """Normalize a value for attribute comparison (price as rounded float)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return v
    return str(v).strip().lower()


def apply_scd2(current, incoming, nk, sk_col, tracked, load_date):
    """
    current  : existing dimension with SCD2 columns.
    incoming : cleaned Day-2 rows (business columns only, no SCD2 columns).
    Returns (merged_dim, stats, transitions, new_keys).
    """
    cur = current.copy()
    rows_new, transitions, new_keys = [], [], []
    stats = {"new": 0, "scd2_changed": 0, "updated_in_place": 0, "unchanged": 0}
    next_sk = int(cur[sk_col].max()) + 1
    live = {cur.at[i, nk]: i for i in cur.index[cur["is_current"] == 1]}

    def make_version(inc):
        nonlocal next_sk
        row = inc.to_dict()
        row[sk_col] = next_sk; next_sk += 1
        row["effective_from"] = load_date
        row["effective_to"] = cfg.SCD_OPEN_END
        row["is_current"] = 1
        return row

    for _, inc in incoming.iterrows():
        key = inc[nk]
        if key not in live:
            rows_new.append(make_version(inc)); stats["new"] += 1; new_keys.append(key)
            continue
        i = live[key]
        changed = [a for a in tracked if a in incoming.columns and _norm(cur.at[i, a]) != _norm(inc[a])]
        if changed:
            if "kyc_status" in changed:
                transitions.append({
                    nk: key, "name": cur.at[i, "name"] if "name" in cur.columns else "",
                    "old_kyc_status": cur.at[i, "kyc_status"],
                    "new_kyc_status": inc["kyc_status"], "change_date": load_date})
            cur.at[i, "effective_to"] = load_date
            cur.at[i, "is_current"] = 0
            rows_new.append(make_version(inc)); stats["scd2_changed"] += 1
        else:
            touched = False
            for a in incoming.columns:
                if a in cur.columns and a not in (sk_col, "effective_from", "effective_to", "is_current"):
                    if _norm(cur.at[i, a]) != _norm(inc[a]):
                        touched = True
                    cur.at[i, a] = inc[a]
            stats["updated_in_place" if touched else "unchanged"] += 1

    merged = pd.concat([cur, pd.DataFrame(rows_new)], ignore_index=True) if rows_new else cur
    return merged, stats, transitions, new_keys


def upsert_transactions(clean_txn_day1, clean_txn_day2):
    """Correction = reused transaction_id -> replace the Day-1 row. New id -> append."""
    day1_ids = set(clean_txn_day1["transaction_id"])
    is_corr = clean_txn_day2["transaction_id"].isin(day1_ids)
    corrections = clean_txn_day2[is_corr].copy()
    new_txns = clean_txn_day2[~is_corr].copy()
    kept = clean_txn_day1[~clean_txn_day1["transaction_id"].isin(corrections["transaction_id"])]
    merged = pd.concat([kept, corrections, new_txns], ignore_index=True)
    merged = merged.drop_duplicates(subset=["transaction_id"], keep="last")  # safety net
    return merged, corrections, new_txns


def process_day2(dim_customer1, dim_product1, dim_branch1, clean_txn1, raw2):
    """Apply the full Day-2 batch. Returns a result dict for main to load + report."""
    load_date = cfg.LOAD_DATE["DAY2"]
    proc_date = cfg.PROCESSING_DATE["DAY2"]

    # --- clean Day-2 dimension feeds ---
    cb2, rb2, dqb = cl.clean_branches(raw2["branches"]) if raw2.get("branches") is not None \
        else (pd.DataFrame(), pd.DataFrame(), None)
    cc2, rc2, dqc = cl.clean_customers(raw2["customers"])
    cp2, rp2, dqp = cl.clean_products(raw2["products"])

    # --- SCD2 merge dimensions ---
    dim_branch2, st_b, _, _ = (apply_scd2(dim_branch1, cb2, "branch_id", "branch_sk",
                                          cfg.SCD2_TRACKED["dim_branch"], load_date)
                               if len(cb2) else (dim_branch1, {"new":0,"scd2_changed":0,
                                                 "updated_in_place":0,"unchanged":0}, [], []))
    dim_customer2, st_c, kyc_trans, new_cust = apply_scd2(
        dim_customer1, cc2, "customer_id", "customer_sk",
        cfg.SCD2_TRACKED["dim_customer"], load_date)
    dim_product2, st_p, _, _ = apply_scd2(
        dim_product1, cp2, "product_id", "product_sk",
        cfg.SCD2_TRACKED["dim_product"], load_date)

    # --- rebuild account dim from CURRENT customers (captures new accounts) ---
    cur_customers = dim_customer2[dim_customer2["is_current"] == 1]
    dim_account2 = build_dim_account(cur_customers)

    # --- clean Day-2 transactions against UPDATED dims ---
    accounts = set(dim_account2["account_id"])
    branch_ids = set(dim_branch2.loc[dim_branch2["is_current"] == 1, "branch_id"])
    product_ids = set(dim_product2.loc[dim_product2["is_current"] == 1, "product_id"])
    ctx2, rtx2, dqt = cl.clean_transactions(raw2["transactions"], accounts, branch_ids,
                                            product_ids, proc_date)

    # --- upsert transactions, rebuild fact ---
    merged_clean, corrections, new_txns = upsert_transactions(clean_txn1, ctx2)
    fact_final = build_fact(merged_clean)

    # Day-2 DQ records (tagged)
    dq_day2 = []
    for d in [dqb, dqc, dqp, dqt]:
        if d:
            d = dict(d); d["batch_id"] = "DAY2"; dq_day2.append(d)
    rejects_day2 = pd.concat([r for r in [rb2, rc2, rp2, rtx2] if r is not None and not r.empty],
                             ignore_index=True) if any(len(r) for r in [rb2, rc2, rp2, rtx2]) \
        else pd.DataFrame()

    return {
        "dim_customer": dim_customer2, "dim_product": dim_product2,
        "dim_branch": dim_branch2, "dim_account": dim_account2,
        "merged_clean_txn": merged_clean, "fact_final": fact_final,
        "corrections": corrections, "new_txns": new_txns,
        "scd_stats": {"branch": st_b, "customer": st_c, "product": st_p},
        "kyc_transitions": kyc_trans, "new_customers": new_cust,
        "dq_day2": dq_day2, "rejects_day2": rejects_day2,
        "clean_txn_day2_valid": ctx2,
    }


# ===========================================================================
# KPI 11 — Day-over-day incremental reconciliation (+ deltas to KPI1 & KPI7)
# ===========================================================================
def kpi11_reconciliation(res, fact_day1, kpi1_day1, kpi1_final, kpi7_day1, kpi7_final):
    scd = res["scd_stats"]
    day2_rejected = int(len(res["rejects_day2"])) if res["rejects_day2"] is not None else 0
    summary = pd.DataFrame([{
        "day2_txn_valid": len(res["clean_txn_day2_valid"]),
        "day2_rows_rejected_all_entities": day2_rejected,
        "new_transactions_inserted": len(res["new_txns"]),
        "transaction_corrections_applied": len(res["corrections"]),
        "fact_rows_before": len(fact_day1),
        "fact_rows_after": len(res["fact_final"]),
        "customers_new": scd["customer"]["new"],
        "customers_scd2_versioned": scd["customer"]["scd2_changed"],
        "customers_updated_in_place": scd["customer"]["updated_in_place"],
        "products_new": scd["product"]["new"],
        "products_scd2_versioned": scd["product"]["scd2_changed"],
        "branches_new": scd["branch"]["new"],
        "branches_scd2_versioned": scd["branch"]["scd2_changed"],
    }])

    # delta to KPI 1 (net volume per customer)
    d1 = kpi1_day1.rename(columns={"net_volume": "net_volume_day1", "txn_count": "txns_day1"})
    df = kpi1_final.rename(columns={"net_volume": "net_volume_after", "txn_count": "txns_after"}) \
        .merge(d1[["customer_id", "net_volume_day1", "txns_day1"]], on="customer_id", how="left")
    df["net_volume_day1"] = df["net_volume_day1"].fillna(0.0)
    df["volume_delta"] = (df["net_volume_after"] - df["net_volume_day1"]).round(2)
    kpi1_delta = df[df["volume_delta"] != 0][
        ["customer_id", "name", "net_volume_day1", "net_volume_after", "volume_delta"]] \
        .sort_values("volume_delta", key=lambda s: s.abs(), ascending=False)

    # delta to KPI 7 (branch revenue + rank within region)
    b1 = kpi7_day1[["branch_id", "revenue", "rank_in_region"]].rename(
        columns={"revenue": "revenue_day1", "rank_in_region": "rank_day1"})
    bf = kpi7_final[["branch_id", "branch_name", "region", "revenue", "rank_in_region"]].rename(
        columns={"revenue": "revenue_after", "rank_in_region": "rank_after"})
    kpi7_delta = bf.merge(b1, on="branch_id", how="left")
    kpi7_delta["revenue_delta"] = (kpi7_delta["revenue_after"]
                                   - kpi7_delta["revenue_day1"].fillna(0)).round(2)
    kpi7_delta["rank_change"] = kpi7_delta["rank_day1"] - kpi7_delta["rank_after"]
    kpi7_delta = kpi7_delta.sort_values(["region", "rank_after"])

    return summary, kpi1_delta, kpi7_delta


# ===========================================================================
# KPI 12 — Customer KYC status transition report
# ===========================================================================
def kpi12_kyc_transitions(res):
    rows = [t for t in res["kyc_transitions"] if _norm(t["old_kyc_status"]) != _norm(t["new_kyc_status"])]
    if not rows:
        return pd.DataFrame(columns=["customer_id", "name", "old_kyc_status",
                                     "new_kyc_status", "change_date"])
    return pd.DataFrame(rows)[["customer_id", "name", "old_kyc_status",
                               "new_kyc_status", "change_date"]]


# ===========================================================================
# KPI 13 — New account activation
# ===========================================================================
def kpi13_new_activation(res, fact_day1):
    day1_accts = set(fact_day1["account_id"])
    d2 = res["clean_txn_day2_valid"]
    d2_accts = set(d2["account_id"])
    new_active = sorted(d2_accts - day1_accts)   # accounts transacting for the first time

    acct_dim = res["dim_account"].set_index("account_id")
    rows = []
    for acct in new_active:
        g = d2[d2["account_id"] == acct]
        primary = acct_dim.at[acct, "primary_customer_id"] if acct in acct_dim.index else ""
        rows.append({
            "account_id": acct, "primary_customer_id": primary,
            "first_txn_date": g["txn_date"].min(), "day2_txns": len(g),
            "newly_onboarded_customer": primary in set(res["new_customers"]),
        })
    activated = pd.DataFrame(rows)

    # newly onboarded customers with zero prior transaction history
    new_cust_rows = []
    cur_cust = res["dim_customer"][res["dim_customer"]["is_current"] == 1]
    for cid in res["new_customers"]:
        c = cur_cust[cur_cust["customer_id"] == cid]
        if c.empty:
            continue
        acct = c.iloc[0]["account_id"]
        had_prior = acct in day1_accts
        new_cust_rows.append({"customer_id": cid, "name": c.iloc[0]["name"],
                              "account_id": acct, "had_prior_txn_history": had_prior,
                              "transacted_day2": acct in d2_accts})
    new_customers = pd.DataFrame(new_cust_rows)
    return activated, new_customers
