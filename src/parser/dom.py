"""ChandraDoc — wraps ChandraOCR 2 markdown for combined access.

ChandraOCR's ``ocr_layout`` prompt asks for labeled ``<div data-bbox
data-label>`` blocks, but the actual saved output for the calibration
fixtures is a mix of:

- Plain markdown (``##``, ``###``, ``**bold**``, ``[text](url)``)
- Raw HTML ``<table>`` blocks (with ``rowspan`` for tax sub-rows)
- Trailing two-space markdown hard breaks inside paragraphs
- Page separator: ``\\n\\n---\\n\\n`` (see ``chandra_mgr.py:90``)

This class exposes both views so the rest of the parser can use the right
tool for each job: regex on ``self.text`` for key/value fields, BeautifulSoup
on ``self.tables`` for tabular data.
"""

import re
from bs4 import BeautifulSoup

PAGE_SEPARATOR = re.compile(r"\n\n---\n\n")
_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)


class ChandraDoc:
    """Parsed view over a ChandraOCR 2 markdown document."""

    def __init__(self, markdown: str):
        self.markdown = markdown or ""
        self.pages = PAGE_SEPARATOR.split(self.markdown)
        self._soup = BeautifulSoup(self.markdown, "lxml")
        self.tables = self._soup.find_all("table")
        self.text = self._strip_tables(self.markdown)

    @staticmethod
    def _strip_tables(markdown: str) -> str:
        """Return markdown with all ``<table>...</table>`` blocks removed.

        This is what we run regex against for header / party field
        extraction. Tables are accessed separately via ``self.tables``.
        """
        return _TABLE_RE.sub("", markdown)

    # ------------------------------------------------------------------ #
    # Convenience accessors
    # ------------------------------------------------------------------ #

    def find_tables_by_header(self, *required_headers) -> list:
        """Return tables whose ``<th>`` cells contain all *required_headers*.

        Header matching is case-insensitive and substring-tolerant.
        """
        wanted = [h.lower() for h in required_headers]
        out = []
        for t in self.tables:
            headers = [c.get_text(strip=True).lower() for c in t.find_all("th")]
            if not headers:
                continue
            if all(any(w in h for h in headers) for w in wanted):
                out.append(t)
        return out

    def first_page_text(self) -> str:
        """Return text (tables stripped) of page 1 only — used for classification."""
        return self._strip_tables(self.pages[0]) if self.pages else ""

    def full_text(self) -> str:
        """Return text (tables stripped) for the whole document."""
        return self.text
