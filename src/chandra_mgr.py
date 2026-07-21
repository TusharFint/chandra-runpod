"""ChandraOCR 2 model manager.

Wraps `chandra.model.InferenceManager` (HF backend) to convert PDF pages
into layout-aware Markdown/HTML.  Loads the 5B model in BF16 (~10 GB VRAM).
"""

import logging
import os

logger = logging.getLogger(__name__)

_MAX_OUTPUT_TOKENS = int(os.environ.get("CHANDRA_MAX_TOKENS", "12384"))


class ChandraManager:
    """Lazy-loading singleton wrapper around chandra InferenceManager."""

    def __init__(self):
        self._model = None

    @property
    def model(self):
        """Lazily instantiate InferenceManager(method='hf')."""
        if self._model is None:
            from chandra.model import InferenceManager

            logger.info("Loading ChandraOCR 2 (HF backend, BF16)...")
            self._model = InferenceManager(method="hf")
            logger.info("ChandraOCR 2 loaded successfully.")
        return self._model

    def ocr(self, pdf_path, page_range=None, max_output_tokens=None):
        """Run OCR on every page of *pdf_path*.

        Parameters
        ----------
        pdf_path : str
            Path to a PDF file on disk.
        page_range : str | None
            e.g. "1-3,5" — passed straight to chandra's ``load_file``.
        max_output_tokens : int | None
            Per-page generation cap.  Defaults to ``_MAX_OUTPUT_TOKENS``.

        Returns
        -------
        (markdown, metadata)
            ``markdown``  – all pages concatenated with ``\\n\\n---\\n\\n``
            ``metadata``  – dict with ``page_count``, ``total_token_count``,
                            ``pages`` list.
        """
        from chandra.input import load_file
        from chandra.model.schema import BatchInputItem

        if max_output_tokens is None:
            max_output_tokens = _MAX_OUTPUT_TOKENS

        config = {"page_range": page_range} if page_range else {}
        images = load_file(pdf_path, config=config)
        logger.info(f"Loaded {len(images)} page(s) from {pdf_path}")

        results = []
        for img in images:
            batch = [BatchInputItem(image=img, prompt_type="ocr_layout")]
            page_results = self.model.generate(
                batch,
                max_output_tokens=max_output_tokens,
                include_images=False,
                include_headers_footers=False,
            )
            results.extend(page_results)
            logger.info(f"  OCR page {len(results)}/{len(images)} done")

        pages_md = []
        pages_meta = []
        total_tokens = 0

        for i, r in enumerate(results):
            if r.error:
                logger.warning(f"Page {i} OCR returned error flag")
            pages_md.append(r.markdown or "")
            total_tokens += r.token_count or 0
            pages_meta.append(
                {
                    "page_num": i,
                    "token_count": r.token_count or 0,
                    "error": bool(r.error),
                }
            )

        full_markdown = "\n\n---\n\n".join(pages_md)
        metadata = {
            "page_count": len(results),
            "total_token_count": total_tokens,
            "pages": pages_meta,
        }
        logger.info(
            f"OCR complete: {len(results)} pages, {total_tokens} tokens, "
            f"{len(full_markdown)} chars markdown"
        )
        return full_markdown, metadata
