"""Extraction logic: build prompts, call Qwen, parse JSON.

Bridges Stage 1 (ChandraOCR markdown) and Stage 2 (Qwen 2.5 extraction)
and produces a dict ready for the existing assembler.
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Skill-file resolution
# --------------------------------------------------------------------------- #

_SKILL_FILE_MAP = {
    "invoice": "invoice.md",
    "credit_note": "credit_note.md",
    "purchase_order": "purchase_order.md",
}


def get_skill(doc_type, skills_dir=None):
    """Read the skill ``.md`` file for *doc_type*.

    Falls back to ``invoice.md`` for unknown types.
    """
    if skills_dir is None:
        here = Path(__file__).resolve().parent.parent
        skills_dir = here / "skills"
    else:
        skills_dir = Path(skills_dir)

    filename = _SKILL_FILE_MAP.get(doc_type, _SKILL_FILE_MAP["invoice"])
    skill_path = skills_dir / filename

    with open(skill_path, "r", encoding="utf-8") as f:
        return f.read().strip()


# --------------------------------------------------------------------------- #
# Result wrapper (matches assembler's expected interface)
# --------------------------------------------------------------------------- #


class ExtractionResult:
    """Mimics the lift result object expected by assembler.build()."""

    def __init__(self, extraction, metadata=None):
        self.extraction = extraction
        self.metadata = metadata or {}


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #


def build_system_prompt(skill_content, schema):
    """Build the system prompt that guides Qwen's extraction.

    Combines domain rules (from skill .md) with the JSON schema so the
    model knows exactly what structure to produce.
    """
    schema_str = json.dumps(schema, indent=2, ensure_ascii=False)
    return f"""You are an expert document data extraction system specializing in Indian GST documents.

Your task is to extract structured data from the provided OCR markdown and return ONLY a valid JSON object.

EXTRACTION RULES:
{skill_content}

OUTPUT JSON SCHEMA (match this structure exactly):
{schema_str}

CRITICAL INSTRUCTIONS:
- Output ONLY the JSON object. No explanation, no reasoning, no markdown.
- Use null for any field that is missing, unreadable, or contains placeholder text.
- All numeric fields must be numbers (int/float), not strings.
- Dates must be in DD-MM-YYYY format."""


def build_user_prompt(markdown, max_chars=50000):
    """Build the user prompt containing the OCR markdown.

    Truncates extremely long markdown to stay within Qwen's context window.
    """
    if len(markdown) > max_chars:
        logger.warning(
            f"Markdown truncated: {len(markdown)} -> {max_chars} chars"
        )
        markdown = markdown[:max_chars] + "\n\n[... TRUNCATED ...]"

    return f"Extract all structured data from the following document:\n\n{markdown}"


# --------------------------------------------------------------------------- #
# JSON parsing
# --------------------------------------------------------------------------- #


def parse_json_response(response):
    """Extract a JSON dict from Qwen's raw text output.

    Tries multiple strategies:
    1. Strip <think> blocks, then json.loads
    2. Extract from ```json ... ``` code fences
    3. Regex outermost { ... }
    4. Last-resort: find first { to last }

    Returns dict or None.
    """
    if not response or not response.strip():
        return None

    # Strip reasoning blocks (Qwen3.x style)
    cleaned = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()

    # Strategy 1: direct parse
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 2: code fence ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: balanced brace extraction (first { to matching last })
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.error(f"Failed to parse JSON. First 200 chars: {cleaned[:200]}")
    return None


# --------------------------------------------------------------------------- #
# Full extraction pipeline
# --------------------------------------------------------------------------- #


def extract(qwen_manager, markdown, schema, skill_content):
    """Run the full extraction with one retry on parse failure.

    Parameters
    ----------
    qwen_manager : QwenManager
        Already-loaded Qwen 2.5 model.
    markdown : str
        Full OCR markdown from ChandraOCR 2.
    schema : dict
        JSON schema for the document type.
    skill_content : str
        Domain extraction rules from skills/*.md.

    Returns
    -------
    dict  – extracted data ready for the assembler.

    Raises
    ------
    ValueError  – if JSON cannot be parsed after 2 attempts.
    """
    system_prompt = build_system_prompt(skill_content, schema)
    user_prompt = build_user_prompt(markdown)

    for attempt in range(2):
        logger.info(f"Qwen extraction attempt {attempt + 1}/2")
        response = qwen_manager.generate(system_prompt, user_prompt)

        result = parse_json_response(response)
        if result is not None:
            logger.info("JSON parsed successfully.")
            return result

        if attempt == 0:
            user_prompt += (
                "\n\nIMPORTANT: Your previous response was not valid JSON. "
                "Output ONLY the raw JSON object with no other text, "
                "no code fences, no explanation."
            )
            logger.warning("First parse failed, retrying with stricter prompt.")

    raise ValueError(
        "Failed to parse JSON from Qwen output after 2 attempts"
    )
