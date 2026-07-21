"""Markdown-based document type classifier.

A thin companion to ``shared/classify/classifier.py`` (which classifies a
PDF via pypdfium2 text extraction). This module classifies the chandra
markdown directly — no PDF access needed at parse time.

Rule precedence (most specific first):

1. ``credit_note`` — only "credit note" / "credit memo" trigger it.
2. ``invoice`` — "tax invoice", "gst invoice", "invoice". Checked
   *before* purchase_order because most invoices reference a customer
   PO number ("Cust PO# …", "P.O. No …") and that reference must not
   override the invoice classification.
3. ``purchase_order`` — explicit "Purchase Order" title or "PO No"/"PO#"
   *without* any invoice keyword nearby.
"""

import re

DOCUMENT_TYPES = ("invoice", "credit_note", "purchase_order")

_RULES = [
    ("credit_note", [r"credit\s*note", r"credit\s*memo"]),
    ("invoice", [r"tax\s*invoice", r"gst\s*invoice", r"\binvoice\b", r"commercial\s*invoice"]),
    ("purchase_order", [r"purchase\s*order", r"p\.?\s*o\.?\s*no", r"p\.?\s*o\.?\s*#"]),
]


def classify_doc(text: str):
    """Classify chandra markdown.

    Returns ``(doc_type, confidence)``. Confidence is 0.95 on a clear
    keyword hit, 0.3 when defaulting to ``invoice``.
    """
    if not text:
        return "invoice", 0.3
    lower = text.lower()
    for doc_type, patterns in _RULES:
        for p in patterns:
            if re.search(p, lower):
                return doc_type, 0.95
    return "invoice", 0.3
