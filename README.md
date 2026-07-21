# ChandraOCR 2 — Single-Model JSON Extraction

A RunPod Serverless endpoint that converts PDF documents to structured JSON using **only ChandraOCR 2** — no LLM required for extraction.

## How It Works

The pipeline extracts JSON directly from ChandraOCR 2's layout-aware HTML/markdown output through deterministic parsing:

```
PDF Input
  └─► ChandraOCR 2 (prompt_type='ocr_layout')
        HTML/markdown with semantic structure
          └─► Deterministic Parser (regex + BeautifulSoup)
                └─► Structured JSON
```

### Key Insight

ChandraOCR 2 produces **layout-aware output** with:
- `<div data-bbox data-label>` blocks for spatial layout
- `<table>` elements with rowspan for complex tax structures
- Markdown formatting for text content

This structured output eliminates the need for an LLM — we can extract fields directly using regex patterns and HTML parsing.

## Pipeline Components

### 1. ChandraOCR 2 (`src/chandra_mgr.py`)
- **Model**: `datalab-to/chandra-ocr-2` (5B parameters)
- **VRAM**: ~10GB
- **Output**: Layout-aware HTML/markdown per page
- **Processing**: Page-by-page with sequential GPU execution

### 2. Deterministic Parser (`src/parser/`)
- **Classification**: Regex-based document type detection
- **Party Extraction**: Seller/buyer info via text block analysis
- **Metadata Extraction**: Invoice numbers, dates, totals from tables
- **Line Item Extraction**: Two table layouts (rowspan-aware)

### 3. Assembler (`shared/merge/assembler.py`)
- Maps extraction dict to final JSON schema
- Handles document-type-specific formatting

## Usage

### RunPod Endpoint
```python
import requests

payload = {
    "pdf_base64": "<base64-encoded-pdf>",
    "extract": True,  # Set False for markdown only
    "doc_type": None   # Optional override
}

response = requests.post(ENDPOINT_URL, json={"input": payload})
result = response.json()
```

### Response Format
```json
{
  "markdown": "<full HTML/markdown>",
  "page_count": 5,
  "pipeline": "chandra-parser",
  "doc_type": "credit_note",
  "result": { "...assembled JSON..." },
  "parser_meta": { "...field sources..." }
}
```

## Supported Document Types

- **Invoice**: Standard GST e-invoice format
- **Credit Note**: Amazon Seller Services style with rowspan tax sub-rows
- **Purchase Order**: Multi-page table splits supported

## Local Development

### Test Parser Only (No GPU)
```bash
python test/test_parser/test_kas_credit_note.py
```

### Test Parser + Assembler
```bash
python test/test_parser/e2e_assemble.py KA-C-27-124630
```

### Docker Build
```bash
docker build -t chandra-runpod .
```

## Architecture Benefits

1. **No LLM Overhead**: Eliminates Qwen 2.5 inference time and VRAM usage
2. **Deterministic Output**: Same input always produces same extraction
3. **Faster Processing**: Only one model to load and run
4. **Simpler Deployment**: Single GPU requirement (~10GB VRAM)

## Documentation

See `EXTRACTION.md` for detailed extraction pipeline documentation and fixture calibration process.