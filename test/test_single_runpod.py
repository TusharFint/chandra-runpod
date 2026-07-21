"""Interactive single-PDF test client for the ChandraOCR 2 RunPod endpoint.

Sends a PDF, receives markdown output, saves as <pdf_name>.md.

Usage:
    python test_single_runpod.py
    python test_single_runpod.py --pdf path/to/invoice.pdf
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
POLL_INTERVAL = 5
POLL_TIMEOUT = 1500


def submit_job(base_url, headers, payload):
    resp = requests.post(
        f"{base_url}/run", headers=headers, json={"input": payload}, timeout=120
    )
    resp.raise_for_status()
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"No job id returned: {data}")
    return data["id"]


def poll_job(base_url, headers, job_id, interval=POLL_INTERVAL, timeout=POLL_TIMEOUT):
    url = f"{base_url}/status/{job_id}"
    deadline = time.time() + timeout
    print(f"  Polling job {job_id}...", end="", flush=True)
    while time.time() < deadline:
        s = requests.get(url, headers=headers, timeout=60).json()
        status = s.get("status")
        if status in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            print(f" {status}")
            return s
        print(".", end="", flush=True)
        time.sleep(interval)
    print(" TIMEOUT")
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


def encode_pdf(pdf_path):
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return None, 0, f"PDF not found: {pdf_path}"

    raw_bytes = pdf_path.read_bytes()
    b64 = base64.b64encode(raw_bytes).decode()
    size_mb = len(raw_bytes) / (1024 * 1024)

    if len(b64) > 9_500_000:
        return None, size_mb, f"PDF too large (~{len(b64)/(1024*1024):.1f}MB b64)."

    return {"pdf_base64": b64}, size_mb, None


def process_one_pdf(base_url, headers, pdf_path_str):
    pdf_path = Path(pdf_path_str)
    if not pdf_path.exists():
        print(f"  File not found: {pdf_path}")
        return

    print(f"\n{'='*60}")
    print(f"  Processing: {pdf_path.name}")
    print(f"{'='*60}")

    payload, size_mb, error = encode_pdf(pdf_path)
    if error:
        print(f"  Error: {error}")
        return

    print(f"  Submitting to RunPod ({size_mb:.2f} MB)...")
    t0 = time.time()
    try:
        job_id = submit_job(base_url, headers, payload)
        result = poll_job(base_url, headers, job_id)
        elapsed = time.time() - t0
    except Exception as e:
        print(f"  Request failed: {e}")
        return

    status = result.get("status")
    output = result.get("output", {})

    if status == "COMPLETED":
        markdown = output.get("markdown", "")
        page_count = output.get("page_count", 0)
        pipeline = output.get("pipeline", "unknown")

        print(f"\n  Success! {elapsed:.1f}s | {page_count} pages | {len(markdown)} chars")
        print(f"  Pipeline: {pipeline}")
        preview = markdown[:200].replace("\n", " ")
        print(f"  Preview: {preview}...")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_file = OUTPUT_DIR / f"{pdf_path.stem}.md"
        out_file.write_text(markdown, encoding="utf-8")
        print(f"\n  Saved to: {out_file}")
    else:
        print(f"\n  Job failed: {status}")
        print(f"  Details: {json.dumps(output, indent=2)}")


def main():
    p = argparse.ArgumentParser(
        description="Test single PDFs on ChandraOCR 2 RunPod endpoint."
    )
    p.add_argument("--pdf", help="Path to the PDF file (skips first prompt).")
    p.add_argument(
        "--endpoint-id",
        default=os.environ.get("CHANDRA_ENDPOINT_ID"),
        help="RunPod serverless endpoint ID.",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("RUNPOD_API_KEY"),
        help="RunPod API key.",
    )
    args = p.parse_args()

    if not args.endpoint_id or not args.api_key:
        sys.exit(
            "Set CHANDRA_ENDPOINT_ID and RUNPOD_API_KEY env vars, "
            "or pass --endpoint-id / --api-key."
        )

    base_url = f"https://api.runpod.ai/v2/{args.endpoint_id}"
    headers = {"Authorization": f"Bearer {args.api_key}"}

    print("\n" + "=" * 60)
    print("  ChandraOCR 2 — Markdown OCR")
    print("=" * 60)

    next_pdf = args.pdf

    while True:
        if next_pdf:
            pdf_path_str = next_pdf
            next_pdf = None
        else:
            print()
            pdf_path_str = input("Enter PDF path (or 'q' to quit): ").strip()
            if not pdf_path_str or pdf_path_str.lower() in ("q", "quit", "exit"):
                break

        process_one_pdf(base_url, headers, pdf_path_str)

        print()
        answer = input("Process another PDF? (y/n): ").strip().lower()
        if answer not in ("y", "yes"):
            break

    print("\nDone. Goodbye!")


if __name__ == "__main__":
    main()
