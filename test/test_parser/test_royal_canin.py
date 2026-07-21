"""Regression test: parser output for ``14042026-ROYAL_CANIN_PD96.md`` must
match ``14042026-ROYAL_CANIN_PD96.expected.json`` exactly.

Fixture is a Royal Canin India pet-food tax invoice using the standard
Indian GST e-invoice schema (13-column single-row-per-item table with
HSN + Quantity-UOM + Item Rate + Assessable Amount + GST Rate + CGST +
SGST + Line Value). Exercises the new ``extract_gst_invoice_line_items``
extractor, the labelled-sections party parser (``Bill To`` / ``Supplier``),
and the horizontal totals table.

Run with:
    python -m test.test_parser.test_royal_canin
    python test/test_parser/test_royal_canin.py
"""

import json
import sys
import math
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

from src.parser import parse  # noqa: E402

FIXTURE_STEM = "14042026-ROYAL_CANIN_PD96"


def _floats_equal(a, b, eps=1e-9):
    if a is None or b is None:
        return a is b
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=eps)


def _deep_equal(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False, f"{path}: key mismatch {set(a.keys()) ^ set(b.keys())}"
        for k in a:
            ok, msg = _deep_equal(a[k], b[k], f"{path}.{k}")
            if not ok:
                return False, msg
        return True, ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False, f"{path}: list length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            ok, msg = _deep_equal(x, y, f"{path}[{i}]")
            if not ok:
                return False, msg
        return True, ""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b, f"{path}: bool mismatch {a!r} != {b!r}"
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return _floats_equal(a, b), f"{path}: numeric mismatch {a!r} != {b!r}"
    return a == b, f"{path}: value mismatch {a!r} != {b!r}"


def main():
    md_path = _REPO_ROOT / "test" / "output" / f"{FIXTURE_STEM}.md"
    expected_path = _REPO_ROOT / "test" / "output" / f"{FIXTURE_STEM}.expected.json"

    markdown = md_path.read_text(encoding="utf-8")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    result = parse(markdown)

    assert result.doc_type == expected["doc_type"], (
        f"doc_type: {result.doc_type!r} != {expected['doc_type']!r}"
    )

    ok, msg = _deep_equal(result.extraction, expected["extraction"], "extraction")
    if not ok:
        print("FAIL:", msg)
        sys.exit(1)

    li = result.extraction["line_items"]
    sum_taxable = sum((i.get("taxable_value") or 0) for i in li)
    sum_tax = sum(
        (i.get("sgst_amount") or 0)
        + (i.get("cgst_amount") or 0)
        + (i.get("igst_amount") or 0)
        for i in li
    )
    sum_total = sum((i.get("total_amount") or 0) for i in li)
    grand_total = result.extraction["totals"]["grand_total"]

    assert _floats_equal(sum_taxable, result.extraction["totals"]["total_before_tax"]), (
        f"sum(taxable_value) {sum_taxable} != totals.total_before_tax"
    )
    assert _floats_equal(sum_tax, result.extraction["totals"]["total_tax_amount"]), (
        f"sum(taxes) {sum_tax} != totals.total_tax_amount"
    )
    assert _floats_equal(sum_total, grand_total), (
        f"sum(total_amount) {sum_total} != totals.grand_total {grand_total}"
    )

    print(f"PASS  {FIXTURE_STEM}")
    print(f"  doc_type        = {result.doc_type}")
    print(f"  line_items      = {len(li)}")
    print(f"  sum(taxable)    = {sum_taxable}")
    print(f"  sum(tax)        = {round(sum_tax, 2)}")
    print(f"  sum(total)      = {round(sum_total, 2)}")
    print(f"  grand_total     = {grand_total}")


if __name__ == "__main__":
    main()
