"""
ingest.py — read raw source files exactly as received (bronze/raw zone) and
capture ingestion metadata (file name, row count, ingestion timestamp, batch id).

Everything is read as strings so blanks and garbage never become silent NaN.
products.json is parsed defensively because the feed can contain a real JSON
structural defect (per spec section 5.2).
"""
import json
import os
import re
from datetime import datetime

import pandas as pd


def _read_csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False, skipinitialspace=False)


def _recover_json(text):
    """
    Best-effort recovery for a malformed products feed. Handles the common
    structural defects named in the spec: trailing commas and two JSON values
    concatenated without an enclosing array.
    Returns (list_of_dicts, defect_note).
    """
    note = None
    try:
        data = json.loads(text)
        return (data if isinstance(data, list) else [data]), None
    except json.JSONDecodeError:
        note = "malformed JSON recovered"

    # 1) strip trailing commas:  ,]  or  ,}
    fixed = re.sub(r",(\s*[\]}])", r"\1", text)
    try:
        data = json.loads(fixed)
        return (data if isinstance(data, list) else [data]), "trailing comma(s) removed"
    except json.JSONDecodeError:
        pass

    # 2) concatenated top-level objects/arrays -> decode sequentially
    decoder = json.JSONDecoder()
    idx, out = 0, []
    s = fixed.strip()
    while idx < len(s):
        while idx < len(s) and s[idx] in " \t\r\n,":
            idx += 1
        if idx >= len(s):
            break
        obj, end = decoder.raw_decode(s, idx)
        out.extend(obj if isinstance(obj, list) else [obj])
        idx = end
    return out, (note or "concatenated JSON segments merged")


def read_products(path):
    with open(path, "r") as f:
        text = f.read()
    records, defect = _recover_json(text)
    df = pd.DataFrame(records)
    # everything to string for uniform cleaning downstream
    for c in df.columns:
        df[c] = df[c].apply(lambda v: "" if v is None else str(v))
    return df, defect


def ingest_batch(data_dir, files, batch_id):
    """
    Read all four source files for a batch.
    Returns (raw_dict, ingestion_metadata_df).
    raw_dict keys: branches, customers, products, transactions (missing -> None).
    """
    ing_ts = datetime.now().isoformat(timespec="seconds")
    raw, meta = {}, []

    for entity, fname in files.items():
        path = os.path.join(data_dir, fname)
        if not os.path.exists(path):
            raw[entity] = None
            meta.append({"batch_id": batch_id, "entity": entity, "file_name": fname,
                         "row_count": 0, "ingestion_timestamp": ing_ts, "status": "MISSING"})
            continue
        if entity == "products":
            df, defect = read_products(path)
            note = defect or "ok"
        else:
            df = _read_csv(path)
            note = "ok"
        raw[entity] = df
        meta.append({"batch_id": batch_id, "entity": entity, "file_name": fname,
                     "row_count": len(df), "ingestion_timestamp": ing_ts, "status": note})

    return raw, pd.DataFrame(meta)
