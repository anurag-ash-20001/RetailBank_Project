"""
generate_sample_data.py
------------------------
Creates SYNTHETIC, deliberately-uncleaned sample data that matches the
RetailBank spec, so the pipeline runs out of the box and exercises every
cleaning rule and every KPI.

Writes:
  RetailBank_SourceData/        branches.csv customers.csv products.json transactions.csv
  RetailBank_SourceData_Day2/   branches_day2.csv customer_updates_day2.csv
                                products_day2.json transactions_day2.csv

When you get the REAL hackathon data you do not need this file — just drop the
real files into those two folders (same names) and run `python main.py`.

All names / emails / numbers here are fictitious (per spec section 10).
"""
import csv, json, os, random
from datetime import date, datetime, timedelta

random.seed(7)
BASE = os.path.dirname(os.path.abspath(__file__))
D1 = os.path.join(BASE, "RetailBank_SourceData")
D2 = os.path.join(BASE, "RetailBank_SourceData_Day2")
os.makedirs(D1, exist_ok=True); os.makedirs(D2, exist_ok=True)


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(rows)


# ===========================================================================
# BRANCHES (Day 1)
# ===========================================================================
BR_COLS = ["branch_id", "branch_name", "region", "branch_type",
           "manager_name", "contact_number", "open_date"]
branches = [
    {"branch_id": "B001", "branch_name": "Connaught Place", "region": "North",
     "branch_type": "Metro", "manager_name": "Rakesh Nanda",
     "contact_number": "011-23456789", "open_date": "2015-06-01"},
    {"branch_id": "B002", "branch_name": "Bandra West", "region": "west",      # casing issue
     "branch_type": "Metro", "manager_name": "Priya Shah",
     "contact_number": "022 26401234", "open_date": "2016-03-15"},
    {"branch_id": "B003", "branch_name": "Koramangala", "region": "South",
     "branch_type": "Urban", "manager_name": "",                               # missing manager
     "contact_number": "080-41234567", "open_date": "2017-01-20"},
    {"branch_id": "B004", "branch_name": "Salt Lake", "region": "EAST",         # casing issue
     "branch_type": "Urban", "manager_name": "Amitava Bose",
     "contact_number": "033-23987654", "open_date": "2016-09-10"},
    {"branch_id": "B005", "branch_name": "Banjara Hills", "region": "South",
     "branch_type": "Metro", "manager_name": "Sridevi Rao",
     "contact_number": "040 23551212", "open_date": "2018-05-05"},
    {"branch_id": "B006", "branch_name": "Civil Lines", "region": "north",      # casing issue
     "branch_type": "Rural", "manager_name": "Mohan Agarwal",
     "contact_number": "0141-2611000", "open_date": "2019-11-11"},
    {"branch_id": "B007", "branch_name": "Viman Nagar", "region": "West",
     "branch_type": "Urban", "manager_name": "Neha Kulkarni",
     "contact_number": "20-66123456", "open_date": "2020-02-28"},
    {"branch_id": "B008", "branch_name": "Anna Nagar", "region": "South",
     "branch_type": "Urban", "manager_name": "Karthik Iyer",
     "contact_number": "bad-number", "open_date": "2021-07-19"},               # bad contact
]
branch_ids = [b["branch_id"] for b in branches]
# re-sent duplicate branch rows (exact + trailing whitespace variant)
branch_rows = list(branches)
branch_rows.append(dict(branches[0]))                                           # exact dup
dupv = dict(branches[2]); dupv["branch_name"] = "Koramangala "                  # near-dup
branch_rows.append(dupv)
write_csv(os.path.join(D1, "branches.csv"), BR_COLS, branch_rows)

# ===========================================================================
# CUSTOMERS (Day 1)
# ===========================================================================
CU_COLS = ["customer_id", "name", "gender", "email", "phone", "date_of_birth",
           "kyc_status", "address", "city", "registration_date", "account_id"]
first = ["Aarav","Diya","Vihaan","Ananya","Kabir","Isha","Reyansh","Myra","Arjun",
         "Saanvi","Vivaan","Aadhya","Ayaan","Pari","Krish","Anika","Rohan","Navya",
         "Dhruv","Kiara","Advait","Ira","Yuvraj","Sara","Veer","Riya","Aryan","Tara"]
last = ["Sharma","Verma","Nair","Iyer","Gupta","Mehta","Reddy","Bose","Khan",
        "Patel","Chopra","Banerjee","Pillai","Das","Menon"]
cities = {"North": ["New Delhi","Jaipur"], "West": ["Mumbai","Pune"],
          "South": ["Bengaluru","Hyderabad","Chennai"], "East": ["Kolkata"]}
all_cities = [c for v in cities.values() for c in v]
kyc_raw = ["Verified","verified","VERIFIED","Pending","PENDING","pend",
           "In-Progress","in progress","Rejected","", "rejected"]
gender_raw = ["M","F","male","Female","Other",""]

customers = []
# account assignment: most 1:1, a few shared (joint accounts)
shared = {   # customer_id -> shared account_id
    "C0003": "AC0002", "C0002": "AC0002",     # share AC0002
    "C0011": "AC0009", "C0010": "AC0009",     # share AC0009
    "C0025": "AC0020", "C0026": "AC0020",     # share AC0020
}
for i in range(1, 51):
    cid = f"C{i:04d}"
    acct = shared.get(cid, f"AC{i:04d}")
    name = f"{random.choice(first)} {random.choice(last)}"
    gender = random.choice(gender_raw)
    uname = name.lower().replace(" ", ".")
    email = random.choice([f"{uname}@example.com", f"{uname}@mail.co",
                            "not-an-email", ""])                    # some invalid/blank
    phone = random.choice([f"9{random.randint(100000000,999999999)}",
                           f"+91-9{random.randint(10000000,99999999)}",
                           "12345", "phone-na"])                    # some invalid
    # DOB: mostly valid, a few bad
    if i == 7:
        dob = "2030-01-01"                 # future
    elif i == 13:
        dob = "1850-05-05"                 # implausible
    elif i == 21:
        dob = ""                           # missing
    else:
        dob = (date(1965,1,1) + timedelta(days=random.randint(0, 16000))).isoformat()
    kyc = random.choice(kyc_raw)
    region = random.choice(list(cities.keys()))
    city = random.choice(cities[region])
    addr = f"{random.randint(1,999)} {random.choice(['MG Road','Park St','Ring Rd','Lake View'])}, {city}"
    # registration_date in several formats
    rd = date(2018,1,1) + timedelta(days=random.randint(0, 2500))
    fmt = random.choice(["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y"])
    customers.append({"customer_id": cid, "name": name, "gender": gender,
                      "email": email, "phone": phone, "date_of_birth": dob,
                      "kyc_status": kyc, "address": addr, "city": city,
                      "registration_date": rd.strftime(fmt), "account_id": acct})

customer_ids = [c["customer_id"] for c in customers]
account_ids = sorted({c["account_id"] for c in customers})

cust_rows = list(customers)
# near-duplicate re-sent rows (same id, trailing whitespace / casing)
d = dict(customers[4]); d["name"] = d["name"].upper() + " "; cust_rows.append(d)
d = dict(customers[9]); d["email"] = " " + d["email"]; cust_rows.append(d)
cust_rows.append(dict(customers[15]))        # exact dup
write_csv(os.path.join(D1, "customers.csv"), CU_COLS, cust_rows)

# ===========================================================================
# PRODUCTS (Day 1)  -- JSON with a structural defect + data issues
# ===========================================================================
products = [
    {"product_id": "P01", "product_name": "Savings Account", "category": "Savings",       "price": 0.0,    "is_active": True},
    {"product_id": "P02", "product_name": "Current Account", "category": "current",        "price": 500.0,  "is_active": "Y"},
    {"product_id": "P03", "product_name": "Fixed Deposit",   "category": "Fixed Deposit",  "price": 0.0,    "is_active": 1},
    {"product_id": "P04", "product_name": "Personal Loan",   "category": "loan",           "price": 1500.0, "is_active": "yes"},
    {"product_id": "P05", "product_name": "Home Loan",       "category": "Loan",           "price": 2500.0, "is_active": True},
    {"product_id": "P06", "product_name": "Credit Card",     "category": "credit card",    "price": 999.0,  "is_active": "true"},
    {"product_id": "P07", "product_name": "Gold Credit Card","category": "CREDIT CARD",    "price": -200.0, "is_active": None},   # negative price, null active
    {"product_id": "P08", "product_name": "Mutual Fund",     "category": "Mutual Fund",    "price": 100.0,  "is_active": "0"},
    {"product_id": "P09", "product_name": "Forex Card",      "category": "Forex",          "price": 300.0,  "is_active": False},
    {"product_id": "P06", "product_name": "Credit Card (dup)","category": "Credit Card",   "price": 999.0,  "is_active": True},   # duplicate product_id
]
product_ids = ["P01","P02","P03","P04","P05","P06","P07","P08","P09"]
# serialize then inject a trailing-comma structural defect (invalid JSON on purpose)
raw = json.dumps(products, indent=2)
raw = raw.rstrip()
assert raw.endswith("]")
raw = raw[:-1].rstrip() + ",\n]"     # trailing comma before ] -> structural defect
with open(os.path.join(D1, "products.json"), "w") as f:
    f.write(raw)

# ===========================================================================
# TRANSACTIONS (Day 1)
# ===========================================================================
TX_COLS = ["transaction_id", "account_id", "branch_id", "product_id", "txn_date",
           "txn_timestamp", "amount", "currency", "payment_method", "status", "is_refund"]
status_raw = ["completed","Completed","COMPLETED","pending","Pending","failed","reversed","Reversed"]
pay_raw = ["UPI","upi","Card","card","NetBanking","netbanking","Cash","cheque","CHEQUE"]
START = date(2026, 1, 1); END = date(2026, 3, 31)
tid = 0
def ntid():
    global tid; tid += 1; return f"T{tid:07d}"

def rand_dt(a, b):
    d0 = a + timedelta(days=random.randint(0, (b - a).days))
    return datetime(d0.year, d0.month, d0.day,
                    random.randint(6, 22), random.randint(0, 59), random.randint(0, 59))

def amt_str(val):
    """Return amount sometimes as a messy string (comma / currency-symbol formatted)."""
    r = random.random()
    if r < 0.08:
        return f"₹{val:,.2f}"           # ₹ prefixed
    if r < 0.14:
        # Indian grouping with commas
        return f"{val:,.2f}"
    if r < 0.17:
        return f"${val:,.2f}"                # $ prefixed (symbol/currency mismatch)
    return f"{val:.2f}"

# reserve a few accounts as dormant: they only transact in early January and
# never again, so they fall outside the 90-day window by the Day-2 processing date
DORMANT_ACCTS = ["AC0048", "AC0049", "AC0050"]
active_pool = [a for a in account_ids if a not in DORMANT_ACCTS]

txns = []
for _ in range(800):
    acct = random.choice(active_pool)
    ts = rand_dt(START, END)
    amount = round(random.uniform(200, 95_000), 2)
    if random.random() < 0.03:
        amount = round(random.uniform(120_000, 850_000), 2)   # high value -> suspicious
    cur = "INR" if random.random() > 0.05 else random.choice(["USD", "EUR"])
    is_ref = "true" if random.random() < 0.08 else random.choice(["false","FALSE","N","no",""])
    txns.append({
        "transaction_id": ntid(), "account_id": acct,
        "branch_id": random.choice(branch_ids), "product_id": random.choice(product_ids),
        "txn_date": ts.date().isoformat(), "txn_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "amount": amt_str(amount), "currency": cur,
        "payment_method": random.choice(pay_raw), "status": random.choice(status_raw),
        "is_refund": is_ref})

# ---- dormant accounts: a single early-January transaction, nothing after ----
for k, acct in enumerate(DORMANT_ACCTS):
    prod = "P06" if k == 0 else "P05" if k == 1 else "P01"   # first two are Credit Card / Loan
    ts = datetime(2025, 12, 20, 10, 0, 0)                     # well before the 90-day window
    txns.append({"transaction_id": ntid(), "account_id": acct, "branch_id": "B003",
                 "product_id": prod, "txn_date": ts.date().isoformat(),
                 "txn_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                 "amount": f"{random.uniform(5000,40000):.2f}", "currency": "INR",
                 "payment_method": "netbanking", "status": "completed", "is_refund": "false"})

# ---- suspicious: late-night (00:00-05:00) transactions ----
for _ in range(10):
    base = random.choice(txns); r = dict(base)
    ts = datetime(2026, random.randint(1,3), random.randint(1,28),
                  random.randint(0,4), random.randint(0,59), random.randint(0,59))
    r["transaction_id"] = ntid(); r["txn_date"] = ts.date().isoformat()
    r["txn_timestamp"] = ts.strftime("%Y-%m-%d %H:%M:%S")
    r["amount"] = f"{random.uniform(5000,60000):.2f}"; r["currency"] = "INR"
    r["status"] = "completed"; r["is_refund"] = "false"
    txns.append(r)

# ---- suspicious: burst of 4 txns, same account, within 10 minutes ----
burst_acct = "AC0005"
burst_base = datetime(2026, 2, 14, 11, 0, 0)
for k in range(4):
    ts = burst_base + timedelta(minutes=2 * k)
    txns.append({"transaction_id": ntid(), "account_id": burst_acct,
                 "branch_id": "B001", "product_id": "P06",
                 "txn_date": ts.date().isoformat(), "txn_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                 "amount": f"{random.uniform(8000,15000):.2f}", "currency": "INR",
                 "payment_method": "card", "status": "completed", "is_refund": "false"})

# ---- date/timestamp DISAGREEMENT (date says one day, timestamp another) ----
for _ in range(8):
    base = dict(random.choice(txns)); base["transaction_id"] = ntid()
    ts = rand_dt(START, END)
    base["txn_timestamp"] = ts.strftime("%Y-%m-%d %H:%M:%S")
    base["txn_date"] = (ts.date() + timedelta(days=3)).isoformat()   # mismatched date
    txns.append(base)

# ---- multiple date formats in txn_date ----
for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y"]:
    base = dict(random.choice(txns)); base["transaction_id"] = ntid()
    ts = rand_dt(START, END)
    base["txn_timestamp"] = ts.strftime("%Y-%m-%d %H:%M:%S")
    base["txn_date"] = ts.date().strftime(fmt)
    txns.append(base)

# ---- invalid amounts (blank / zero / negative) -> reject ----
for bad in ["", "0", "0.00", "-500.00", "abc"]:
    base = dict(random.choice(txns)); base["transaction_id"] = ntid(); base["amount"] = bad
    txns.append(base)

# ---- orphan foreign keys -> reject ----
for _ in range(5):
    b = dict(random.choice(txns)); b["transaction_id"] = ntid(); b["account_id"] = "AC9999"; txns.append(b)
for _ in range(3):
    b = dict(random.choice(txns)); b["transaction_id"] = ntid(); b["branch_id"] = "B099"; txns.append(b)
for _ in range(3):
    b = dict(random.choice(txns)); b["transaction_id"] = ntid(); b["product_id"] = "P99"; txns.append(b)

# ---- future-dated beyond processing date (2026-03-31) -> quarantine ----
for _ in range(4):
    b = dict(random.choice(txns)); b["transaction_id"] = ntid()
    ts = datetime(2026, 4, 15, 10, 0, 0)
    b["txn_date"] = ts.date().isoformat(); b["txn_timestamp"] = ts.strftime("%Y-%m-%d %H:%M:%S")
    txns.append(b)

# ---- exact duplicate rows ----
for base in random.sample(txns[:800], 10):
    txns.append(dict(base))

# ---- conflicting duplicate transaction_id (same id, different payload) ----
for base in random.sample([t for t in txns[:800]], 6):
    c = dict(base); c["amount"] = f"{random.uniform(1000,50000):.2f}"; c["status"] = "reversed"
    txns.append(c)   # same transaction_id as `base`, different amount/status

random.shuffle(txns)
write_csv(os.path.join(D1, "transactions.csv"), TX_COLS, txns)

# ===========================================================================
# DAY 2 — dimension updates + incremental transactions
# ===========================================================================
# branches_day2: one new branch + one manager change
branch2 = [
    {"branch_id": "B009", "branch_name": "Whitefield", "region": "South",
     "branch_type": "Urban", "manager_name": "Deepa Menon",
     "contact_number": "080-67451000", "open_date": "2026-04-01"},            # NEW branch
    {"branch_id": "B002", "branch_name": "Bandra West", "region": "West",
     "branch_type": "Metro", "manager_name": "Farhan Ali",                     # manager CHANGED (SCD2)
     "contact_number": "022 26401234", "open_date": "2016-03-15"},
]
write_csv(os.path.join(D2, "branches_day2.csv"), BR_COLS, branch2)

# customer_updates_day2: KYC progressions, address change, new customers
cust2 = []
# KYC Pending/other -> Verified for 4 existing customers (forces to Pending first so a transition shows)
for cid in ["C0001", "C0004", "C0008", "C0012"]:
    src = next(c for c in customers if c["customer_id"] == cid)
    r = dict(src); r["kyc_status"] = "Verified"; cust2.append(r)
# address change (SCD2) for one existing customer
src = next(c for c in customers if c["customer_id"] == "C0004")
r = dict(src); r["kyc_status"] = "Verified"; r["address"] = "NEW 42 Residency Rd, " + src["city"]
cust2[1] = r   # C0004 gets both kyc + address change
# brand-new customers (new ids + new accounts)
for j, cid in enumerate(["C0051", "C0052", "C0053"], start=1):
    acct = f"AC00{50+j}"
    name = f"{random.choice(first)} {random.choice(last)}"
    cust2.append({"customer_id": cid, "name": name, "gender": random.choice(["M","F"]),
                  "email": f"{name.lower().replace(' ','.')}@example.com",
                  "phone": f"9{random.randint(100000000,999999999)}",
                  "date_of_birth": (date(1990,1,1)+timedelta(days=random.randint(0,9000))).isoformat(),
                  "kyc_status": "Pending", "address": f"{random.randint(1,999)} New Town, Bengaluru",
                  "city": "Bengaluru", "registration_date": "2026-04-01", "account_id": acct})
write_csv(os.path.join(D2, "customer_updates_day2.csv"), CU_COLS, cust2)
new_accounts_day2 = [c["account_id"] for c in cust2 if c["customer_id"] in ("C0051","C0052","C0053")]

# products_day2.json: price change + is_active change on existing + a new product (valid JSON)
prod2 = [
    {"product_id": "P04", "product_name": "Personal Loan", "category": "Loan", "price": 1800.0, "is_active": True},  # price change
    {"product_id": "P09", "product_name": "Forex Card", "category": "Forex", "price": 300.0, "is_active": True},     # reactivated
    {"product_id": "P10", "product_name": "Education Loan", "category": "Loan", "price": 1200.0, "is_active": True},  # NEW product
]
with open(os.path.join(D2, "products_day2.json"), "w") as f:
    json.dump(prod2, f, indent=2)

# transactions_day2: new Apr transactions + corrections to Day-1 ids + new-account activity
tx2 = []
# corrections: reuse 8 existing Day-1 transaction_ids with revised status/amount
settled_day1 = [t for t in txns if t["transaction_id"].startswith("T") and t["currency"] == "INR"][:400]
for base in random.sample(settled_day1, 8):
    c = dict(base)
    c["status"] = "completed"
    c["amount"] = f"{random.uniform(1000, 70000):.2f}"
    c["txn_timestamp"] = "2026-04-01 09:30:00"; c["txn_date"] = "2026-04-01"
    tx2.append(c)   # SAME transaction_id -> late correction (update fact, not new)

# brand-new Apr transactions (existing active + new accounts; keep dormant ones dormant)
pool_accounts = active_pool + new_accounts_day2
for _ in range(110):
    acct = random.choice(pool_accounts)
    ts = datetime(2026, 4, 1, random.randint(6,22), random.randint(0,59), random.randint(0,59))
    amount = round(random.uniform(300, 90_000), 2)
    tx2.append({"transaction_id": ntid(), "account_id": acct, "branch_id": random.choice(branch_ids+["B009"]),
                "product_id": random.choice(product_ids+["P10"]),
                "txn_date": ts.date().isoformat(), "txn_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "amount": f"{amount:.2f}", "currency": "INR",
                "payment_method": random.choice(pay_raw), "status": random.choice(status_raw),
                "is_refund": "true" if random.random() < 0.07 else "false"})

# guarantee each new account transacts on Day 2 (New Account Activation KPI)
for acct in new_accounts_day2:
    tx2.append({"transaction_id": ntid(), "account_id": acct, "branch_id": "B009", "product_id": "P01",
                "txn_date": "2026-04-01", "txn_timestamp": "2026-04-01 10:15:00",
                "amount": f"{random.uniform(1000,25000):.2f}", "currency": "INR",
                "payment_method": "upi", "status": "completed", "is_refund": "false"})

# a couple of dirty Day-2 rows (prove incremental cleaning also runs)
tx2.append({"transaction_id": ntid(), "account_id": "AC9999", "branch_id": "B001", "product_id": "P01",
            "txn_date": "2026-04-01", "txn_timestamp": "2026-04-01 11:00:00", "amount": "5000.00",
            "currency": "INR", "payment_method": "upi", "status": "completed", "is_refund": "false"})  # orphan
tx2.append({"transaction_id": ntid(), "account_id": random.choice(account_ids), "branch_id": "B002",
            "product_id": "P06", "txn_date": "2026-04-20", "txn_timestamp": "2026-04-20 11:00:00",
            "amount": "7000.00", "currency": "INR", "payment_method": "card", "status": "completed",
            "is_refund": "false"})  # future-dated beyond Day-2 processing date

random.shuffle(tx2)
write_csv(os.path.join(D2, "transactions_day2.csv"), TX_COLS, tx2)

print("Sample data generated.")
print(f"  Day1: {len(branch_rows)} branch rows, {len(cust_rows)} customer rows, "
      f"{len(products)} products, {len(txns)} transaction rows")
print(f"  Day2: {len(branch2)} branch updates, {len(cust2)} customer updates, "
      f"{len(prod2)} products, {len(tx2)} transaction rows")
