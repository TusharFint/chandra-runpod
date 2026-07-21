# ChandraOCR 2 markdown-only pipeline.
# Self-contained repo — build context is this directory.
#
#   docker build -t ghcr.io/tusharfint/chandra-runpod:v4 .

FROM python:3.12-slim

# System libraries for image / OCR dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libsm6 libxext6 libxrender-dev libgomp1 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build-time guard: fail fast if Python is not 3.12+
RUN python -c "import sys; assert sys.version_info >= (3, 12), sys.version"

# ------------------------------------------------------------------ #
# Python dependencies
# ------------------------------------------------------------------ #
COPY requirements.txt ./
RUN pip install --no-cache-dir --retries 5 --timeout 120 \
        --extra-index-url https://download.pytorch.org/whl/cu126 \
        -r requirements.txt \
    && pip install --no-cache-dir --retries 5 --timeout 120 \
        --force-reinstall \
        --index-url https://download.pytorch.org/whl/cu126 \
        torch torchvision

# CUDA build guard
RUN python -c "import torch; v=torch.__version__; print(f'torch {v}'); \
    assert '+cu' in v, f'FATAL: torch is not CUDA build: {v}'"

# C compiler for Triton JIT (chandra uses Triton at inference time)
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------ #
# Application code
# ------------------------------------------------------------------ #

# Chandra pipeline code
COPY src ./src
COPY schemas ./schemas
COPY shared ./shared
COPY handler.py ./

# ------------------------------------------------------------------ #
# Runtime configuration
# ------------------------------------------------------------------ #
ENV TORCH_DEVICE=cuda:0
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
ENV CC=/usr/bin/gcc
ENV PYTHONPATH=/app
ENV HF_HOME=/runpod-volume/hf_cache
ENV MODEL_CHECKPOINT=datalab-to/chandra-ocr-2
ENV CHANDRA_MAX_TOKENS=12384

RUN mkdir -p /runpod-volume/hf_cache

CMD ["python", "-u", "handler.py"]
