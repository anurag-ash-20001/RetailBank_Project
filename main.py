"""
main.py — RetailBank Customer Transaction Analytics pipeline (end-to-end).

Run:
    python generate_sample_data.py     # once, to create sample data (skip for real data)
    python main.py

Stages:
    Day 1  : ingest -> clean/validate/quarantine -> curated model (dims + fact)
             -> load SQLite warehouse -> baseline KPIs 1-10
    Day 2  : SCD2 dimension upserts + late transaction corrections (idempotent)
             -> re-load warehouse -> re-run KPIs 1-10 -> incremental KPIs 11-13

Outputs (output/):
    curated/          dim_* and fact_transaction (curated layer, reflects Day1+Day2)
    kpis/             KPIs 1-13 (final, after incremental)
    kpis_day1_baseline/  KPIs 1-10 on Day-1 data only (for comparison)
    data_quality/     ingestion metadata, rejected records, DQ scorecard
    retailbank.db     SQLite warehouse
"""
import os
import shutil

import pandas as pd

import config as cfg
import clean as cl
import kpis as K
from ingest import ingest_batch
from model import build_dimensions, build_fact, load_warehouse
import incremental as inc


def log(m=""): print(m, flush=True)
def section(t): log("\n" + "=" * 70); log(t); log("=" * 70)


def save(df, folder, name):
    os.makedirs(folder, exist_ok=True)
    df.to_csv(os.path.join(folder, name + ".csv"), index=False)


def save_kpis(kpi_dict, folder):
    for name, df in kpi_dict.items():
        save(df, folder, name)


def clean_day1(raw):
    cb, rb, dqb = cl.clean_branches(raw["branches"])
    cc, rc, dqc = cl.clean_customers(raw["customers"])
    cp, rp, dqp = cl.clean_products(raw["products"])
    accounts = set(cc["account_id"]); branch_ids = set(cb["branch_id"]); product_ids = set(cp["product_id"])
    ctx, rtx, dqt = cl.clean_transactions(raw["transactions"], accounts, branch_ids,
                                          product_ids, cfg.PROCESSING_DATE["DAY1"])
    dq = []
    for d in [dqb, dqc, dqp, dqt]:
        d = dict(d); d["batch_id"] = "DAY1"; dq.append(d)
    rejects = pd.concat([r for r in [rb, rc, rp, rtx] if not r.empty], ignore_index=True) \
        if any(len(r) for r in [rb, rc, rp, rtx]) else pd.DataFrame()
    return (cb, cc, cp, ctx), dq, rejects


def main():
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    if os.path.exists(cfg.DB_PATH):
        os.remove(cfg.DB_PATH)

    # ---------------- DAY 1 ----------------
    section("DAY 1 — ingest")
    raw1, meta1 = ingest_batch(cfg.DAY1_DIR, cfg.DAY1_FILES, "DAY1")
    if raw1["transactions"] is None:
        log("ERROR: no source files. Run `python generate_sample_data.py` first.")
        return
    for _, r in meta1.iterrows():
        log(f"  {r['entity']:<12} {r['row_count']:>5} rows  [{r['status']}]")

    section("DAY 1 — clean / validate / quarantine")
    (cb, cc, cp, ctx), dq_day1, rejects_day1 = clean_day1(raw1)
    for d in dq_day1:
        log(f"  {d['entity']:<12} received={d['received']:>4} valid={d['valid']:>4} "
            f"rejected={d['rejected']:>3}  flags={d['flags']}")

    section("DAY 1 — curated model (dims + fact) + warehouse")
    dim_customer1, dim_product1, dim_branch1, dim_account1 = build_dimensions(
        cb, cc, cp, cfg.LOAD_DATE["DAY1"])
    fact_day1 = build_fact(ctx)
    load_warehouse(cfg.DB_PATH, {
        "dim_customer": dim_customer1, "dim_product": dim_product1,
        "dim_branch": dim_branch1, "dim_account": dim_account1,
        "fact_transaction": fact_day1})
    for nm, df in [("dim_customer", dim_customer1), ("dim_product", dim_product1),
                   ("dim_branch", dim_branch1), ("dim_account", dim_account1),
                   ("fact_transaction", fact_day1)]:
        log(f"  {nm:<16} {len(df):>5} rows")

    section("DAY 1 — baseline KPIs 1-10")
    core_day1 = K.run_core_kpis(cfg.DB_PATH, cfg.PROCESSING_DATE["DAY1"], dq_day1)
    save_kpis(core_day1, os.path.join(cfg.OUTPUT_DIR, "kpis_day1_baseline"))
    conn = K.connect(cfg.DB_PATH)
    kpi1_day1_full = K.kpi1_net_volume(conn)
    conn.close()
    kpi7_day1 = core_day1["kpi_07_branch_rank_within_region"]
    top = core_day1["kpi_01_top_customers_net_volume"]
    log(f"  Top customer (net volume): {top.iloc[0]['name']} = {top.iloc[0]['net_volume']:,}")
    log(f"  Suspicious txns: {len(core_day1['kpi_05_suspicious_transactions'])} | "
        f"Dormant accounts: {len(core_day1['kpi_04_dormant_accounts'])}")

    # ---------------- DAY 2 ----------------
    section("DAY 2 — ingest + incremental (SCD2 upserts, late corrections)")
    raw2, meta2 = ingest_batch(cfg.DAY2_DIR, cfg.DAY2_FILES, "DAY2")
    if raw2["transactions"] is None:
        log("  No Day-2 batch found — skipping incremental stage.")
        _finalize_outputs(meta1, pd.DataFrame(), rejects_day1, pd.DataFrame(),
                          dim_customer1, dim_product1, dim_branch1, dim_account1, fact_day1)
        return
    for _, r in meta2.iterrows():
        log(f"  {r['entity']:<12} {r['row_count']:>5} rows  [{r['status']}]")

    res = inc.process_day2(dim_customer1, dim_product1, dim_branch1, ctx, raw2)
    load_warehouse(cfg.DB_PATH, {
        "dim_customer": res["dim_customer"], "dim_product": res["dim_product"],
        "dim_branch": res["dim_branch"], "dim_account": res["dim_account"],
        "fact_transaction": res["fact_final"]})
    sc = res["scd_stats"]
    log(f"  Customers: {sc['customer']['new']} new, {sc['customer']['scd2_changed']} SCD2-versioned, "
        f"{sc['customer']['updated_in_place']} updated-in-place")
    log(f"  Products:  {sc['product']['new']} new, {sc['product']['scd2_changed']} SCD2-versioned")
    log(f"  Branches:  {sc['branch']['new']} new, {sc['branch']['scd2_changed']} SCD2-versioned")
    log(f"  Transactions: {len(res['new_txns'])} new, {len(res['corrections'])} corrections applied")
    log(f"  Fact rows: {len(fact_day1)} -> {len(res['fact_final'])}")

    section("DAY 2 — re-run KPIs 1-10 on reconciled curated layer")
    dq_all = dq_day1 + res["dq_day2"]
    core_final = K.run_core_kpis(cfg.DB_PATH, cfg.PROCESSING_DATE["DAY2"], dq_all)
    save_kpis(core_final, cfg.KPI_DIR)

    section("DAY 2 — incremental KPIs 11-13")
    conn = K.connect(cfg.DB_PATH)
    kpi1_final_full = K.kpi1_net_volume(conn)
    conn.close()
    kpi7_final = core_final["kpi_07_branch_rank_within_region"]

    k11_summary, k11_kpi1_delta, k11_kpi7_delta = inc.kpi11_reconciliation(
        res, fact_day1, kpi1_day1_full, kpi1_final_full, kpi7_day1, kpi7_final)
    k12 = inc.kpi12_kyc_transitions(res)
    k13_activated, k13_new_customers = inc.kpi13_new_activation(res, fact_day1)

    save(k11_summary, cfg.KPI_DIR, "kpi_11_incremental_reconciliation")
    save(k11_kpi1_delta, cfg.KPI_DIR, "kpi_11a_delta_to_kpi1_net_volume")
    save(k11_kpi7_delta, cfg.KPI_DIR, "kpi_11b_delta_to_kpi7_branch_rank")
    save(k12, cfg.KPI_DIR, "kpi_12_kyc_status_transitions")
    save(k13_activated, cfg.KPI_DIR, "kpi_13_new_account_activation")
    save(k13_new_customers, cfg.KPI_DIR, "kpi_13b_new_customers_zero_prior_history")
    log(f"  New accounts activated on Day 2: {len(k13_activated)}")
    log(f"  KYC status transitions detected: {len(k12)}")

    _finalize_outputs(meta1, meta2, rejects_day1, res["rejects_day2"],
                      res["dim_customer"], res["dim_product"], res["dim_branch"],
                      res["dim_account"], res["fact_final"])

    section("DONE")
    log(f"  Curated layer + KPIs 1-13 written under {cfg.OUTPUT_DIR}")
    log(f"  SQLite warehouse: {cfg.DB_PATH}")


def _finalize_outputs(meta1, meta2, rej1, rej2, dim_customer, dim_product,
                      dim_branch, dim_account, fact):
    # curated layer
    save(dim_customer, cfg.CURATED_DIR, "dim_customer")
    save(dim_product, cfg.CURATED_DIR, "dim_product")
    save(dim_branch, cfg.CURATED_DIR, "dim_branch")
    save(dim_account, cfg.CURATED_DIR, "dim_account")
    save(fact, cfg.CURATED_DIR, "fact_transaction")
    # data quality
    meta = pd.concat([m for m in [meta1, meta2] if m is not None and not m.empty], ignore_index=True)
    save(meta, cfg.DQ_DIR, "ingestion_metadata")
    if rej1 is not None and not rej1.empty:
        save(rej1, cfg.DQ_DIR, "rejected_records_day1")
    if rej2 is not None and not rej2.empty:
        save(rej2, cfg.DQ_DIR, "rejected_records_day2")


if __name__ == "__main__":
    main()
