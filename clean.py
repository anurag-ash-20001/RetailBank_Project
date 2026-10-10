"""
clean.py — profile, standardize, validate and deduplicate each entity.

Each clean_* function returns (clean_df, rejected_df, dq) where:
  clean_df    : standardized rows ready for the curated model
  rejected_df : quarantined rows, each with a `reject_reason` (never silently dropped)
  dq          : dict of counts for the Data Quality scorecard (KPI 10)

Design: validation failures are QUARANTINED (routed to rejects with a reason);
value problems that still leave a usable record (missing manager, invalid email,
corrected price, non-INR currency, date/timestamp disagreement) are kept and
FLAGGED instead of dropped.
"""
import pandas as pd
from datetime import date

import utils as u


def _dq(entity, received, clean_df, rejected_df, duplicates=0, flags=None):
    reasons = (rejected_df["reject_reason"].value_counts().to_dict()
               if rejected_df is not None and not rejected_df.empty else {})
    return {"entity": entity, "received": int(received),
            "valid": int(len(clean_df)), "rejected": int(len(rejected_df)),
            "duplicates_removed": int(duplicates), "reasons": reasons,
            "flags": flags or {}}


def _empty_rejects(cols):
    return pd.DataFrame(columns=list(cols) + ["reject_reason"])


# ===========================================================================
# BRANCHES
# ===========================================================================
def clean_branches(raw):
    received = len(raw)
    df = raw.copy()
    rejects = []

    df["branch_id"] = df["branch_id"].map(lambda x: u.std_text(x, lower=False))
    df["branch_name"] = df["branch_name"].map(lambda x: u.std_text(x, lower=False))
    df["region"] = df["region"].map(u.std_title)             # north/NORTH -> North
    df["branch_type"] = df["branch_type"].map(u.std_title)
    df["manager_name"] = df["manager_name"].map(lambda x: u.std_text(x, lower=False))
    df["open_date"] = df["open_date"].map(u.parse_date)

    # exact duplicate rows
    m = df.duplicated(keep="first")
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "exact duplicate row"; rejects.append(d)
    df = df[~m]

    # missing / duplicate branch_id
    m = df["branch_id"] == ""
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "missing branch_id"; rejects.append(d)
    df = df[~m]
    m = df.duplicated(subset=["branch_id"], keep="first")
    dups = int(m.sum())
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "duplicate branch_id (re-sent)"; rejects.append(d)
    df = df[~m]

    # flags (kept, not rejected)
    df["manager_missing"] = df["manager_name"] == ""
    df["contact_valid"] = df["contact_number"].map(u.valid_phone)
    flags = {"manager_missing": int(df["manager_missing"].sum()),
             "invalid_contact": int((~df["contact_valid"]).sum())}

    clean = df[["branch_id", "branch_name", "region", "branch_type",
                "manager_name", "contact_number", "open_date",
                "manager_missing", "contact_valid"]].reset_index(drop=True)
    rej = pd.concat(rejects, ignore_index=True) if rejects else _empty_rejects(raw.columns)
    return clean, rej, _dq("branches", received, clean, rej, dups, flags)


# ===========================================================================
# CUSTOMERS
# ===========================================================================
def clean_customers(raw):
    received = len(raw)
    df = raw.copy()
    rejects = []

    df["customer_id"] = df["customer_id"].map(lambda x: u.std_text(x, lower=False))
    df["name"] = df["name"].map(u.std_title)
    df["gender"] = df["gender"].map(u.std_gender)
    df["email"] = df["email"].map(lambda x: u.std_text(x))
    df["phone"] = df["phone"].map(lambda x: u.std_text(x, lower=False))
    df["kyc_status"] = df["kyc_status"].map(u.std_kyc)
    df["address"] = df["address"].map(lambda x: u.std_text(x, lower=False))
    df["city"] = df["city"].map(u.std_title)
    df["account_id"] = df["account_id"].map(lambda x: u.std_text(x, lower=False))

    # DOB validation: future or implausible (age>120) -> null + flag
    today = date.today()
    def dob_ok(v):
        iso = u.parse_date(v)
        if iso is None:
            return (None, False)
        y = int(iso[:4])
        d0 = date.fromisoformat(iso)
        if d0 > today or (today.year - y) > 120:
            return (None, False)
        return (iso, True)
    dob = df["date_of_birth"].map(dob_ok)
    df["date_of_birth"] = dob.map(lambda t: t[0])
    df["dob_valid"] = dob.map(lambda t: t[1])

    # registration_date standardize
    df["registration_date"] = df["registration_date"].map(u.parse_date)

    # exact dup rows, then near-dup / dup id
    m = df.duplicated(keep="first")
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "exact duplicate row"; rejects.append(d)
    df = df[~m]
    m = df["customer_id"] == ""
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "missing customer_id"; rejects.append(d)
    df = df[~m]
    m = df.duplicated(subset=["customer_id"], keep="first")   # re-sent near-duplicates
    dups = int(m.sum())
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "duplicate customer_id (re-sent)"; rejects.append(d)
    df = df[~m]

    # flags
    df["email_valid"] = df["email"].map(u.valid_email)
    df["phone_valid"] = df["phone"].map(u.valid_phone)
    flags = {"invalid_email": int((~df["email_valid"]).sum()),
             "invalid_phone": int((~df["phone_valid"]).sum()),
             "invalid_dob": int((~df["dob_valid"]).sum())}

    clean = df[["customer_id", "name", "gender", "email", "email_valid", "phone",
                "phone_valid", "date_of_birth", "dob_valid", "kyc_status",
                "address", "city", "registration_date", "account_id"]].reset_index(drop=True)
    rej = pd.concat(rejects, ignore_index=True) if rejects else _empty_rejects(raw.columns)
    return clean, rej, _dq("customers", received, clean, rej, dups, flags)


# ===========================================================================
# PRODUCTS
# ===========================================================================
def clean_products(raw):
    received = len(raw)
    df = raw.copy()
    rejects = []

    df["product_id"] = df["product_id"].map(lambda x: u.std_text(x, lower=False))
    df["product_name"] = df["product_name"].map(lambda x: u.std_text(x, lower=False))
    df["category"] = df["category"].map(u.std_title)       # credit card -> Credit Card
    price = df["price"].map(u.parse_amount)
    df["price_corrected"] = price.map(lambda p: p is not None and p < 0)
    df["price"] = price.map(lambda p: abs(p) if p is not None else None)  # correct negatives
    df["is_active"] = df["is_active"].map(lambda v: u.std_bool(v, default=True))

    m = df.duplicated(keep="first")
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "exact duplicate row"; rejects.append(d)
    df = df[~m]
    m = df["product_id"] == ""
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "missing product_id"; rejects.append(d)
    df = df[~m]
    m = df.duplicated(subset=["product_id"], keep="first")
    dups = int(m.sum())
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "duplicate product_id"; rejects.append(d)
    df = df[~m]

    flags = {"price_corrected": int(df["price_corrected"].sum())}
    clean = df[["product_id", "product_name", "category", "price",
                "is_active", "price_corrected"]].reset_index(drop=True)
    rej = pd.concat(rejects, ignore_index=True) if rejects else _empty_rejects(raw.columns)
    return clean, rej, _dq("products", received, clean, rej, dups, flags)


# ===========================================================================
# TRANSACTIONS
# ===========================================================================
def clean_transactions(raw, accounts, branch_ids, product_ids, processing_date):
    received = len(raw)
    df = raw.copy()
    rejects = []
    proc = date.fromisoformat(processing_date)

    for c in ["transaction_id", "account_id", "branch_id", "product_id"]:
        df[c] = df[c].map(lambda x: u.std_text(x, lower=False))
    df["currency"] = df["currency"].map(lambda x: u.std_text(x, lower=False).upper())
    df["payment_method"] = df["payment_method"].map(u.std_payment)
    df["status"] = df["status"].map(u.std_status)
    df["is_refund"] = df["is_refund"].map(lambda v: u.std_bool(v, default=False))

    # parse amount / dates / timestamps
    amount = df["amount"].map(u.parse_amount)
    ts_iso = df["txn_timestamp"].map(u.parse_timestamp)
    date_iso = df["txn_date"].map(u.parse_date)

    # effective date: timestamp's date is authoritative; else the date field
    def eff_date(row_ts, row_dt):
        if row_ts:
            return row_ts[:10]
        return row_dt
    effective = [eff_date(t, d) for t, d in zip(ts_iso, date_iso)]
    effective = pd.Series(effective, index=df.index)
    # date/timestamp disagreement flag
    conflict = pd.Series(
        [(t is not None and d is not None and t[:10] != d) for t, d in zip(ts_iso, date_iso)],
        index=df.index)
    df["txn_datetime"] = pd.Series(
        [t if t else (f"{d} 00:00:00" if d else None) for t, d in zip(ts_iso, date_iso)],
        index=df.index)
    df["txn_date"] = effective
    df["currency_review"] = df["currency"].map(lambda c: c != "INR" and c != "")
    df["date_timestamp_conflict"] = conflict

    # ---- structural rejects first ----
    # exact-duplicate detection uses the ORIGINAL raw rows (before column reshaping)
    m = raw.duplicated(keep="first").reindex(df.index, fill_value=False)
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "exact duplicate row"; rejects.append(d)
    df = df[~m]; amount = amount[~m]; effective = effective[~m]

    m = df["transaction_id"] == ""
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "missing transaction_id"; rejects.append(d)
    df = df[~m]; amount = amount[~m]; effective = effective[~m]

    m = df.duplicated(subset=["transaction_id"], keep="first")
    dups = int(m.sum())
    if m.any():
        d = df[m].copy(); d["reject_reason"] = "duplicate/conflicting transaction_id"; rejects.append(d)
    df = df[~m]; amount = amount[~m]; effective = effective[~m]

    # ---- field-level validation (first failing rule wins) ----
    reason = pd.Series("", index=df.index)
    reason[(reason == "") & amount.isna()] = "invalid amount (blank/non-numeric)"
    reason[(reason == "") & (amount <= 0)] = "invalid amount (<= 0)"
    reason[(reason == "") & effective.isna()] = "unparseable date"
    # future-dated beyond processing date
    fut = effective.map(lambda d0: (d0 is not None) and (date.fromisoformat(d0) > proc))
    reason[(reason == "") & fut] = "future-dated beyond processing date"
    # referential integrity
    reason[(reason == "") & (~df["account_id"].isin(accounts))] = "orphan account_id"
    reason[(reason == "") & (~df["branch_id"].isin(branch_ids))] = "orphan branch_id"
    reason[(reason == "") & (~df["product_id"].isin(product_ids))] = "orphan product_id"

    bad = reason != ""
    if bad.any():
        d = df[bad].copy(); d["reject_reason"] = reason[bad].values; rejects.append(d)

    good = df[~bad].copy()
    good["amount"] = amount[~bad].astype(float)

    flags = {"non_INR_currency_flagged": int(good["currency_review"].sum()),
             "date_timestamp_conflicts": int(good["date_timestamp_conflict"].sum()),
             "refunds": int(good["is_refund"].sum())}

    clean = good[["transaction_id", "account_id", "branch_id", "product_id",
                  "txn_date", "txn_datetime", "amount", "currency", "currency_review",
                  "payment_method", "status", "is_refund"]].reset_index(drop=True)
    rej = pd.concat(rejects, ignore_index=True) if rejects else _empty_rejects(raw.columns)
    return clean, rej, _dq("transactions", received, clean, rej, dups, flags)
