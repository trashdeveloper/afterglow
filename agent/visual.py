#!/usr/bin/env python3
"""Afterglow live Muse Image caller (AGENTS.md section 4, load-bearing visual).

Provenance contract:
  input  = sha256(artifacts/input_trace.txt) + verbatim diagnosis text
  output = explain.png saved into the SAME runs/<id>/ BEFORE re-sim starts.

Only live API calls. Raises on missing key or any failure -- the loop
catches, marks ``explain: FAILED`` in meta.json, and continues WITHOUT a
silent skip. No mocks, no canned images, no matplotlib fallback.

  model : muse-image-1.0
  route : <SPARK_API_URL>/v1/images/generations   (OpenAI-compatible)

SPARK_API_URL handling: the env var may hold either the API base
(https://api.meta.ai) or a full sibling endpoint
(https://api.meta.ai/v1/chat/completions). Known suffixes are stripped to
recover the base, then /v1/images/generations is appended.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import time
import urllib.request

IMAGE_MODEL = "muse-image-1.0"
DEFAULT_BASE = "https://api.meta.ai"
IMAGE_PATH = "/v1/images/generations"


class LiveImageError(RuntimeError):
    """Raised when the live image call cannot produce a diagram."""


def api_key() -> str:
    """Return the Spark key or raise (missing key fails LOUDLY)."""
    for var in ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY"):
        key = os.environ.get(var, "").strip()
        if key:
            return key
    raise LiveImageError(
        "missing image API key: set MODEL_API_KEY "
        "(SPARK_API_KEY / META_API_KEY also accepted)"
    )


def base_url() -> str:
    """Recover the API base from SPARK_API_URL (base or full endpoint)."""
    raw = os.environ.get("SPARK_API_URL", "").strip() or DEFAULT_BASE
    base = raw.rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions",
                   "/v1/images/generations", "/images/generations"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.rstrip("/") or DEFAULT_BASE


def image_url() -> str:
    return base_url() + IMAGE_PATH


def build_prompt(case: str, trace_sha: str, diagnosis: str) -> str:
    """Deterministic diagram prompt bound to THIS failure.

    Binds the failing-window hash and the verbatim diagnosis so a judge can
    verify the diagram came from this exact RED run (AGENTS.md section 4).
    Pure string function -- no API call, fully testable offline.
    """
    digest = (trace_sha or "").strip()[:16]
    diag = (diagnosis or "").strip()
    if len(diag) > 1500:
        diag = diag[:1500] + "...[truncated]"
    return (
        "You are a hardware-debug illustrator. Draw ONE technical "
        "signal-flow diagram (dark background, light labels) explaining a "
        "RISC-V RTL failure.\n"
        "Case: %s\n"
        "Failing-trace sha256 (first 16 hex): %s\n"
        "Verbatim diagnosis from the debug agent:\n%s\n"
        "Diagram MUST show: the failing signal path named in the diagnosis, "
        "the wrong value vs the expected value, and the single RTL line to "
        "fix. Annotate the trace hash %s in a corner as provenance. "
        "No decorative elements, no invented signals."
        % (case, digest, diag, digest)
    )


def _post_json(url: str, payload: dict, key: str, timeout: int) -> tuple:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            headers = dict(resp.headers.items())
            status = resp.status
    except Exception as exc:
        raise LiveImageError("live image call failed: %r" % (exc,))
    latency_ms = int((time.monotonic() - t0) * 1000)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise LiveImageError(
            "live image call returned non-JSON (status %s): %r" % (status, exc)
        )
    return parsed, headers, latency_ms, status


def _extract_image_bytes(parsed: dict, url: str, key: str,
                         timeout: int) -> tuple:
    """Return (png_source_bytes, response_summary). Handles b64_json | url."""
    data = parsed.get("data")
    if isinstance(data, list) and data:
        first = data[0] if isinstance(data[0], dict) else {}
        b64 = first.get("b64_json") or first.get("b64") or ""
        if b64:
            try:
                return base64.b64decode(b64), {"transport": "b64_json"}
            except Exception as exc:
                raise LiveImageError("image b64 decode failed: %r" % (exc,))
        img_url = first.get("url") or ""
        if img_url:
            req = urllib.request.Request(
                img_url, headers={"Authorization": "Bearer " + key})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read(), {"transport": "url"}
            except Exception as exc:
                raise LiveImageError(
                    "image url download failed: %r" % (exc,))
    # Some gateways echo raw bytes with an image content-type disguised as
    # JSON failure -- unreachable here; fail loudly instead of guessing.
    raise LiveImageError(
        "live image response carried no image (no data[0].b64_json|url): %s"
        % (json.dumps(parsed)[:400],)
    )


def generate(prompt: str, out_path: str, model: str = IMAGE_MODEL,
             timeout: int = 180) -> dict:
    """Live image call. Saves normalized PNG to out_path. Returns provenance.

    Normalizes webp/jpeg/etc to PNG via Pillow. Raises LiveImageError on
    missing key, transport failure, empty payload, or bad image bytes --
    never returns a placeholder.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise LiveImageError("Pillow is required to normalize explain.png: %r"
                             % (exc,))
    key = api_key()  # raises when missing -- loud, before any network
    url = image_url()
    payload = {"model": model, "prompt": prompt, "size": "1024x1024"}
    parsed, headers, latency_ms, _status = _post_json(
        url, payload, key, timeout)
    request_id = str(
        parsed.get("id") or parsed.get("request_id") or headers.get(
            "x-request-id", "")
    )
    if not isinstance(parsed, dict) or not parsed.get("data"):
        raise LiveImageError(
            "live image call returned empty data: %s"
            % (json.dumps(parsed)[:400],)
        )
    src_bytes, transport = _extract_image_bytes(parsed, url, key, timeout)
    if not src_bytes:
        raise LiveImageError("live image call returned zero image bytes")
    try:
        img = Image.open(io.BytesIO(src_bytes))
        img.load()
        if img.mode in ("RGBA", "LA", "PA"):
            canvas = Image.new("RGB", img.size, (0, 0, 0))
            canvas.paste(img, mask=img.split()[-1])
            img = canvas
        else:
            img = img.convert("RGB")
        img.save(out_path, format="PNG")
    except Exception as exc:
        raise LiveImageError("cannot decode/normalize image bytes: %r"
                             % (exc,))
    with open(out_path, "rb") as fh:
        saved = fh.read()
    return {
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "model": parsed.get("model") or model,
        "latency_ms": latency_ms,
        "request_id": request_id,
        "bytes": len(saved),
        "sha256": hashlib.sha256(saved).hexdigest(),
        "transport": transport.get("transport", "?"),
        "out_path": out_path,
    }


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Afterglow live image probe.")
    ap.add_argument("--case", required=True)
    ap.add_argument("--trace-sha", required=True)
    ap.add_argument("--diagnosis", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    prompt = build_prompt(a.case, a.trace_sha, a.diagnosis)
    try:
        info = generate(prompt, a.out)
    except LiveImageError as exc:
        print("IMAGE FAILED: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps({k: v for k, v in info.items() if k != "prompt"},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
