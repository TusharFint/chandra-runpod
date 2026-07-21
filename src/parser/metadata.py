"""Header metadata + totals extraction.

Calibrated on the Amazon credit note layout. Invoice / purchase-order
rules are stubbed pending their respective calibration fixtures.
"""

import re
import logging

from .patterns import parse_amount
from .tables import _is_header_row, _flatten_header

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Labelled-field helpers (work on plain markdown text)
# ---------------------------------------------------------------------------

def _labelled(text: str, *patterns) -> str:
    """Return the captured group for the first matching label regex.

    Markdown bold/italic markers (``**``, ``*``) are stripped from the
    input text before matching so patterns don't have to defend against
    ``**Label:** value`` shapes. Captured values are also trimmed of
    whitespace and trailing punctuation.
    """
    cleaned_text = re.sub(r"\*+", "", text or "")
    for pat in patterns:
        m = re.search(pat, cleaned_text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = m.group(1).strip().rstrip(",;:").strip()
            if val:
                return val
    return None


# ---------------------------------------------------------------------------
# Reverse Charge Mechanism detection
# ---------------------------------------------------------------------------

# Phrases that explicit a YES / affirmative answer to a reverse-charge query.
# Excludes the template placeholder "Yes/No" / "Yes / No" which appears in
# the boilerplate label of many GST invoices ("Whether ... reverse charge
# Yes/No") before the actual answer is filled in.
_RCM_YES_RE = re.compile(
    r"reverse\s*charge\s*[:\-]?\s*(?:yes|y\b|applicable|applies)\b(?!\s*/\s*(?:no|n))",
    re.IGNORECASE,
)
# Phrases that explicit a NO / negative answer.
_RCM_NO_RE = re.compile(
    r"reverse\s*charge\s*[:\-]?\s*(?:no|n\b|not\s*applicable|n\.?a\.?)\b",
    re.IGNORECASE,
)
# Template placeholder "Yes/No" — neutral, should not by itself trigger RCM.
# Allows up to 80 chars (incl. newlines) between "reverse charge" and the
# placeholder, since many templates put "Yes/No" on the next line.
_RCM_PLACEHOLDER_RE = re.compile(
    r"reverse\s*charge[\s\S]{0,80}?yes\s*/\s*no\b",
    re.IGNORECASE,
)
# Bare mention of "reverse charge" (without qualifier).
_RCM_BARE_RE = re.compile(r"reverse\s*charge", re.IGNORECASE)
# "GST payable by Consigner" affirmative indicator.
_RCM_CONSIGNER_RE = re.compile(
    r"gst\s*payable\s*by\s*consigner", re.IGNORECASE
)


def detect_rcm(text: str) -> tuple:
    """Return ``(is_rcm, rcm_description)`` for an invoice / credit note.

    Order of precedence:
      1. Explicit "No" qualifier  -> is_rcm=False, description=qualifier
      2. Explicit "Yes" qualifier -> is_rcm=True,  description=qualifier
      3. "GST Payable by Consigner" -> is_rcm=True,  description=that phrase
      4. Template "Yes/No" placeholder -> (False, None) — neutral, do not
         infer RCM from the boilerplate label alone.
      5. Bare "Reverse Charge" mention (no qualifier) -> is_rcm=True,
         description="Reverse Charge Mechanism applies"
      6. No mention -> (False, None)
    """
    if not text:
        return False, None

    # Look for the full RCM statement to extract a description.
    # The qualifier is constrained to a single short token run on the same
    # line — no newlines (markdown page breaks "---" must not bleed in).
    stmt_match = re.search(
        r"(?:whether\s+tax\s+is\s+payable\s+under\s+)?reverse\s*charge\s*[:\-]?\s*([A-Za-z0-9./\s-]{0,30}?)\s*(?:\n|$)",
        text,
        re.IGNORECASE,
    )
    qualifier = (
        stmt_match.group(1).strip().rstrip(",;:").strip() if stmt_match else None
    )

    if _RCM_NO_RE.search(text):
        return False, qualifier or "Reverse Charge: No"

    if _RCM_YES_RE.search(text):
        return True, qualifier or "Reverse Charge: Yes"

    if _RCM_CONSIGNER_RE.search(text):
        return True, "GST Payable by Consigner"

    # If the only mention is the "Yes/No" template placeholder, treat as
    # not applicable (the actual answer wasn't filled in).
    if _RCM_PLACEHOLDER_RE.search(text):
        # Strip the placeholder from the bare-mention check below by
        # removing it from the text before the bare-mention test.
        stripped = _RCM_PLACEHOLDER_RE.sub("", text)
        if _RCM_BARE_RE.search(stripped):
            return True, "Reverse Charge Mechanism applies"
        return False, None

    if _RCM_BARE_RE.search(text):
        return True, "Reverse Charge Mechanism applies"

    return False, None


# ---------------------------------------------------------------------------
# Totals — scan every table for known label rows
# ---------------------------------------------------------------------------

# Order matters: most specific patterns first. The first matching pattern
# wins for a given row; rows that don't match any pattern are skipped.
_TOTALS_PATTERNS = [
    (re.compile(r"total\s*invoice\s*(?:amount|value)", re.IGNORECASE), "grand_total"),
    (re.compile(r"amount\s*with\s*tax", re.IGNORECASE), "grand_total"),
    (re.compile(r"invoice\s*value", re.IGNORECASE), "grand_total"),
    (re.compile(r"grand\s*total", re.IGNORECASE), "grand_total"),
    (re.compile(r"^\s*net\s*amount\b", re.IGNORECASE), "grand_total"),
    (re.compile(r"total\s*gst\s*tax", re.IGNORECASE), "total_tax_amount"),
    (re.compile(r"sub\s*total\s*of\s*gst\s*amount", re.IGNORECASE), "total_tax_amount"),
    (re.compile(r"sub\s*total\s*of\s*tax", re.IGNORECASE), "total_tax_amount"),
    (re.compile(r"total\s*tax\s*amount", re.IGNORECASE), "total_tax_amount"),
    (re.compile(r"^\s*tax\s*amt\b", re.IGNORECASE), "total_tax_amount"),
    (re.compile(r"^\s*tax\s*amount\b", re.IGNORECASE), "total_tax_amount"),
    (re.compile(r"^\s*total\s*tax\s*$", re.IGNORECASE), "total_tax_amount"),
    (re.compile(r"total\s*assessable\s*(?:value|amount)", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"sub\s*total\s*of\s*fees?\s*amount", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"total\s*amount\s*before\s*tax", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"sub\s*total\s*before\s*tax", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"^\s*taxable\s*amount\b", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"^\s*taxable\s*amt\b", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"^\s*taxable\s*val(?:ue)?\b", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"^\s*assessable\s*amount\b", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"^\s*assessable\s*amt\b", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"^\s*assessable\s*val(?:ue)?\b", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"round(?:ing)?[\s-]?off", re.IGNORECASE), "round_off"),
    (re.compile(r"^\s*total\s*$", re.IGNORECASE), "grand_total"),
]


def _match_totals_key(label: str) -> str:
    for regex, key in _TOTALS_PATTERNS:
        if regex.search(label):
            return key
    return None


def _row_cells(tr) -> list:
    out = []
    for c in tr.find_all(["td", "th"]):
        for br in c.find_all("br"):
            br.replace_with("\n")
        out.append(c.get_text().strip())
    return out


def extract_totals(doc) -> dict:
    """Scan all tables for label/amount rows and assemble a totals dict.

    Handles three layouts:

    - **Horizontal**: a header row of labels followed by a single body
      row of amounts at the same column positions (Royal Canin / standard
      GST e-invoice totals table). Multi-row headers with rowspan /
      colspan (GNV) are flattened first via :func:`_flatten_header` so
      column indices line up with body cells. Processed *first* — these
      typically have clean ``Rs.X,XXX.XX`` formatting.
    - **Vertical**: each row is ``[label, amount, ...]`` (Amazon style).
      Processed as a fallback for any totals key not yet set, so messy
      page-1 summary tables can't overwrite clean page-3 GST values.
    - **Plain text**: ``Bill Amount : 110,600.00`` outside any table
      (Atlas distributor layout).

    Returns:
        {
          "total_before_tax": float | None,
          "total_tax_amount": float | None,
          "round_off": float | None,
          "grand_total": float | None,
        }
    """
    totals = {
        "total_before_tax": None,
        "total_tax_amount": None,
        "round_off": None,
        "grand_total": None,
    }

    # Pass 1: horizontal tables (header row of labels + value row).
    # Skip line-items tables (those with a description / item / product /
    # hsn column) — their "Total" / "Taxable Amount" columns hold
    # per-item values, not document totals. For single-item invoices
    # (GNV) these would otherwise be mis-extracted as grand_total.
    _LINE_ITEM_MARKER_RES = (
        re.compile(r"\bdescription\b", re.IGNORECASE),
        re.compile(r"\bhsn\b", re.IGNORECASE),
        re.compile(r"\bproduct\s*name\b", re.IGNORECASE),
        re.compile(r"^\s*item\s*$", re.IGNORECASE),
    )
    for table in doc.tables:
        rows = table.find_all("tr")
        if not rows:
            continue
        first = rows[0]
        if not _is_header_row(first):
            continue
        header_cells = _flatten_header(table)
        if any(
            any(p.search(c) for p in _LINE_ITEM_MARKER_RES)
            for c in header_cells
            if c
        ):
            continue
        keys = [_match_totals_key(c) for c in header_cells]
        mapped = [k for k in keys if k]
        if len(mapped) < 2:
            continue
        for next_row in rows[1:]:
            if _is_header_row(next_row):
                continue
            value_cells = _row_cells(next_row)
            if not value_cells:
                continue
            for i, key in enumerate(keys):
                if key is None or totals.get(key) is not None:
                    continue
                if i >= len(value_cells):
                    continue
                val = parse_amount(value_cells[i])
                if val is not None:
                    totals[key] = val
            break

    # Pass 2: vertical tables (label anywhere in row, amount in a later cell).
    # Only fills keys that are still None. "Anywhere in row" handles
    # colspan-heavy layouts like Mars: ``<td colspan="6"></td><td>Total
    # Assessable Value</td><td>58,015.08</td>``.
    for table in doc.tables:
        for tr in table.find_all("tr"):
            if _is_header_row(tr):
                continue
            cells = _row_cells(tr)
            if len(cells) < 2:
                continue
            # Find first cell matching a totals key, then take the next
            # parseable amount after it.
            for i, cell in enumerate(cells):
                key = _match_totals_key(cell)
                if key is None:
                    continue
                if totals.get(key) is not None:
                    break
                for value_cell in cells[i + 1:]:
                    val = parse_amount(value_cell)
                    if val is not None:
                        totals[key] = val
                        break
                break

    # Derive total_tax_amount from CGST+SGST+IGST rows if we still don't have it.
    if totals["total_tax_amount"] is None:
        _derive_tax_from_components(doc, totals)

    # Pass 3: plain-text label/value pairs outside any table. Some layouts
    # (Atlas distributor invoices) put totals as bare markdown like
    # "Bill Amount : 110,600.00" instead of in a table.
    _extract_totals_from_text(doc, totals)

    return totals


# Plain-text totals patterns (applied to markdown outside any <table>).
# Each tuple is (regex, totals_key). First match wins per key. Patterns
# are intentionally permissive about spacing around the colon.
_TOTALS_TEXT_PATTERNS = [
    (re.compile(r"bill\s*amount\s*[:\-]\s*([\d,]+\.\d+)", re.IGNORECASE), "grand_total"),
    (re.compile(r"balance\s*due\s*[:\-]?\s*₹?\s*([\d,]+\.\d+)", re.IGNORECASE), "grand_total"),
    (re.compile(r"invoice\s*value\s*[:\-]\s*([\d,]+\.\d+)", re.IGNORECASE), "grand_total"),
    (re.compile(r"grand\s*total\s*[:\-]\s*([\d,]+\.\d+)", re.IGNORECASE), "grand_total"),
    (re.compile(r"receivable\s*amt\s*[:\-]?\s*\*{0,2}\s*([\d,]+\.\d+)", re.IGNORECASE), "grand_total"),
    (re.compile(r"taxable\s*amt\s*[:\-]\s*([\d,]+\.\d+)", re.IGNORECASE), "total_before_tax"),
    (re.compile(r"^\s*tax\s*amt\s*[:\-]\s*([\d,]+\.\d+)", re.IGNORECASE | re.MULTILINE), "total_tax_amount"),
    (re.compile(r"total\s*tax\s*amt\s*[:\-]\s*([\d,]+\.\d+)", re.IGNORECASE), "total_tax_amount"),
    (re.compile(r"round(?:ing)?[\s-]?off\s*[:\-]\s*(-?[\d,]+\.\d+)", re.IGNORECASE), "round_off"),
    (re.compile(r"^\s*rounding\s+(-?[\d,]+\.\d+)", re.IGNORECASE | re.MULTILINE), "round_off"),
]


def _extract_totals_from_text(doc, totals: dict) -> None:
    """Fill any still-None totals keys from plain-text label/value pairs.

    Strips all ``<table>...</table>`` blocks first so we only look at
    labelled fields sitting in the surrounding markdown body. Atlas-style
    distributor invoices put totals like ``Bill Amount : 110,600.00`` as
    bare markdown rather than inside a table.
    """
    text = doc.full_text()
    text = re.sub(r"<table[\s\S]*?</table>", "", text, flags=re.IGNORECASE)
    for regex, key in _TOTALS_TEXT_PATTERNS:
        if totals.get(key) is not None:
            continue
        m = regex.search(text)
        if m:
            val = parse_amount(m.group(1))
            if val is not None:
                totals[key] = val


def _derive_tax_from_components(doc, totals: dict) -> None:
    """If ``total_tax_amount`` is missing, sum CGST/SGST/IGST rows from tables.

    Many Royal Canin/Mars-style invoices list ``CGST 9%`` and ``SGST 9%`` as
    separate rows but no aggregate ``Total Tax`` row. Sum them here.

    Handles colspan layouts where the tax-type label is not in cells[0]
    (e.g. Mars: ``<td colspan="5">The total CGST applicable</td><td>5,221.36</td>``).
    """
    tax_label_res = (
        re.compile(r"\bcgst\b", re.IGNORECASE),
        re.compile(r"\bsgst\b", re.IGNORECASE),
        re.compile(r"\bigst\b", re.IGNORECASE),
        re.compile(r"\butgst\b", re.IGNORECASE),
    )
    tax_total = 0.0
    found_any = False
    for table in doc.tables:
        for tr in table.find_all("tr"):
            if _is_header_row(tr):
                continue
            cells = _row_cells(tr)
            if len(cells) < 2:
                continue
            # Find first cell that looks like a tax-component label.
            label_idx = None
            for i, cell in enumerate(cells):
                if any(p.search(cell) for p in tax_label_res):
                    label_idx = i
                    break
            if label_idx is None:
                continue
            # Take the next parseable amount after the label.
            for cell in cells[label_idx + 1:]:
                val = parse_amount(cell)
                if val is not None:
                    tax_total += val
                    found_any = True
                    break
    if found_any:
        totals["total_tax_amount"] = round(tax_total, 2)


# ---------------------------------------------------------------------------
# Per-doc-type header extractors
# ---------------------------------------------------------------------------

def extract_credit_note_metadata(doc, text: str) -> dict:
    out = {
        "credit_note_number": None,
        "credit_note_date": None,
        "invoice_number": None,
        "adjustment": None,
        "place_of_supply": None,
        "place_of_delivery": None,
        "order_number": None,
        "order_date": None,
        "is_rcm": False,
        "rcm_description": None,
        "mode_of_payment": None,
        "reason_for_credit": None,
    }

    out["credit_note_number"] = _labelled(
        text,
        r"credit\s*note\s*(?:number|no\.?)\s*:\s*(\S+)",
    )
    out["credit_note_date"] = _labelled(
        text,
        r"credit\s*note\s*date\s*:\s*(\S+)",
    )
    out["place_of_supply"] = _labelled(text, r"place\s*of\s*supply\s*:\s*(.+?)\s*$")
    out["place_of_delivery"] = _labelled(
        text,
        r"place\s*of\s*delivery\s*:\s*(.+?)\s*$",
    )
    out["order_number"] = _labelled(
        text,
        r"\border\s*(?:number|no\.?)\s*:\s*(\S+)",
        r"\bp\.?\s*o\.?\s*(?:number|no\.?)\s*:\s*(\S+)",
    )
    out["order_date"] = _labelled(text, r"order\s*date\s*:\s*(\S+)")
    out["mode_of_payment"] = _labelled(
        text,
        r"mode\s*of\s*payment\s*:\s*(.+?)\s*$",
        r"payment\s*terms\s*:\s*(.+?)\s*$",
    )
    out["reason_for_credit"] = _labelled(
        text, r"reason\s*for\s*credit\s*:\s*(.+?)\s*$"
    )

    # RCM detection
    out["is_rcm"], out["rcm_description"] = detect_rcm(text)

    return out


def extract_invoice_metadata(doc, text: str) -> dict:
    out = {
        "invoice_number": None,
        "invoice_date": None,
        "order_number": None,
        "order_date": None,
        "delivery_date": None,
        "place_of_supply": None,
        "place_of_delivery": None,
        "mode_of_payment": None,
        "is_rcm": False,
        "rcm_description": None,
        "amount_in_words": None,
    }

    out["invoice_number"] = _labelled(
        text,
        r"invoice\s*(?:number|no\.?)\s*:\s*(\S+)",
        r"invoice\s*#?\s*:\s*(\S+)",
        r"\bbill\s*no\.?\s*:\s*(\S+)",
    )
    out["invoice_date"] = (
        _labelled(text, r"invoice\s*date\s*[:\-]?\s*(\S+)")
        or _labelled(text, r"^\s*date\s*[:\-]\s*(\S+)")
        or _labelled(text, r"\bbill\s*date\s*:\s*(\S+)")
    )
    out["order_number"] = _labelled(
        text,
        r"\border\s*(?:number|no\.?)\s*:\s*(\S+)",
        r"\bp\.?\s*o\.?\s*(?:number|no\.?)\s*:\s*(\S+)",
    )
    out["order_date"] = _labelled(text, r"order\s*date\s*:\s*(\S+)")
    out["delivery_date"] = _labelled(
        text,
        r"delivery\s*date\s*:\s*(\S+)",
        r"unloading\s*date\s*:\s*(\S+)",
    )
    out["place_of_supply"] = _labelled(text, r"place\s*of\s*supply\s*:\s*(.+?)\s*$")
    out["place_of_delivery"] = _labelled(
        text,
        r"place\s*of\s*delivery\s*:\s*(.+?)\s*$",
    )
    out["is_rcm"], out["rcm_description"] = detect_rcm(text)
    return out

def extract_purchase_order_metadata(doc, text: str) -> dict:
    out = {
        "purchaseorder_number": None,
        "order_number": None,
        "order_date": None,
        "delivery_date": None,
        "place_of_supply": None,
        "place_of_delivery": None,
        "shipping_address": None,
        "mode_of_payment": None,
        "notes": None,
    }

    out["purchaseorder_number"] = _labelled(
        text,
        r"p\.?\s*o\.?\s*(?:number|no\.?)\s*:\s*(\S+)",
        r"purchase\s*order\s*(?:number|no\.?)\s*:\s*(\S+)",
        r"po\s*#\s*:\s*(\S+)",
    )
    out["order_number"] = out["purchaseorder_number"]
    out["order_date"] = _labelled(
        text, r"(?:order\s*)?date\s*:\s*(\S+)"
    )
    out["delivery_date"] = _labelled(text, r"delivery\s*date\s*:\s*(\S+)")
    out["place_of_supply"] = _labelled(text, r"place\s*of\s*supply\s*:\s*(.+?)\s*$")
    out["place_of_delivery"] = _labelled(
        text, r"place\s*of\s*delivery\s*:\s*(.+?)\s*$"
    )
    out["mode_of_payment"] = _labelled(
        text, r"payment\s*terms\s*:\s*(.+?)\s*$"
    )
    out["notes"] = _labelled(
        text,
        r"(?:comments|special\s*instructions|notes)\s*:\s*(.+?)\s*$",
    )
    return out
