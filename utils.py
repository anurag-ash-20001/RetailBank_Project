"""
utils.py — small, reusable parsing / standardization helpers used by cleaning.
Each returns a normalized value (or None when it cannot be parsed), never raises.
"""
import re
from datetime import datetime

DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y",
                "%m/%d/%Y", "%d %b %Y", "%Y.%m.%d"]
TS_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y %H:%M:%S",
              "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"]


def parse_date(val):
    """Parse a date in several formats -> 'YYYY-MM-DD' or None."""
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_timestamp(val):
    """Parse a datetime -> 'YYYY-MM-DD HH:MM:SS' or None."""
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return None
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    # last resort: a bare date in a timestamp field
    d = parse_date(s)
    return f"{d} 00:00:00" if d else None


_AMOUNT_CLEAN = re.compile(r"[^\d.\-]")   # strip currency symbols, commas, spaces


def parse_amount(val):
    """
    Parse messy amounts: '1,23,456.78', '₹5,000', '$120.50', '-500', '' -> float or None.
    Returns None when the value is blank or non-numeric.
    """
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return None
    cleaned = _AMOUNT_CLEAN.sub("", s)
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def std_text(val, lower=True):
    if val is None:
        return ""
    s = str(val).strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower() if lower else s


def std_title(val):
    s = std_text(val, lower=False)
    return s.title() if s else ""


# ---- categorical standardizers ----------------------------------------
_KYC = {"verified": "verified", "v": "verified",
        "pending": "pending", "pend": "pending", "in progress": "pending",
        "in-progress": "pending", "inprogress": "pending",
        "rejected": "rejected", "reject": "rejected", "r": "rejected"}

def std_kyc(val):
    s = std_text(val)
    return _KYC.get(s, "unknown" if s == "" else s)


_GENDER = {"m": "male", "male": "male", "f": "female", "female": "female",
           "o": "other", "other": "other", "": "unknown"}

def std_gender(val):
    s = std_text(val)
    return _GENDER.get(s, "unknown")


_STATUS = {"completed": "completed", "complete": "completed", "success": "completed",
           "pending": "pending", "failed": "failed", "fail": "failed",
           "reversed": "reversed", "reversal": "reversed", "refunded": "reversed"}

def std_status(val):
    s = std_text(val)
    return _STATUS.get(s, s if s else "unknown")


_PAY = {"upi": "upi", "card": "card", "credit card": "card", "debit card": "card",
        "netbanking": "netbanking", "net banking": "netbanking", "nb": "netbanking",
        "cash": "cash", "cheque": "cheque", "check": "cheque"}

def std_payment(val):
    s = std_text(val)
    return _PAY.get(s, s if s else "unknown")


_TRUE = {"true", "t", "yes", "y", "1", "refund", "r"}
_FALSE = {"false", "f", "no", "n", "0", "", "none", "null"}

def std_bool(val, default=False):
    """Normalize messy booleans (is_refund / is_active) -> True/False."""
    if isinstance(val, bool):
        return val
    s = std_text(val)
    if s in _TRUE:
        return True
    if s in _FALSE:
        return default
    return default


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d[\d\-\s]{8,14}$")

def valid_email(val):
    return bool(EMAIL_RE.match(str(val).strip()))

def valid_phone(val):
    return bool(PHONE_RE.match(str(val).strip()))
