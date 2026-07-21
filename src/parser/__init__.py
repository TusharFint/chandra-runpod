"""Parser package — deterministic ChandraOCR 2 markdown → structured dict.

Public entry point: :func:`parse` (re-exported at package level).

Returns a dict shaped for ``shared.merge.assembler`` — same shape the old
Qwen Stage 2 used to produce — so the existing assemblers work unchanged.
"""

from .pipeline import parse, ParseResult

__all__ = ["parse", "ParseResult"]
