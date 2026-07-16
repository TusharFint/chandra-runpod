"""RunPod Serverless Handler — ChandraOCR 2 + Qwen 2.5 pipeline.

Input (event['input']):
    pdf_base64 : str  — base64-encoded PDF
    pdf_url    : str  — URL to download PDF

Output:
    Assembled dict matching the same schema as the lift endpoint,
    with additional metadata: pipeline="chandra-qwen2.5".
"""

import base64
import logging
import os
import sys
import tempfile

import runpod

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Make /app/shared importable (classifier + assembler live there)
_SHARED_DIR = os.path.join(os.path.dirname(__file__), "shared")
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

# --------------------------------------------------------------------------- #
# Lazy model singletons
# --------------------------------------------------------------------------- #

_chandra_mgr = None
_qwen_mgr = None


def get_chandra_manager():
    global _chandra_mgr
    if _chandra_mgr is None:
        from src.chandra_mgr import ChandraManager

        _chandra_mgr = ChandraManager()
        logger.info("Initialized ChandraManager")
    return _chandra_mgr


def get_qwen_manager():
    global _qwen_mgr
    if _qwen_mgr is None:
        from src.qwen_mgr import QwenManager

        _qwen_mgr = QwenManager()
        logger.info("Initialized QwenManager")
    return _qwen_mgr


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #


def handler(event):
    input_data = event.get("input", {})
    pdf_base64 = input_data.get("pdf_base64")
    pdf_url = input_data.get("pdf_url")

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
        # --- Step 1: Classify ------------------------------------------ #
        from classify.classifier import classify_document

        doc_type, confidence = classify_document(pdf_path)
        logger.info(f"Classified as '{doc_type}' (confidence={confidence})")

        # --- Step 2: Select schema + skill + assembler ----------------- #
        from merge.assembler import get_schema, get_assembler
        from src.extractor import get_skill, extract, ExtractionResult

        schema = get_schema(doc_type)
        skill = get_skill(doc_type)
        assembler = get_assembler(doc_type)

        # --- Step 3: Stage 1 — OCR (ChandraOCR 2) --------------------- #
        chandra = get_chandra_manager()
        markdown, ocr_meta = chandra.ocr(pdf_path)

        # --- Step 4: Stage 2 — Extraction (Qwen 2.5) ------------------ #
        qwen = get_qwen_manager()
        extraction_dict = extract(qwen, markdown, schema, skill)

        # --- Step 5: Assemble ------------------------------------------ #
        result = ExtractionResult(
            extraction=extraction_dict,
            metadata={"page_count": ocr_meta.get("page_count")},
        )
        assembled = assembler.build(pdf_path, result)

        # --- Step 6: Inject metadata ---------------------------------- #
        if isinstance(assembled, dict) and "result" in assembled:
            assembled["result"].setdefault("metadata", {})
            assembled["result"]["metadata"]["doc_type"] = doc_type
            assembled["result"]["metadata"][
                "classification_confidence"
            ] = confidence
            assembled["result"]["metadata"]["pipeline"] = "chandra-qwen2.5"
            assembled["result"]["metadata"][
                "ocr_token_count"
            ] = ocr_meta.get("total_token_count")
        assembled["doc_type"] = doc_type

        return assembled

    except Exception as e:
        logger.exception("Error during extraction")
        return {"error": str(e)}
    finally:
        if os.path.exists(pdf_path):
            os.unlink(pdf_path)


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
