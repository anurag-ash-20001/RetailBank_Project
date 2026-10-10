"""
model.py — build the curated/gold layer.

Conformed dimensions (Customer, Product, Branch) + a Transaction fact at
transaction grain. Dimensions carry a surrogate key and SCD Type 2 effective
dating (effective_from / effective_to / is_current) so Day-2 history can be
preserved. Accounts are resolved into dim_account, with the shared-account case
settled by naming a primary holder (lowest customer_id on the account).

Grain & keys
------------
  fact_transaction : one row per transaction_id (natural PK).
                     FKs -> account_id, branch_id, product_id (natural keys).
  dim_customer     : natural key customer_id, surrogate customer_sk, SCD2.
  dim_product      : natural key product_id, surrogate product_sk, SCD2.
  dim_branch       : natural key branch_id, surrogate branch_sk, SCD2.
  dim_account      : natural key account_id (num_holders, is_shared, primary_customer_id).
"""
import sqlite3
import pandas as pd

import config as cfg


def add_scd2(df, nk_col, sk_col, load_date, start_sk=1):
    """Attach surrogate key + SCD2 effective-dating columns to a fresh dimension."""
    out = df.copy().reset_index(drop=True)
    out.insert(0, sk_col, range(start_sk, start_sk + len(out)))
    out["effective_from"] = load_date
    out["effective_to"] = cfg.SCD_OPEN_END
    out["is_current"] = 1
    return out


def build_dim_account(clean_customers):
    """Resolve accounts; settle shared accounts by naming a primary holder."""
    g = clean_customers.groupby("account_id")["customer_id"]
    acct = g.agg(num_holders="nunique", primary_customer_id="min").reset_index()
    acct["is_shared"] = acct["num_holders"] > 1
    return acct[["account_id", "num_holders", "is_shared", "primary_customer_id"]]


def build_dimensions(cb, cc, cp, load_date):
    dim_branch = add_scd2(cb, "branch_id", "branch_sk", load_date)
    dim_customer = add_scd2(cc, "customer_id", "customer_sk", load_date)
    dim_product = add_scd2(cp, "product_id", "product_sk", load_date)
    dim_account = build_dim_account(cc)
    return dim_customer, dim_product, dim_branch, dim_account


def build_fact(clean_txn):
    f = clean_txn.copy().reset_index(drop=True)
    f["txn_month"] = f["txn_date"].str.slice(0, 7)                       # YYYY-MM
    mnum = f["txn_date"].str.slice(5, 7).astype(int)
    yr = f["txn_date"].str.slice(0, 4)
    f["txn_quarter"] = yr + "-Q" + (((mnum - 1) // 3) + 1).astype(str)   # YYYY-Qn
    f["txn_hour"] = f["txn_datetime"].str.slice(11, 13).astype(int)
    # net_amount nets refunds out of transaction volume (KPI 1)
    f["net_amount"] = f.apply(lambda r: -r["amount"] if r["is_refund"] else r["amount"], axis=1)
    f["is_settled"] = f["status"].isin(cfg.SETTLED_STATUSES)
    cols = ["transaction_id", "account_id", "branch_id", "product_id", "txn_date",
            "txn_datetime", "txn_month", "txn_quarter", "txn_hour", "amount",
            "net_amount", "currency", "currency_review", "payment_method",
            "status", "is_refund", "is_settled"]
    return f[cols]


def load_warehouse(db_path, tables: dict):
    """tables: {table_name: DataFrame} -> written with if_exists='replace'."""
    conn = sqlite3.connect(db_path)
    try:
        for name, df in tables.items():
            df.to_sql(name, conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()
