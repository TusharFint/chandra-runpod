"""End-to-end: ChandraOCR markdown -> parser -> assembler -> final schema JSON.

Runs against a saved fixture, no GPU / no RunPod needed. Verifies that the
parser's output is shaped correctly for the existing assembler and that the
final result conforms to the doc-type schema shape.

Usage:
    python test/test_parser/e2e_assemble.py KA-C-27-124630
"""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

from src.parser import parse, ParseResult  # noqa: E402
from merge.assembler import get_assembler  # noqa: stdlib


class _LiftLike:
    """Minimal stand-in for the old lift result object the assembler expects."""

    def __init__(self, extraction, metadata):
        self.extraction = extraction
        self.metadata = metadata


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: e2e_assemble.py <fixture-stem>")

    stem = sys.argv[1]
    md_path = _REPO_ROOT / "test" / "output" / f"{stem}.md"
    if not md_path.exists():
        sys.exit(f"Fixture not found: {md_path}")

    markdown = md_path.read_text(encoding="utf-8")
    result = parse(markdown)

    assembler = get_assembler(result.doc_type)
    lift = _LiftLike(
        extraction=result.extraction,
        metadata={"page_count": len(markdown.split("---"))},
    )
    assembled = assembler.build(str(md_path), lift)

    # Stash pipeline metadata.
    if isinstance(assembled, dict) and "result" in assembled:
        assembled["result"].setdefault("metadata", {})
        assembled["result"]["metadata"]["pipeline"] = "chandra-parser"
        assembled["result"]["metadata"]["doc_type"] = result.doc_type
        assembled["result"]["metadata"]["parser_meta"] = result.parser_meta
    assembled["doc_type"] = result.doc_type

    out_path = md_path.with_suffix(".assembled.json")
    out_path.write_text(
        json.dumps(assembled, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"Wrote {out_path}")

    result_body = assembled.get("result", assembled)
    if result.doc_type == "credit_note":
        ref = result_body.get("credit_note_number")
        val = result_body.get("credit_note_value")
    elif result.doc_type == "purchase_order":
        ref = result_body.get("purchaseorder_number")
        val = result_body.get("totals", {}).get("grand_total")
    else:
        ref = result_body.get("invoice_number")
        val = result_body.get("invoice_value")
    print(f"  doc_type        = {result.doc_type}")
    print(f"  reference       = {ref}")
    print(f"  value           = {val}")
    print(f"  line_items      = {len(result_body.get('line_items', []))}")
    print(f"  seller          = {result_body.get('seller', {}).get('name')}")
    print(f"  buyer           = {result_body.get('buyer', {}).get('name')}")


if __name__ == "__main__":
    main()
