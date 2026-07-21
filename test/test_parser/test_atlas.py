"""Regression test: parser output for ``16012026 Atlas 7686.md`` must match
``16012026 Atlas 7686.expected.json`` exactly.

Fixture is an Atlas Distributors tax invoice with a 17-column distributor
layout (no explicit Assessable/Taxable column — taxable_value is derived
as ``Gross - Disc+Gst Benefit``). Exercises:

- ``extract_gst_invoice_line_items`` against the Atlas-style 17-col header
  (``GST%`` / ``GST Amt`` remapped to ``cgst_rate`` / ``cgst_amount``
  post-loop because they sit beside an explicit ``SGST%`` / ``SGST Amt``
  pair — bare ``GST`` means the CGST half).
- Page-2 line-items table (16-col header, missing ``SGST%``) whose only
  body row is a ``Total:`` footer — must be filtered out, not extracted
  as an item.
- Tax-details sub-table with ``Tax Desc | Tax% | Taxable Amt | Tax Amt``
  headers — recognised via the ``amt`` aliases of the totals patterns.
- Plain-text totals (``Bill Amount`` / ``Round Off``) sitting outside any
  table.
- ``Bill No`` / ``Bill Date`` headers (distributor convention) for
  ``invoice_number`` / ``invoice_date``.
- Doc-type-header party split: seller sits above the bold
  ``**Tax Invoice**`` header, buyer below it (no explicit ``Bill To``).

Run with:
    python -m test.test_parser.test_atlas
    python test/test_parser/test_atlas.py
"""

import json
import sys
import math
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

from src.parser import parse  # noqa: E402

FIXTURE_STEM = "16012026 Atlas 7686"


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
    totals = result.extraction["totals"]
    grand_total = totals["grand_total"]
    round_off = totals.get("round_off") or 0

    # Allow a 0.50 rounding slack — Atlas rounds per-item tax independently
    # of the table totals, so sum(line_items) drifts by a few paise
    # against the doc's stated totals.
    slack = 0.50
    assert _floats_equal(sum_taxable, totals["total_before_tax"], slack), (
        f"sum(taxable_value) {sum_taxable} != totals.total_before_tax {totals['total_before_tax']}"
    )
    assert _floats_equal(sum_tax, totals["total_tax_amount"], slack), (
        f"sum(taxes) {sum_tax} != totals.total_tax_amount {totals['total_tax_amount']}"
    )
    # NOTE: grand_total is intentionally NOT reconciled against
    # sum(total_amount) + round_off for this fixture. Atlas's printed
    # "Net Amt" column drifts ~0.33 from "Bill Amount" because per-item
    # tax is rounded independently of the table totals — the source
    # itself doesn't reconcile. The grand_total extracted from
    # "Bill Amount : 110,600.00" is the source of truth.

    print(f"PASS  {FIXTURE_STEM}")
    print(f"  doc_type        = {result.doc_type}")
    print(f"  line_items      = {len(li)}")
    print(f"  invoice_number  = {result.extraction['invoice_number']}")
    print(f"  sum(taxable)    = {round(sum_taxable, 2)}")
    print(f"  sum(tax)        = {round(sum_tax, 2)}")
    print(f"  sum(total)      = {round(sum_total, 2)}")
    print(f"  round_off       = {round_off}")
    print(f"  grand_total     = {grand_total}")
    print(f"  seller          = {result.extraction['seller']['name']}")
    print(f"  buyer           = {result.extraction['buyer']['name']}")


if __name__ == "__main__":
    main()
