"""Qwen 2.5 Coder 7B model manager (INT4 quantised).

Loads ``Qwen/Qwen2.5-Coder-7B-Instruct`` in 4-bit NF4 via bitsandbytes,
consuming ~5 GB VRAM.  Used for structured-JSON extraction from the
Markdown produced by ChandraOCR 2.
"""

import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"
_DEFAULT_MAX_TOKENS = 4096


class QwenManager:
    """Lazy-loading singleton wrapper around the Qwen 2.5 Coder model."""

    def __init__(self):
        self.model_id = os.environ.get("EXTRACTION_MODEL", _DEFAULT_MODEL)
        self._model = None
        self._tokenizer = None

    # ------------------------------------------------------------------
    # Lazy properties
    # ------------------------------------------------------------------

    @property
    def model(self):
        if self._model is None:
            self._load()
        return self._model

    @property
    def tokenizer(self):
        if self._tokenizer is None:
            self._load()
        return self._tokenizer

    def _load(self):
        """Load model + tokenizer in INT4."""
        import torch
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
        )

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )

        logger.info(f"Loading {self.model_id} (INT4 / NF4)...")
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)

        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            quantization_config=bnb_config,
            device_map="auto",
        ).eval()
        logger.info(f"{self.model_id} loaded successfully.")

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def generate(
        self,
        system_prompt,
        user_prompt,
        max_new_tokens=None,
    ):
        """Generate a completion from a system + user prompt pair.

        Parameters
        ----------
        system_prompt : str
            Extraction rules / schema instructions.
        user_prompt : str
            The Markdown content to extract from.
        max_new_tokens : int | None
            Generation cap.  Defaults to ``_DEFAULT_MAX_TOKENS``.

        Returns
        -------
        str  – raw model output (may contain JSON, code fences, etc.)
        """
        import torch

        if max_new_tokens is None:
            max_new_tokens = _DEFAULT_MAX_TOKENS

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self.tokenizer(text, return_tensors="pt").to(
            self.model.device
        )

        with torch.no_grad():
            output_ids = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        response = self.tokenizer.decode(
            output_ids[0][inputs["input_ids"].shape[1]:],
            skip_special_tokens=True,
        )
        logger.info(
            f"Qwen generated {len(response)} chars "
            f"({output_ids.shape[1] - inputs['input_ids'].shape[1]} tokens)"
        )
        return response
