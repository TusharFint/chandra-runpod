# ChandraOCR 2 + Deterministic Parser

RunPod Serverless endpoint that turns a PDF into structured JSON using a
**single model** (ChandraOCR 2) — no Stage-2 LLM is loaded.

## Pipeline

```
PDF
  └─► ChandraOCR 2  (src/chandra_mgr.py, prompt_type='ocr_layout')
        HTML/markdown per page, joined by "\n\n---\n\n"
          └─► src/parser/   (deterministic, BeautifulSoup + regex)
                ├─ dom.py        ChandraDoc: text + table views
                ├─ classify.py   doc_type via regex (credit_note/invoice/PO)
                ├─ parties.py    seller/buyer from text blocks
                ├─ metadata.py   header fields + totals from tables
                ├─ tables.py     line items (rowspan-aware)
                └─ pipeline.py   orchestrator -> extraction dict
                      └─► shared/merge/assembler.py  (unchanged)
                            └─► final JSON per doc-type schema
```

## Handler input

```json
{
  "pdf_base64": "<base64 PDF>",
  "extract": true,
  "doc_type": null
}
```

| Key | Default | Description |
|---|---|---|
| `pdf_base64` *or* `pdf_url` | — | PDF payload |
| `extract` | `true` | Set `false` to return markdown only |
| `doc_type` | classifier | Override: `invoice` / `credit_note` / `purchase_order` |

## Handler output (extract=true)

```json
{
  "markdown":    "<full HTML/markdown>",
  "page_count":  5,
  "pages":       [...],
  "pipeline":    "chandra-parser",
  "doc_type":    "credit_note",
  "result":      { ...assembled JSON matching schemas/<doc_type>.json... },
  "parser_meta": { ...per-field source flags, confidence... }
}
```

If Stage 2 fails for any reason, the handler returns the markdown-only
payload (pipeline `chandra-ocr-2`) with a `stage2_error` key so the caller
can fall back.

## Calibrated fixtures

The parser is **fixture-calibrated**, one document at a time. Each fixture
has an `*.expected.json` golden file; the parser must reproduce it exactly.

| Status | Fixture | Doc type | Notes |
|---|---|---|---|
| ✓ | `test/output/KA-C-27-124630.md` | `credit_note` | Amazon Seller Services; 5 pages, rowspan tax sub-rows, multi-page table splits |

To run regression:
```
python test/test_parser/test_kas_credit_note.py
```

To re-run the parser on a fixture and write a fresh `*.last_output.json`:
```
python test/test_parser/run_fixture.py KA-C-27-124630
```

To exercise parser + assembler end-to-end (no RunPod / no GPU):
```
python test/test_parser/e2e_assemble.py KA-C-27-124630
```

## Adding a new fixture

1. Place the chandra markdown at `test/output/<stem>.md`.
2. Run `python test/test_parser/run_fixture.py <stem>` and inspect the
   `*.last_output.json`.
3. Review the extraction rules; if a heuristic needs to change, edit the
   relevant module (`parties.py` / `metadata.py` / `tables.py`) and
   re-run.
4. Once correct, copy `*.last_output.json` -> `*.expected.json` and add a
   regression test mirroring `test_kas_credit_note.py`.
5. Re-run all previous fixtures' regression tests to confirm no regressions.

## Repository layout

```
chandra-deploy/
├── handler.py             RunPod entry point
├── requirements.txt
├── Dockerfile
├── schemas/               JSON schemas (input to assembler)
├── shared/
│   ├── classify/          PDF-based doc-type classifier (legacy, used by test_local.py)
│   └── merge/             assembler.py
├── skills/                Stage-2 Qwen prompts (DORMANT — kept for fallback)
├── src/
│   ├── chandra_mgr.py     Stage 1: ChandraOCR 2 wrapper
│   ├── extractor.py       Stage 2 LLM extractor (DORMANT — kept for fallback)
│   ├── qwen_mgr.py        Qwen 2.5 Coder loader  (DORMANT — kept for fallback)
│   └── parser/            Stage 2 deterministic parser (active)
└── test/
    ├── output/            Chandra markdown fixtures + golden expected JSON
    └── test_parser/       Parser regression + e2e tests
```
