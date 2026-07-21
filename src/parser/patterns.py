"""Regex patterns + value-coercion helpers shared across the parser.

These patterns are calibrated against Indian GST documents (invoices,
credit notes, purchase orders). All helpers return ``None`` on miss so
callers can plug straight into the existing assembler's ``_safe`` convention.
"""

import re

# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}Z[A-Z\d]{1}\b")
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
PINCODE_RE = re.compile(r"\b(\d{6})\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")

# HSN / SAC: 2-8 consecutive digits (after stripping <br/> etc.)
HSN_RAW_RE = re.compile(r"\d+")

# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

DATE_PATTERNS = [
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",          # 30/04/2026, 12-25-26
    r"\b\d{1,2}-[A-Z]{3}-\d{2,4}\b",               # 27-DEC-2025
    r"\b\d{1,2}\s+[A-Z][a-z]+\s+\d{2,4}\b",        # 27 December 2025
]
DATE_RE = re.compile("|".join(DATE_PATTERNS))

# ---------------------------------------------------------------------------
# Amounts
# ---------------------------------------------------------------------------

# Captures an optional leading minus, optional currency token, then a number
# with optional thousands separators and decimals. Examples matched:
#   "-INR 85.00", "INR 24,18,056.00", "₹ 1,234.56", "-Rs. 7.65", "1,234.56"
_AMOUNT_RE = re.compile(
    r"(?P<sign>-)?\s*(?:₹|INR|Rs\.?|RS)\.?\s*(?P<num>-?[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PLAIN_NUM_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")


def parse_amount(text):
    """Extract a numeric amount from a cell/label value.

    Returns ``float`` or ``None``. Handles currency tokens (₹, INR, Rs),
    Indian thousand separators (commas), and a leading minus sign (credit
    notes routinely use negative amounts).
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None

    m = _AMOUNT_RE.search(s)
    negative = False
    if m:
        negative = m.group("sign") == "-"
        num_str = m.group("num")
    else:
        m = _PLAIN_NUM_RE.search(s)
        if not m:
            return None
        num_str = m.group(0)

    if num_str.startswith("-"):
        negative = True
        num_str = num_str[1:]

    num_str = num_str.replace(",", "")
    try:
        val = float(num_str)
    except ValueError:
        return None
    return -val if negative else val


def parse_int(text):
    """Parse a small integer (e.g. SI No, qty) from a string."""
    if text is None:
        return None
    m = re.search(r"\d+", str(text).replace(",", ""))
    return int(m.group(0)) if m else None


def parse_pct(text):
    """Parse a percentage like '9.00%' -> 9.0."""
    if text is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(text))
    return float(m.group(0)) if m else None


def clean_hsn(text):
    """Concatenate all digit fragments in a cell, drop non-digits.

    Implements the rule from ``skills/*.md`` for cells like
    ``<td>8504909<br/>0</td>`` -> ``"85049090"``. Returns ``None`` if no
    digits are present.
    """
    if text is None:
        return None
    digits = "".join(HSN_RAW_RE.findall(str(text)))
    return digits or None


def normalize_country(text):
    """Map common short codes to country names. Returns input unchanged if no match."""
    if not text:
        return text
    t = str(text).strip().upper()
    aliases = {
        "IN": "India",
        "IND": "India",
        "INDIA": "India",
        "US": "USA",
        "USA": "USA",
        "UK": "UK",
        "AE": "UAE",
        "UAE": "UAE",
    }
    return aliases.get(t, str(text).strip())
