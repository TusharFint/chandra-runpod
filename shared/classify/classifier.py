"""Document classification via text extraction + keyword rules.

Extracts text from the first page of a PDF using pypdfium2 and applies
keyword heuristics to determine the document type (invoice, credit_note,
purchase_order). No GPU required -- runs in milliseconds.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DOCUMENT_TYPES = ("invoice", "credit_note", "purchase_order")

_CLASSIFICATION_RULES = [
    (
        "credit_note",
        [
            r"\bcredit\s*note\b",
            r"\bcredit\s*memo\b",
            r"\bcredit\s*notes\b",
        ],
    ),
    (
        "purchase_order",
        [
            r"\bpurchase\s*order\b",
            r"\bp\.?\s*o\.?\s*no\b",
            r"\bp\.?\s*o\.?\s*#\b",
            r"\bpurchase\s*order\s*no\b",
        ],
    ),
    (
        "invoice",
        [
            r"\btax\s*invoice\b",
            r"\bgst\s*invoice\b",
            r"\binvoice\b",
            r"\bcommercial\s*invoice\b",
            r"\bbill\s*to\b",
        ],
    ),
]


def _extract_text(pdf_path: str, max_pages: int = 1) -> str:
    """Extract text from the first *max_pages* pages of a PDF."""
    try:
        import pypdfium2

        doc = pypdfium2.PdfDocument(pdf_path)
        parts = []
        for i in range(min(max_pages, len(doc))):
            page = doc[i]
            textpage = page.get_textpage()
            parts.append(textpage.get_text_range())
            textpage.close()
            page.close()
        doc.close()
        return " ".join(parts)
    except Exception as e:
        logger.warning(f"Text extraction failed for {pdf_path}: {e}")
        return ""


def classify_document(pdf_path: str, max_pages: int = 1):
    """Classify a PDF by document type.

    Returns ``(doc_type, confidence)`` where *doc_type* is one of
    ``DOCUMENT_TYPES`` and *confidence* is a float in [0, 1].
    """
    text = _extract_text(pdf_path, max_pages=max_pages)
    if not text:
        logger.info("No text extracted -- defaulting to 'invoice'")
        return "invoice", 0.3

    text_lower = text.lower()

    for doc_type, patterns in _CLASSIFICATION_RULES:
        for pattern in patterns:
            if re.search(pattern, text_lower):
                logger.info(
                    f"Classified '{Path(pdf_path).name}' as '{doc_type}' "
                    f"(matched: {pattern})"
                )
                return doc_type, 0.95

    logger.info(f"No keyword match -- defaulting to 'invoice'")
    return "invoice", 0.3
