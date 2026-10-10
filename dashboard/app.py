"""
dashboard/app.py — Streamlit dashboard for the RetailBank pipeline outputs.

Data source:
    AZURE_STORAGE_ACCOUNT_URL set  -> reads latest/ from the Blob `output` container
                                      (managed identity in Azure, `az login` locally)
    otherwise                      -> reads ../output/ on disk

Run locally:
    pip install -r dashboard/requirements.txt
    streamlit run dashboard/app.py
"""
import io
import os
from datetime import timezone

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="RetailBank Analytics", page_icon="🏦", layout="wide")

LOCAL_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
ACCOUNT_URL = os.environ.get("AZURE_STORAGE_ACCOUNT_URL")
OUTPUT_CONTAINER = os.environ.get("OUTPUT_CONTAINER", "output")

# Validated categorical slots 1-4 (light, dark) and status colours.
SERIES = {"light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
          "dark":  ["#3987e5", "#d95926", "#199e70", "#c98500"]}
CRITICAL, GOOD = "#d03b3b", "#0ca30c"


def theme_mode():
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


MODE = theme_mode()
PRIMARY = SERIES[MODE][0]


# ---------------------------------------------------------------- data ----
@st.cache_data(ttl=300, show_spinner="Loading pipeline output…")
def load_all():
    """Return ({'kpis/kpi_01_...': DataFrame, ...}, last_updated_str)."""
    frames, last = {}, None
    if ACCOUNT_URL:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
        cc = BlobServiceClient(ACCOUNT_URL, credential=DefaultAzureCredential()) \
            .get_container_client(OUTPUT_CONTAINER)
        for b in cc.list_blobs(name_starts_with="latest/"):
            if not b.name.endswith(".csv"):
                continue
            key = b.name[len("latest/"):-len(".csv")]
            frames[key] = pd.read_csv(io.BytesIO(cc.download_blob(b.name).readall()))
            if last is None or b.last_modified > last:
                last = b.last_modified
        last = last.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC") if last else None
    else:
        for root, _, files in os.walk(LOCAL_OUTPUT):
            for f in files:
                if f.endswith(".csv"):
                    rel = os.path.relpath(os.path.join(root, f), LOCAL_OUTPUT)
                    frames[rel[:-4].replace(os.sep, "/")] = pd.read_csv(os.path.join(root, f))
        last = "local output/ folder"
    return frames, last


def inr(x):
    """Indian-style short money: ₹1.41 Cr, ₹4.47 L, ₹9,850."""
    if pd.isna(x):
        return "–"
    a = abs(x)
    if a >= 1e7:
        return f"₹{x / 1e7:,.2f} Cr"
    if a >= 1e5:
        return f"₹{x / 1e5:,.2f} L"
    return f"₹{x:,.0f}"


def get(key):
    return DATA.get(key, pd.DataFrame())


# -------------------------------------------------------------- charts ----
def hbar(df, cat, val, title, val_title, fmt=",.0f", color=None, extra_tips=()):
    """Single-series horizontal bar, sorted, with hover tooltip."""
    tips = [alt.Tooltip(cat, title=cat.replace("_", " ").title()),
            alt.Tooltip(val, title=val_title, format=fmt)] + list(extra_tips)
    return (alt.Chart(df, title=title)
            .mark_bar(color=color or PRIMARY, cornerRadiusEnd=4, size=16)
            .encode(y=alt.Y(cat, sort="-x", title=None),
                    x=alt.X(val, title=val_title, axis=alt.Axis(format="~s")),
                    tooltip=tips)
            .properties(height=max(160, 32 * len(df))))


def show(chart):
    st.altair_chart(chart, width="stretch")


def table(df, label="Show table"):
    with st.expander(label):
        st.dataframe(df, width="stretch", hide_index=True)


# ---------------------------------------------------------------- page ----
st.title("RetailBank — Customer Transaction Analytics")

try:
    DATA, LAST = load_all()
except Exception as e:  # storage unreachable / no permission
    st.error(f"Could not load pipeline output: {e}")
    st.stop()

if not DATA:
    st.warning("No pipeline output found yet. Run the pipeline job, then refresh.")
    st.stop()

c1, c2 = st.columns([4, 1])
c1.caption(f"Data as of: **{LAST}** · Day 1 + Day 2 incremental batch")
if c2.button("Refresh data", width="stretch"):
    st.cache_data.clear()
    st.rerun()

k5, k4, k8 = get("kpis/kpi_05_suspicious_transactions"), get("kpis/kpi_04_dormant_accounts"), \
    get("kpis/kpi_08_kyc_risk_exposure")
k10, k13 = get("kpis/kpi_10_data_quality_scorecard"), get("kpis/kpi_13_new_account_activation")

m = st.columns(5)
if not k8.empty:
    m[0].metric("Transaction value", inr(k8["txn_value"].sum()), help="All valid transactions")
    m[1].metric("Transactions", f"{int(k8['txn_count'].sum()):,}")
    risky = k8.loc[k8["kyc_risk"].astype(str) == "True", "pct_of_value"].sum()
    m[4].metric("Value with KYC risk", f"{risky:.1f}%", help="Pending / rejected / unknown KYC")
m[2].metric("Suspicious txns", f"{len(k5):,}")
if not k4.empty:
    hr = int(k4["high_risk_dormant"].astype(str).eq("True").sum())
    m[3].metric("Dormant accounts", f"{len(k4)}", help=f"{hr} high-risk (credit card / loan)")

tabs = st.tabs(["Customers", "Branches & regions", "Products", "Risk & KYC",
                "Data quality", "Day-2 changes"])

# ---- Customers -----------------------------------------------------------
with tabs[0]:
    k1 = get("kpis/kpi_01_top_customers_net_volume")
    k6 = get("kpis/kpi_06_customer_segmentation_rfm")
    a, b = st.columns(2)
    with a:
        if not k1.empty:
            show(hbar(k1, "name", "net_volume", "KPI 1 · Top customers by net volume", "Net volume (₹)",
                      extra_tips=[alt.Tooltip("txn_count", title="Transactions")]))
            table(k1)
    with b:
        if not k6.empty:
            seg = k6.groupby("segment").size().reset_index(name="customers")
            order = ["Platinum", "Gold", "Silver", "Bronze"]
            show(alt.Chart(seg, title="KPI 6 · RFM customer segments")
                 .mark_bar(color=PRIMARY, cornerRadiusEnd=4, size=28)
                 .encode(x=alt.X("segment", sort=order, title=None, axis=alt.Axis(labelAngle=0)),
                         y=alt.Y("customers", title="Customers"),
                         tooltip=["segment", "customers"])
                 .properties(height=280))
            table(k6, "Show all customers with RFM scores")
    st.subheader("KPI 13 · New account activation (Day 2)")
    st.dataframe(k13, width="stretch", hide_index=True)

# ---- Branches ------------------------------------------------------------
with tabs[1]:
    k2 = get("kpis/kpi_02_monthly_trend_branch_region")
    if not k2.empty:
        reg = k2.groupby(["region", "txn_month"], as_index=False)[["value", "volume"]].sum()
        regions = sorted(reg["region"].unique())
        colours = (SERIES[MODE] * 2)[:len(regions)] if len(regions) <= 4 else None
        enc_color = alt.Color("region", title="Region",
                              scale=alt.Scale(domain=regions, range=colours) if colours else alt.Undefined)
        base = alt.Chart(reg, title="KPI 2 · Monthly transaction value by region").encode(
            x=alt.X("txn_month:O", title="Month", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("value", title="Value (₹)", axis=alt.Axis(format="~s")),
            color=enc_color)
        hover = alt.selection_point(fields=["txn_month"], nearest=True, on="pointerover", empty=False)
        lines = base.mark_line(strokeWidth=2)
        points = base.mark_point(size=60, filled=True).encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=["region", "txn_month",
                     alt.Tooltip("value", title="Value (₹)", format=",.0f"),
                     alt.Tooltip("volume", title="Transactions")]).add_params(hover)
        show((lines + points).properties(height=320))
        table(k2, "Show branch-level monthly trend")

    a, b = st.columns(2)
    with a:
        k7 = get("kpis/kpi_07_branch_rank_within_region")
        if not k7.empty:
            show(hbar(k7, "branch_name", "revenue", "KPI 7 · Branch revenue", "Revenue (₹)",
                      extra_tips=[alt.Tooltip("region"), alt.Tooltip("rank_in_region", title="Rank in region")]))
            table(k7)
    with b:
        k9b = get("kpis/kpi_09b_refund_rate_by_branch")
        if not k9b.empty:
            show(hbar(k9b, "branch_name", "refund_rate_pct", "KPI 9 · Refund rate by branch", "Refund rate (%)",
                      fmt=".2f", extra_tips=[alt.Tooltip("refund_txns", title="Refunds")]))
            table(k9b)

# ---- Products ------------------------------------------------------------
with tabs[2]:
    a, b = st.columns(2)
    with a:
        k3 = get("kpis/kpi_03_product_revenue_contribution")
        if not k3.empty:
            show(hbar(k3, "product_name", "revenue_total", "KPI 3 · Revenue by product", "Revenue (₹)",
                      extra_tips=[alt.Tooltip("share_pct", title="Share %"),
                                  alt.Tooltip("pop_growth_pct", title="Period growth %")]))
            table(k3)
    with b:
        k9 = get("kpis/kpi_09_refund_rate_by_product")
        if not k9.empty:
            show(hbar(k9, "product_name", "refund_rate_pct", "KPI 9 · Refund rate by product", "Refund rate (%)",
                      fmt=".2f", extra_tips=[alt.Tooltip("refund_txns", title="Refunds")]))
            table(k9)

# ---- Risk ----------------------------------------------------------------
with tabs[3]:
    a, b = st.columns(2)
    with a:
        if not k8.empty:
            kd = k8.assign(status=k8["kyc_risk"].astype(str).map({"True": "⚠ At risk", "False": "✓ Verified"}))
            show(alt.Chart(kd, title="KPI 8 · Transaction value by KYC status")
                 .mark_bar(cornerRadiusEnd=4, size=20)
                 .encode(y=alt.Y("kyc_status", sort="-x", title=None),
                         x=alt.X("txn_value", title="Value (₹)", axis=alt.Axis(format="~s")),
                         color=alt.Color("status", title=None,
                                         scale=alt.Scale(domain=["⚠ At risk", "✓ Verified"],
                                                         range=[CRITICAL, GOOD])),
                         tooltip=["kyc_status", "status",
                                  alt.Tooltip("txn_value", title="Value (₹)", format=",.0f"),
                                  alt.Tooltip("pct_of_value", title="% of value")])
                 .properties(height=200))
            table(k8)
    with b:
        if not k5.empty:
            reasons = k5["risk_reason"].str.split(";").explode().str.strip() \
                .value_counts().rename_axis("reason").reset_index(name="transactions")
            show(hbar(reasons, "reason", "transactions", "KPI 5 · Suspicious transactions by reason",
                      "Transactions"))
    st.subheader(f"KPI 5 · Suspicious transactions ({len(k5)})")
    st.dataframe(k5, width="stretch", hide_index=True)
    a, b = st.columns(2)
    a.subheader("KPI 4 · Dormant accounts")
    a.dataframe(k4, width="stretch", hide_index=True)
    b.subheader("KPI 12 · KYC status changes")
    b.dataframe(get("kpis/kpi_12_kyc_status_transitions"), width="stretch", hide_index=True)

# ---- Data quality --------------------------------------------------------
with tabs[4]:
    if not k10.empty:
        tot_r, tot_v = k10["records_received"].sum(), k10["records_valid"].sum()
        st.metric("Overall pass rate", f"{100 * tot_v / tot_r:.1f}%",
                  help=f"{tot_v:,} valid of {tot_r:,} records received")
        dq = k10.assign(feed=k10["batch_id"] + " · " + k10["entity"])
        show(hbar(dq, "feed", "pass_rate_pct", "KPI 10 · Pass rate by feed", "Pass rate (%)", fmt=".1f",
                  extra_tips=[alt.Tooltip("records_rejected", title="Rejected"),
                              alt.Tooltip("top_3_rejection_reasons", title="Top reasons")]))
        table(k10, "Show scorecard")
    st.subheader("Ingestion log")
    st.dataframe(get("data_quality/ingestion_metadata"), width="stretch", hide_index=True)
    for day in ("day1", "day2"):
        rej = get(f"data_quality/rejected_records_{day}")
        if not rej.empty:
            table(rej.dropna(axis=1, how="all"), f"Rejected records — {day.upper()} ({len(rej)})")

# ---- Day 2 ---------------------------------------------------------------
with tabs[5]:
    k11 = get("kpis/kpi_11_incremental_reconciliation")
    if not k11.empty:
        r = k11.iloc[0]
        m = st.columns(4)
        m[0].metric("New transactions", int(r["new_transactions_inserted"]))
        m[1].metric("Corrections applied", int(r["transaction_corrections_applied"]))
        m[2].metric("Fact rows", f"{int(r['fact_rows_after']):,}",
                    delta=f"{int(r['fact_rows_after'] - r['fact_rows_before']):+,}")
        m[3].metric("New customers", int(r["customers_new"]),
                    help=f"{int(r['customers_scd2_versioned'])} customers SCD2-versioned")
        table(k11.T.reset_index().rename(columns={"index": "measure", 0: "value"}),
              "Show full reconciliation")
    k11a = get("kpis/kpi_11a_delta_to_kpi1_net_volume")
    if not k11a.empty:
        top = k11a.reindex(k11a["volume_delta"].abs().sort_values(ascending=False).index).head(10)
        show(hbar(top, "name", "volume_delta", "KPI 11 · Biggest changes in customer net volume after Day 2",
                  "Change in net volume (₹)",
                  extra_tips=[alt.Tooltip("net_volume_day1", title="Day 1", format=",.0f"),
                              alt.Tooltip("net_volume_after", title="After Day 2", format=",.0f")]))
        table(k11a, "Show all customers")
    st.subheader("KPI 11 · Branch rank changes")
    st.dataframe(get("kpis/kpi_11b_delta_to_kpi7_branch_rank"), width="stretch", hide_index=True)
