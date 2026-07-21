"""Pipeline orchestrator — markdown → extraction dict + parser metadata.

The returned ``extraction`` dict is shaped exactly like the old Qwen
Stage 2 output so :mod:`shared.merge.assembler` works unchanged.

``parser_meta`` carries diagnostic info (doc_type, confidence, source
flags per major field group) so callers can detect low-confidence parses
without re-running the parser.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from .dom import ChandraDoc
from .classify import classify_doc
from . import metadata as metadata_mod
from . import parties as parties_mod
from . import tables as tables_mod

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Container for the parser output."""

    extraction: Dict[str, Any]
    doc_type: str
    parser_meta: Dict[str, Any] = field(default_factory=dict)


def _build_meta(
    doc_type: str,
    confidence: float,
    parties_found: Tuple[dict, dict],
    line_item_count: int,
    totals: dict,
    doc_type_source: str,
) -> dict:
    seller, buyer = parties_found
    return {
        "doc_type": doc_type,
        "doc_type_confidence": confidence,
        "doc_type_source": doc_type_source,
        "seller_name_found": bool(seller.get("name")),
        "seller_gstin_found": bool(seller.get("gstin")),
        "buyer_name_found": bool(buyer.get("name")),
        "buyer_gstin_found": bool(buyer.get("gstin")),
        "line_item_count": line_item_count,
        "grand_total_found": totals.get("grand_total") is not None,
        "total_before_tax_found": totals.get("total_before_tax") is not None,
        "total_tax_amount_found": totals.get("total_tax_amount") is not None,
    }


def parse(markdown: str, doc_type: Optional[str] = None) -> ParseResult:
    """Parse ChandraOCR 2 markdown into a structured extraction dict.

    Parameters
    ----------
    markdown : str
        Full OCR markdown (pages joined by ``\\n\\n---\\n\\n``).
    doc_type : str | None
        Optional document type override (``invoice`` / ``credit_note`` /
        ``purchase_order``). If omitted, the regex classifier is used.

    Returns
    -------
    ParseResult
        ``extraction`` (dict shaped for the assembler), ``doc_type``,
        and ``parser_meta``.
    """
    doc = ChandraDoc(markdown)
    text = doc.full_text()

    if doc_type:
        confidence = 1.0
        doc_type_source = "request_input"
    else:
        doc_type, confidence = classify_doc(text)
        doc_type_source = "regex_classifier"

    seller, buyer = parties_mod.extract_parties(doc)
    totals = metadata_mod.extract_totals(doc)

    if doc_type == "credit_note":
        header = metadata_mod.extract_credit_note_metadata(doc, text)
    elif doc_type == "purchase_order":
        header = metadata_mod.extract_purchase_order_metadata(doc, text)
    else:
        header = metadata_mod.extract_invoice_metadata(doc, text)

    line_items = tables_mod.extract_line_items(doc, doc_type)

    # Cross-cutting fallback: if totals extraction couldn't pick up a
    # total_tax_amount or total_before_tax (no explicit "Total Tax" /
    # "Taxable Amount" header anywhere — common for IGST-only distributor
    # invoices like GNV), sum the per-item values from line_items.
    if line_items:
        if totals.get("total_tax_amount") is None:
            tax_sum = 0.0
            found_tax = False
            for item in line_items:
                for k in ("cgst_amount", "sgst_amount", "igst_amount"):
                    v = item.get(k)
                    if v is not None:
                        tax_sum += v
                        found_tax = True
            if found_tax:
                totals["total_tax_amount"] = round(tax_sum, 2)
        if totals.get("total_before_tax") is None:
            taxable_sum = 0.0
            found_taxable = False
            for item in line_items:
                v = item.get("taxable_value")
                if v is not None:
                    taxable_sum += v
                    found_taxable = True
            if found_taxable:
                totals["total_before_tax"] = round(taxable_sum, 2)

    extraction = dict(header)
    extraction.update(
        {
            "seller": seller,
            "buyer": buyer,
            "line_items": line_items,
            "totals": totals,
            "bank_details": None,
            "purchase_order": None,
        }
    )

    parser_meta = _build_meta(
        doc_type=doc_type,
        confidence=confidence,
        parties_found=(seller, buyer),
        line_item_count=len(line_items),
        totals=totals,
        doc_type_source=doc_type_source,
    )

    logger.info(
        "Parsed markdown -> doc_type=%s, %d line_items, grand_total=%s",
        doc_type,
        len(line_items),
        totals.get("grand_total"),
    )

    return ParseResult(
        extraction=extraction,
        doc_type=doc_type,
        parser_meta=parser_meta,
    )
