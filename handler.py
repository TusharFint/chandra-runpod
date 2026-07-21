"""RunPod Serverless Handler — ChandraOCR 2 markdown + deterministic JSON.

Default behaviour (markdown-first-then-JSON):
    1. Run ChandraOCR 2 to produce per-page HTML/markdown.
    2. Run the deterministic parser (``src.parser``) on the markdown to
       produce a structured extraction dict.
    3. Feed that dict through the existing ``shared.merge.assembler`` to
       produce the final schema-shaped JSON.

Input (event['input']):
    pdf_base64 : str           — base64-encoded PDF            (required*)
    pdf_url    : str           — URL to download PDF           (required*)
    extract    : bool          — default True. Set False to skip
                                 Stage 2 and return markdown only.
    doc_type   : str | None    — optional override ('invoice' |
                                 'credit_note' | 'purchase_order')

Output (extract=True):
    {
        "markdown":   "<all pages joined>",
        "page_count": N,
        "pages":      [...],
        "pipeline":   "chandra-parser",
        "doc_type":   "credit_note",
        "result":     { ...assembled JSON... },
        "parser_meta": { ...per-field source flags... }
    }
"""

import base64
import logging
import os
import sys
import tempfile

import runpod

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Lazy model singleton
# --------------------------------------------------------------------------- #

_chandra_mgr = None


def get_chandra_manager():
    global _chandra_mgr
    if _chandra_mgr is None:
        from src.chandra_mgr import ChandraManager

        _chandra_mgr = ChandraManager()
        logger.info("Initialized ChandraManager")
    return _chandra_mgr


# --------------------------------------------------------------------------- #
# Stage 2: deterministic markdown -> assembled JSON
# --------------------------------------------------------------------------- #


def _run_stage2(markdown, page_count, doc_type_override=None):
    """Parse markdown and run it through the doc-type assembler.

    Returns ``(result_body, doc_type, parser_meta)`` or ``(None, None, None)``
    on failure. Errors are logged but never raised so the handler always
    returns the markdown even if Stage 2 fails.
    """
    try:
        from src.parser import parse
        from merge.assembler import get_assembler
    except ImportError as e:
        logger.exception("Stage 2 imports failed: %s", e)
        return None, None, None

    try:
        result = parse(markdown, doc_type=doc_type_override)
    except Exception:
        logger.exception("Parser failed; returning markdown only")
        return None, None, None

    doc_type = result.doc_type

    try:
        assembler = get_assembler(doc_type)

        class _LiftLike:
            def __init__(self, extraction, metadata):
                self.extraction = extraction
                self.metadata = metadata

        lift = _LiftLike(
            extraction=result.extraction,
            metadata={"page_count": page_count},
        )
        # PDF path is only used as a filename hint; pass an empty string so
        # the assembler falls back to metadata.page_count.
        assembled = assembler.build("", lift)
        body = assembled.get("result", assembled) if isinstance(assembled, dict) else {}
        body.setdefault("metadata", {})
        body["metadata"]["pipeline"] = "chandra-parser"
        body["metadata"]["doc_type"] = doc_type
        body["metadata"]["parser_meta"] = result.parser_meta
        return body, doc_type, result.parser_meta
    except Exception:
        logger.exception("Assembler failed; returning markdown only")
        return None, doc_type, result.parser_meta


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #


def handler(event):
    input_data = event.get("input", {})
    pdf_base64 = input_data.get("pdf_base64")
    pdf_url = input_data.get("pdf_url")
    extract = input_data.get("extract", True)
    doc_type_override = input_data.get("doc_type")

    if not pdf_base64 and not pdf_url:
        return {"error": "Missing pdf_base64 or pdf_url in input"}

    # --- Decode / download PDF ----------------------------------------- #
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        if pdf_base64:
            tmp.write(base64.b64decode(pdf_base64))
        else:
            import urllib.request

            urllib.request.urlretrieve(pdf_url, tmp.name)
        pdf_path = tmp.name

    try:
        # --- OCR (ChandraOCR 2, page-by-page) --------------------------- #
        chandra = get_chandra_manager()
        markdown, ocr_meta = chandra.ocr(pdf_path)

        # Unload model to free VRAM between requests
        _chandra_mgr._model = None
        import torch
        torch.cuda.empty_cache()
        logger.info("Unloaded ChandraOCR, freed VRAM")

        # --- Build response: markdown-first ----------------------------- #
        response = {
            "markdown": markdown,
            "page_count": ocr_meta.get("page_count"),
            "pages": ocr_meta.get("pages"),
            "pipeline": "chandra-ocr-2",
        }

        # --- Stage 2: deterministic JSON -------------------------------- #
        if extract:
            body, doc_type, parser_meta = _run_stage2(
                markdown,
                page_count=ocr_meta.get("page_count"),
                doc_type_override=doc_type_override,
            )
            if body is not None:
                response["pipeline"] = "chandra-parser"
                response["doc_type"] = doc_type
                response["result"] = body
                if parser_meta is not None:
                    response["parser_meta"] = parser_meta
            else:
                response["pipeline"] = "chandra-ocr-2"
                response["stage2_error"] = (
                    "Parser or assembler failed; see server logs."
                )

        return response

    except Exception as e:
        logger.exception("Error during OCR")
        return {"error": str(e)}
    finally:
        if os.path.exists(pdf_path):
            os.unlink(pdf_path)


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
