"""Local end-to-end test (no RunPod needed).

Runs the full pipeline on a single PDF:
    Classify -> ChandraOCR 2 -> Qwen 2.5 Extraction -> Assemble

Usage:
    python test_local.py path/to/invoice.pdf
    python test_local.py path/to/invoice.pdf --save-markdown
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Resolve repo root (parent of test/)
_REPO_ROOT = Path(__file__).resolve().parent.parent

# sys.path: repo root for `src.*`, shared/ for classifier + assembler
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

OUTPUT_DIR = _REPO_ROOT / "test" / "output"
SKILLS_DIR = _REPO_ROOT / "skills"


def main():
    p = argparse.ArgumentParser(description="Local test: ChandraOCR + Qwen 2.5")
    p.add_argument("pdf", help="Path to PDF file")
    p.add_argument(
        "--save-markdown",
        action="store_true",
        help="Save intermediate OCR markdown to output dir",
    )
    args = p.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        sys.exit(f"File not found: {pdf_path}")

    print(f"\n{'='*60}")
    print(f"  ChandraOCR 2 + Qwen 2.5 — Local Pipeline Test")
    print(f"{'='*60}")
    print(f"  PDF: {pdf_path.name}")

    # ------------------------------------------------------------------ #
    # Step 1: Classify
    # ------------------------------------------------------------------ #
    from classify.classifier import classify_document

    t0 = time.time()
    doc_type, confidence = classify_document(str(pdf_path))
    t_classify = time.time() - t0
    print(f"\n  [1/4] Classify: {doc_type} (conf={confidence:.2f}) [{t_classify:.1f}s]")

    # ------------------------------------------------------------------ #
    # Step 2: Select schema + skill + assembler
    # ------------------------------------------------------------------ #
    from merge.assembler import get_schema, get_assembler
    from src.extractor import get_skill, extract, ExtractionResult

    schema = get_schema(doc_type)
    skill = get_skill(doc_type, skills_dir=str(SKILLS_DIR))
    assembler = get_assembler(doc_type)

    # ------------------------------------------------------------------ #
    # Step 3: OCR (ChandraOCR 2)
    # ------------------------------------------------------------------ #
    from src.chandra_mgr import ChandraManager

    chandra = ChandraManager()
    t0 = time.time()
    markdown, ocr_meta = chandra.ocr(str(pdf_path))
    t_ocr = time.time() - t0
    print(
        f"  [2/4] OCR: {ocr_meta['page_count']} page(s), "
        f"{ocr_meta['total_token_count']} tokens, "
        f"{len(markdown)} chars [{t_ocr:.1f}s]"
    )

    if args.save_markdown:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        md_file = OUTPUT_DIR / f"{pdf_path.stem}.md"
        md_file.write_text(markdown, encoding="utf-8")
        print(f"        Markdown saved: {md_file}")

    # ------------------------------------------------------------------ #
    # Step 4: Extraction (Qwen 2.5)
    # ------------------------------------------------------------------ #
    from src.qwen_mgr import QwenManager

    qwen = QwenManager()
    t0 = time.time()
    extraction_dict = extract(qwen, markdown, schema, skill)
    t_extract = time.time() - t0
    print(f"  [3/4] Extract: {len(extraction_dict)} top-level keys [{t_extract:.1f}s]")

    # ------------------------------------------------------------------ #
    # Step 5: Assemble
    # ------------------------------------------------------------------ #
    result = ExtractionResult(
        extraction=extraction_dict,
        metadata={"page_count": ocr_meta.get("page_count")},
    )
    t0 = time.time()
    assembled = assembler.build(str(pdf_path), result)
    t_assemble = time.time() - t0

    if isinstance(assembled, dict) and "result" in assembled:
        assembled["result"].setdefault("metadata", {})
        assembled["result"]["metadata"]["doc_type"] = doc_type
        assembled["result"]["metadata"]["classification_confidence"] = confidence
        assembled["result"]["metadata"]["pipeline"] = "chandra-qwen2.5"
        assembled["result"]["metadata"]["ocr_token_count"] = ocr_meta.get(
            "total_token_count"
        )
    assembled["doc_type"] = doc_type

    print(f"  [4/4] Assemble [{t_assemble:.1f}s]")

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #
    total_time = t_classify + t_ocr + t_extract + t_assemble
    extraction = assembled.get("result", assembled)

    print(f"\n  {'─'*56}")
    print(f"  Total time: {total_time:.1f}s")
    print(f"  Type: {doc_type}")

    if isinstance(extraction, dict):
        items = len(extraction.get("line_items", []))
        print(f"  Line items: {items}")

        if doc_type == "credit_note":
            ref = extraction.get("credit_note_number")
            val = extraction.get("credit_note_value")
        elif doc_type == "purchase_order":
            ref = extraction.get("purchaseorder_number")
            val = extraction.get("totals", {}).get("grand_total")
        else:
            ref = extraction.get("invoice_number")
            val = extraction.get("invoice_value")

        print(f"  Reference: {ref}")
        print(f"  Value: {val}")

    # Save JSON
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{pdf_path.stem}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(assembled, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Saved: {out_file}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
