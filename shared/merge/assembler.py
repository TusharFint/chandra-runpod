"""Stage C -- Cross-Page Merge + Assembly into target output schema.

Supports three document types via factory:
  - invoice        -> InvoiceAssembler
  - credit_note    -> CreditNoteAssembler
  - purchase_order -> PurchaseOrderAssembler
"""

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _safe(val, default=None):
    if val is None:
        return default
    return val


def _safe_num(val, default=None):
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return val
    try:
        s = str(val).strip().replace(",", "")
        if "." in s:
            return float(s)
        return int(s)
    except (ValueError, TypeError):
        return None


def _extract_pincode(address):
    if not address:
        return ""
    m = re.search(r"\b(\d{6})\b", str(address))
    return m.group(1) if m else ""


def _extract_state(address):
    if not address:
        return ""
    states = [
        "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
        "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand",
        "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
        "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
        "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh",
        "Uttarakhand", "West Bengal", "Delhi", "Jammu", "Kashmir", "Ladakh",
        "Chandigarh", "Puducherry",
    ]
    addr_lower = str(address).lower()
    for state in states:
        if state.lower() in addr_lower:
            return state
    return ""


# ---------------------------------------------------------------------------
# Shared line-item mapper
# ---------------------------------------------------------------------------

def _map_line_items(items, tax_summary):
    rows = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        row = {
            "sl.no": str(_safe(item.get("sr_no"), idx + 1)),
            "hsn": _safe(item.get("hsn_code"), ""),
            "description": _safe(item.get("description"), ""),
            "unit_price": _safe_num(item.get("rate")),
            "qty": _safe_num(item.get("quantity")),
            "net_amount": _safe_num(item.get("taxable_value")),
            "total_amount": _safe_num(item.get("total_amount")),
            "line_item_discount": str(_safe(item.get("discount"), 0)),
        }

        cgst_rate = _safe_num(item.get("cgst_rate"))
        cgst_amt = _safe_num(item.get("cgst_amount"))
        sgst_rate = _safe_num(item.get("sgst_rate"))
        sgst_amt = _safe_num(item.get("sgst_amount"))
        igst_rate = _safe_num(item.get("igst_rate"))
        igst_amt = _safe_num(item.get("igst_amount"))

        row["igst_tax"] = (
            {"tax_type": "IGST", "tax_rate": igst_rate, "tax_amount": igst_amt}
            if igst_rate is not None
            else None
        )
        row["cgst_tax"] = (
            {"tax_type": "CGST", "tax_rate": cgst_rate, "tax_amount": cgst_amt}
            if cgst_rate is not None
            else None
        )
        row["sgst_tax"] = (
            {"tax_type": "SGST", "tax_rate": sgst_rate, "tax_amount": sgst_amt}
            if sgst_rate is not None
            else None
        )
        row["utsgst_tax"] = None
        rows.append(row)
    return rows


def _map_party(party):
    """Map a seller/buyer dict from lift extraction to output schema party."""
    if not party:
        party = {}
    addr = _safe(party.get("address"), "")
    bank = party.get("bank_details") or {}
    return {
        "name": _safe(party.get("name")),
        "gst": _safe(party.get("gstin")),
        "pan": _safe(party.get("pan")),
        "address": addr,
        "state": _safe(party.get("state")) or _extract_state(addr),
        "pincode": _safe(party.get("pincode")) or _extract_pincode(addr),
        "country": _safe(party.get("country"), "India"),
        "bank_details": {
            "account_name": _safe(bank.get("account_name")),
            "account_number": _safe(bank.get("account_number")),
            "bank_name": _safe(bank.get("bank_name")),
            "branch": _safe(bank.get("branch")),
            "ifsc": _safe(bank.get("ifsc_code")),
        },
        "contact_details": {
            "phone": _safe(party.get("phone")),
            "email": _safe(party.get("email")),
        },
    }


def _map_buyer(buyer):
    """Map buyer with extra billing/shipping addresses."""
    if not buyer:
        buyer = {}
    base = _map_party(buyer)
    buyer_addr = _safe(buyer.get("address"), "")
    base["billing_address"] = _safe(buyer.get("billing_address"), buyer_addr)
    base["shipping_address"] = _safe(buyer.get("shipping_address"), buyer_addr)
    return base


# ---------------------------------------------------------------------------
# Assemblers
# ---------------------------------------------------------------------------

class InvoiceAssembler:
    """Assembles Lift extraction results into the target output_schema.json format."""

    def build(self, pdf_path, lift_result):
        filename = Path(pdf_path).name
        extraction = getattr(lift_result, "extraction", None) or {}
        metadata = getattr(lift_result, "metadata", None) or {}
        page_count = metadata.get("page_count") or self._count_pages(pdf_path)

        result = self._map_extraction(extraction)
        result["metadata"] = {
            "source_filename": filename,
            "page_count": page_count,
        }
        return {"result": result}

    def _map_extraction(self, ext):
        totals = ext.get("totals") or {}
        tax_summary = ext.get("tax_summary") or {}
        line_items = ext.get("line_items") or []

        result = {
            "invoice_number": _safe(ext.get("invoice_number")),
            "invoice_id": _safe(ext.get("invoice_number")),
            "invoice_date": _safe(ext.get("invoice_date")),
            "invoice_value": _safe_num(totals.get("grand_total")),
            "adjustment": _safe_num(totals.get("round_off"), 0),
            "bill_discount": "0.00",
            "order_number": _safe(ext.get("order_number")),
            "order_date": _safe(ext.get("order_date")),
            "purchase_order": {
                "purchaseorder_number": _safe(ext.get("order_number")),
                "delivery_date": _safe(ext.get("order_date")),
            },
            "seller": _map_party(ext.get("seller") or {}),
            "buyer": _map_buyer(ext.get("buyer") or {}),
            "place_of_supply": _safe(ext.get("place_of_supply")),
            "place_of_delivery": _safe(ext.get("place_of_delivery")),
            "line_items": _map_line_items(line_items, tax_summary),
            "transaction_id": _safe(ext.get("invoice_number")),
            "date_time": _safe(ext.get("invoice_date")),
            "mode_of_payment": _safe(ext.get("mode_of_payment")),
        }
        return result

    @staticmethod
    def _count_pages(pdf_path):
        try:
            import pypdfium2
            doc = pypdfium2.PdfDocument(pdf_path)
            count = len(doc)
            doc.close()
            return count
        except Exception:
            return None


class CreditNoteAssembler:
    """Assembles credit note extraction into the target output format."""

    def build(self, pdf_path, lift_result):
        filename = Path(pdf_path).name
        extraction = getattr(lift_result, "extraction", None) or {}
        metadata = getattr(lift_result, "metadata", None) or {}
        page_count = metadata.get("page_count") or InvoiceAssembler._count_pages(pdf_path)

        result = self._map_extraction(extraction)
        result["metadata"] = {
            "source_filename": filename,
            "page_count": page_count,
        }
        return {"result": result}

    def _map_extraction(self, ext):
        totals = ext.get("totals") or {}
        tax_summary = ext.get("tax_summary") or {}
        line_items = ext.get("line_items") or []
        bank = ext.get("bank_details") or {}
        purchase_order = ext.get("purchase_order") or {}

        seller = _map_party(ext.get("seller") or {})
        buyer = _map_buyer(ext.get("buyer") or {})

        result = {
            "credit_note_number": _safe(ext.get("credit_note_number")),
            "credit_note_date": _safe(ext.get("credit_note_date")),
            "invoice_number": _safe(ext.get("invoice_number")),
            "credit_note_value": _safe_num(totals.get("grand_total")),
            "adjustment": _safe_num(totals.get("round_off"), 0),
            "order_number": _safe(ext.get("order_number")),
            "order_date": _safe(ext.get("order_date")),
            "purchase_order": {
                "purchaseorder_number": _safe(purchase_order.get("purchaseorder_number")),
                "delivery_date": _safe(purchase_order.get("delivery_date")),
            },
            "seller": seller,
            "buyer": buyer,
            "place_of_supply": _safe(ext.get("place_of_supply")),
            "place_of_delivery": _safe(ext.get("place_of_delivery")),
            "is_rcm": _safe(ext.get("is_rcm"), False),
            "rcm_description": _safe(ext.get("rcm_description")),
            "line_items": _map_line_items(line_items, tax_summary),
            "transaction_id": _safe(ext.get("credit_note_number")),
            "date_time": _safe(ext.get("credit_note_date")),
            "mode_of_payment": _safe(ext.get("mode_of_payment")),
        }
        return result


class PurchaseOrderAssembler:
    """Assembles purchase order extraction into the target output format."""

    def build(self, pdf_path, lift_result):
        filename = Path(pdf_path).name
        extraction = getattr(lift_result, "extraction", None) or {}
        metadata = getattr(lift_result, "metadata", None) or {}
        page_count = metadata.get("page_count") or InvoiceAssembler._count_pages(pdf_path)

        result = self._map_extraction(extraction)
        result["metadata"] = {
            "source_filename": filename,
            "page_count": page_count,
        }
        return {"result": result}

    def _map_extraction(self, ext):
        totals = ext.get("totals") or {}
        tax_summary = ext.get("tax_summary") or {}
        line_items = ext.get("line_items") or []

        buyer = _map_party(ext.get("buyer") or {})
        seller = _map_party(ext.get("seller") or {})

        result = {
            "purchaseorder_number": _safe(ext.get("purchaseorder_number")),
            "order_number": _safe(ext.get("order_number")),
            "order_date": _safe(ext.get("order_date")),
            "delivery_date": _safe(ext.get("delivery_date")),
            "buyer": buyer,
            "seller": seller,
            "place_of_supply": _safe(ext.get("place_of_supply")),
            "place_of_delivery": _safe(ext.get("place_of_delivery")),
            "shipping_address": _safe(ext.get("shipping_address")),
            "line_items": _map_line_items(line_items, tax_summary),
            "totals": {
                "sub_total": _safe_num(totals.get("sub_total") or totals.get("total_before_tax")),
                "total_tax_amount": _safe_num(totals.get("total_tax_amount")),
                "grand_total": _safe_num(totals.get("grand_total")),
                "currency": _safe(totals.get("currency"), "INR"),
            },
            "mode_of_payment": _safe(ext.get("mode_of_payment")),
            "notes": _safe(ext.get("notes")),
            "transaction_id": _safe(ext.get("purchaseorder_number")),
            "date_time": _safe(ext.get("order_date")),
        }
        return result


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

_SCHEMA_FILE_MAP = {
    "invoice": "gst_invoice_schema.json",
    "credit_note": "credit_note_schema.json",
    "purchase_order": "purchase_order_schema.json",
}


def get_schema(doc_type, schemas_dir=None):
    """Load the JSON schema for *doc_type* from *schemas_dir*.

    Falls back to the invoice schema if *doc_type* is unknown.
    """
    if schemas_dir is None:
        here = Path(__file__).resolve().parent.parent.parent
        schemas_dir = here / "schemas"
    else:
        schemas_dir = Path(schemas_dir)

    filename = _SCHEMA_FILE_MAP.get(doc_type, _SCHEMA_FILE_MAP["invoice"])
    schema_path = schemas_dir / filename
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_assembler(doc_type):
    """Return the assembler instance for *doc_type*."""
    if doc_type == "credit_note":
        return CreditNoteAssembler()
    elif doc_type == "purchase_order":
        return PurchaseOrderAssembler()
    return InvoiceAssembler()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def save_json(data, output_path, indent=2):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False, default=str)
    logger.info(f"Wrote {output_path}")
