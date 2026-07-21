"""Ad-hoc runner: parse one fixture, pretty-print the result.

Usage:
    python -m test.test_parser.run_fixture KA-C-27-124630
    python test/test_parser/run_fixture.py KA-C-27-124630
"""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

from src.parser import parse  # noqa: E402


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: run_fixture.py <fixture-stem>")

    stem = sys.argv[1]
    md_path = _REPO_ROOT / "test" / "output" / f"{stem}.md"
    if not md_path.exists():
        sys.exit(f"Fixture not found: {md_path}")

    markdown = md_path.read_text(encoding="utf-8")
    result = parse(markdown)

    out = {
        "doc_type": result.doc_type,
        "parser_meta": result.parser_meta,
        "extraction": result.extraction,
    }

    # Write a sibling .last_output.json next to the fixture for easy diffing.
    out_path = md_path.with_suffix(".last_output.json")
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"doc_type={result.doc_type}, line_items={len(result.extraction.get('line_items', []))}")


if __name__ == "__main__":
    main()
