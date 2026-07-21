"""Regression test: parser output for ``13042025-Mars_PD119.md`` must match
``13042025-Mars_PD119.expected.json`` exactly.

Fixture is a Mars International India pet-food tax invoice with a
two-row-per-item layout (financial row + descriptive row with the product
description in a ``colspan="5"`` cell). Exercises:

- ``extract_gst_invoice_line_items`` with description-row merging
- Horizontal-spanning totals rows inside the line-items table
  (``<td colspan="6">...</td><td colspan="5">Total ...</td><td>...</td>``)
- RCM template-placeholder handling ("Yes/No" not filled in)
- Fallback seller extraction when only buyer labels are present

Run with:
    python -m test.test_parser.test_mars
    python test/test_parser/test_mars.py
"""

import json
import sys
import math
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

from src.parser import parse  # noqa: E402

FIXTURE_STEM = "13042025-Mars_PD119"


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
    round_off = result.extraction["totals"].get("round_off") or 0

    assert _floats_equal(sum_taxable, result.extraction["totals"]["total_before_tax"]), (
        f"sum(taxable_value) {sum_taxable} != totals.total_before_tax"
    )
    assert _floats_equal(sum_tax, result.extraction["totals"]["total_tax_amount"]), (
        f"sum(taxes) {sum_tax} != totals.total_tax_amount"
    )
    # Mars has a round_off, so sum(total) + round_off == grand_total.
    assert _floats_equal(sum_total + round_off, grand_total), (
        f"sum(total_amount) {sum_total} + round_off {round_off} != grand_total {grand_total}"
    )

    print(f"PASS  {FIXTURE_STEM}")
    print(f"  doc_type        = {result.doc_type}")
    print(f"  line_items      = {len(li)}")
    print(f"  sum(taxable)    = {sum_taxable}")
    print(f"  sum(tax)        = {round(sum_tax, 2)}")
    print(f"  sum(total)      = {round(sum_total, 2)}")
    print(f"  round_off       = {round_off}")
    print(f"  grand_total     = {grand_total}")


if __name__ == "__main__":
    main()
