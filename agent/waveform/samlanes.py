#!/usr/bin/env python3
"""SAM lane-aware waveform slicing: sam-3.1 lane boxes + PNG lane crops.

CLIENT ONLY -- no ``loop.py`` wiring here. Wiring the loop's waveform
path to this client is a later task (fired by the controller per
``eval/ablation-samlanes.md``); this module just builds the wire body,
validates the wire response, and performs the timed POST, plus a
stdlib-only PNG cropper for the returned lane boxes.

Proven live shape (per task brief, on our session key):
  POST https://api.meta.ai/v1/chat/completions
  model sam-3.1, standard OpenAI-ish shape (messages + image_url),
  Bearer auth -- the same transport as the Spark path in agent/loop.py.

Public contract (pinned by tests/test_waveform_samlanes.py):
  call_sam(bearer, png_bytes) -> lane boxes
      ([{"x0","y0","x1","y1"}] int pixel coords, top-to-bottom lane order)
  crop_lanes(png_bytes, boxes) -> PNG crops
      ([bytes], one PNG per box, same order, stdlib PNG codec -- no Pillow)

Failure semantics mirror ``agent/loop.py``: fatal and loud, never a
silent heuristic fallback. ``SamError`` carries the HTTP ``.status``
(``None`` for transport/timeout failures). Programming errors (empty
PNG, malformed boxes, out-of-bounds crops) raise ``ValueError`` -- the
same loud convention as ``waveform/vcdrender.py``.

Key/base envs are the SAME names the Spark path uses in loop.py:
  key  from MODEL_API_KEY / SPARK_API_KEY / META_API_KEY
  base from SPARK_API_URL (default https://api.meta.ai)
No key is ever hardcoded here; no key material ever appears in an
exception message (tests assert both).
"""

from __future__ import annotations

import base64
import json
import math
import os
import struct
import time
import urllib.error
import urllib.request
import zlib

SAM_MODEL = "sam-3.1"
SAM_MAX_TOKENS = 1024
SAM_TIMEOUT_S = 120
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

DEFAULT_BASE = "https://api.meta.ai"
KEY_ENV_VARS = ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY")
BASE_ENV_VAR = "SPARK_API_URL"

SAM_SYSTEM_PROMPT = (
    "You segment waveform screenshots into signal lanes. "
    "Output JSON only, exactly one object of the form "
    '{"lanes": [{"x0": <int>, "y0": <int>, '
    '"x1": <int>, "y1": <int>}, ...]}. '
    "One box per horizontal signal-lane row, in top-to-bottom order. "
    "Coordinates are integer pixels in the input image frame "
    "(origin top-left, x1 > x0, y1 > y0). No prose, no fences."
)

SAM_USER_PROMPT = (
    "Segment this waveform PNG into its signal lanes. "
    "Return one tight box per lane row covering the full row width. "
    "JSON only."
)


class SamError(RuntimeError):
    """Fatal SAM call failure (LoopError-style, always loud)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# key / base resolution (identical names + order to agent/loop.py api_key)
# --------------------------------------------------------------------------

def api_key():
    """Resolve the API key from the shared env names.

    Same names as loop.py ``api_key()``: MODEL_API_KEY, then
    SPARK_API_KEY, then META_API_KEY. Raises SamError when none is
    set -- the caller fails loudly instead of firing unauthenticated.
    The key value is NEVER echoed back in the error.
    """
    for var in KEY_ENV_VARS:
        key = os.environ.get(var, "").strip()
        if key:
            return key
    raise SamError(
        "missing SAM API key: set MODEL_API_KEY "
        "(SPARK_API_KEY / META_API_KEY also accepted); "
        "refusing to continue without the live segmenter")


def sam_base_url():
    """Resolve the chat-completions base from the shared env name.

    Same name as loop.py ``text_url()``: SPARK_API_URL, default
    https://api.meta.ai. Returns the full ...""/v1/chat/completions"
    URL (a value already ending in chat/completions passes through).
    """
    raw = os.environ.get(BASE_ENV_VAR, "").strip()
    if raw.rstrip("/").endswith("chat/completions"):
        return raw.rstrip("/")
    base = (raw or DEFAULT_BASE).rstrip("/")
    return base + CHAT_COMPLETIONS_PATH


# --------------------------------------------------------------------------
# request building (pure, no I/O -- pin the exact wire shape in tests)
# --------------------------------------------------------------------------

def _require_png(png_bytes):
    if not isinstance(png_bytes, (bytes, bytearray)) or not png_bytes:
        raise ValueError("call_sam: png_bytes must be non-empty PNG bytes")
    if bytes(png_bytes[:8]) != _PNG_SIG:
        raise ValueError("call_sam: png_bytes is not a PNG "
                         "(bad 8-byte signature); refusing to burn a call")


def build_request(png_bytes, model=SAM_MODEL, max_tokens=SAM_MAX_TOKENS):
    """Return the exact JSON-serializable sam-3.1 request body.

    OpenAI-ish shape (mirrors the Spark path): model + messages with a
    base64 data-URL image part. Raises ValueError on empty/non-PNG
    input or bad model/max_tokens -- before any network is touched.
    """
    _require_png(png_bytes)
    if not isinstance(model, str) or not model.strip():
        raise ValueError("build_request: model must be a non-empty string")
    if (isinstance(max_tokens, bool) or not isinstance(max_tokens, int)
            or max_tokens <= 0):
        raise ValueError("build_request: max_tokens must be a positive int")
    b64 = base64.b64encode(bytes(png_bytes)).decode("ascii")
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SAM_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": SAM_USER_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + b64}},
            ]},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }


# --------------------------------------------------------------------------
# lane-box parsing (pure, no I/O -- canned fixtures in tests, no network)
# --------------------------------------------------------------------------

def _coerce_coord(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SamError("SAM lane box %r is not numeric: %r" % (name, value))
    if not math.isfinite(value):
        raise SamError("SAM lane box %r is not finite: %r" % (name, value))
    return int(round(value))


def parse_lane_boxes(obj):
    """Extract validated lane boxes from a decoded SAM content object.

    Accepts ``{"lanes": [...]}``, ``{"boxes": [...]}``, or a bare
    ``[...]`` list. Each box must carry numeric x0/y0/x1/y1 with
    x1 > x0 and y1 > y0 (floats are rounded to int pixels). Returns
    the boxes sorted top-to-bottom (y0, then x0) in lane order.
    Raises SamError on anything else -- never invents a lane.
    """
    if isinstance(obj, dict):
        if isinstance(obj.get("lanes"), list):
            items = obj["lanes"]
        elif isinstance(obj.get("boxes"), list):
            items = obj["boxes"]
        else:
            raise SamError("SAM output has no 'lanes'/'boxes' list: keys=%r"
                           % (sorted(obj.keys()),))
    elif isinstance(obj, list):
        items = obj
    else:
        raise SamError("SAM output is not a JSON object or list: %r"
                       % (type(obj).__name__,))
    if not items:
        raise SamError("SAM returned zero lane boxes; "
                       "refusing to invent lanes")
    boxes = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise SamError("SAM lane %d is not an object: %r" % (i, item))
        try:
            x0 = _coerce_coord(item["x0"], "x0")
            y0 = _coerce_coord(item["y0"], "y0")
            x1 = _coerce_coord(item["x1"], "x1")
            y1 = _coerce_coord(item["y1"], "y1")
        except KeyError as exc:
            raise SamError("SAM lane %d missing coordinate %s: %r"
                           % (i, exc, item))
        if not (x1 > x0 and y1 > y0):
            raise SamError("SAM lane %d is degenerate (x1<=x0 or y1<=y0): "
                           "%r" % (i, item))
        boxes.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1})
    boxes.sort(key=lambda b: (b["y0"], b["x0"]))
    return boxes


def parse_sam_content(content):
    """Extract lane boxes from a chat-completions message content string.

    Tolerates ``` fences (same rule as loop.extract_model_json);
    content-block lists are joined first. Raises SamError when the
    content is empty, has no JSON object/list, or carries no valid
    lane boxes.
    """
    if isinstance(content, list):  # content-block style payloads
        content = "".join(
            block.get("text", "") for block in content
            if isinstance(block, dict)
            and isinstance(block.get("text"), str))
    if not isinstance(content, str) or not content.strip():
        raise SamError("SAM returned empty content; refusing to invent lanes")
    text = content.strip()
    if text.startswith("```"):
        import re
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = min([i for i in (text.find("{"), text.find("["))
                 if i >= 0], default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    if start < 0 or end <= start:
        raise SamError("SAM content carries no JSON object/list: %r"
                       % (content[:200],))
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise SamError("SAM content JSON parse failed: %s" % (exc,))
    return parse_lane_boxes(obj)


def parse_response(resp_json):
    """Extract lane boxes from a sam-3.1 chat-completions response object.

    Accepts a decoded dict, or raw str/bytes (parsed as JSON first).
    Raises SamError on: non-JSON input, API ``error`` payload,
    missing/empty ``choices``, missing ``message``, empty content, or
    content with no valid lane boxes.
    """
    if isinstance(resp_json, (str, bytes)):
        try:
            text_in = (resp_json.decode("utf-8")
                       if isinstance(resp_json, bytes) else resp_json)
            resp_json = json.loads(text_in)
        except Exception as exc:
            raise SamError("SAM response is not JSON: %r" % (exc,))
    if not isinstance(resp_json, dict):
        raise SamError("SAM response is not a JSON object: %r"
                       % (type(resp_json).__name__,))
    err = resp_json.get("error")
    if err:
        raise SamError("SAM API error: %s" % (json.dumps(err)[:500],))
    choices = resp_json.get("choices")
    if not isinstance(choices, list) or not choices:
        raise SamError("SAM response missing non-empty 'choices'")
    first = choices[0]
    if not isinstance(first, dict):
        raise SamError("SAM response choices[0] is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise SamError("SAM response missing 'choices[0].message'")
    return parse_sam_content(message.get("content"))


# --------------------------------------------------------------------------
# live call (stdlib urllib only)
# --------------------------------------------------------------------------

def _endpoint_url(base):
    base = (base or "").rstrip("/")
    if not base:
        raise SamError("call_endpoint: base_url must be non-empty")
    if base.endswith("chat/completions"):
        return base
    return base + CHAT_COMPLETIONS_PATH


def call_endpoint(key, base, body, timeout=SAM_TIMEOUT_S):
    """POST body to the chat-completions endpoint; return lane boxes.

    Returns ``(boxes, latency_ms, request_id)``. Raises SamError with
    ``.status`` set on HTTP failures (e.g. 401/429/500), and with
    ``.status=None`` on transport/timeout failures or bad payloads.
    """
    if not key or not str(key).strip():
        raise SamError("call_endpoint: key must be non-empty")
    url = _endpoint_url(base)
    try:
        raw_body = json.dumps(body).encode("utf-8")
    except Exception as exc:
        raise SamError("call_endpoint: body is not JSON-serializable: %r"
                       % (exc,))
    req = urllib.request.Request(
        url, data=raw_body, method="POST",
        headers={"Authorization": "Bearer " + str(key).strip(),
                 "Content-Type": "application/json",
                 "Accept": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_bytes = resp.read()
            headers = dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = ""
        raise SamError("SAM HTTP %s: %s" % (exc.code, detail),
                       status=exc.code)
    except Exception as exc:
        raise SamError("SAM call failed (down/timeout): %r" % (exc,))
    latency_ms = int((time.monotonic() - t0) * 1000)
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise SamError("SAM response is not JSON: %r" % (exc,))
    boxes = parse_response(parsed)
    request_id = str(parsed.get("id") or parsed.get("request_id")
                     or headers.get("x-request-id", ""))
    return boxes, latency_ms, request_id


def call_sam(bearer, png_bytes, *, model=SAM_MODEL,
             timeout=SAM_TIMEOUT_S, base_url=None):
    """Segment a waveform PNG into signal-lane boxes via sam-3.1.

    ``bearer`` is the session key; when falsy the shared
    MODEL_API_KEY/SPARK_API_KEY/META_API_KEY envs are read exactly
    like loop.py ``api_key()`` (missing key fails loudly, never
    unauthenticated). ``base_url`` overrides SPARK_API_URL (tests
    point it at loopback; default resolves from the env). Returns the
    lane-box list (int pixels, top-to-bottom). Fires exactly one live
    POST; every failure mode raises SamError loudly.
    """
    key = (str(bearer).strip() if bearer else "") or api_key()
    body = build_request(png_bytes, model=model)  # validates PNG first
    base = base_url if base_url is not None else sam_base_url()
    boxes, _, _ = call_endpoint(key, base, body, timeout=timeout)
    return boxes


# --------------------------------------------------------------------------
# stdlib-only PNG codec (no Pillow: this module stays stdlib + urllib)
# --------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _read_chunks(png_bytes):
    if bytes(png_bytes[:8]) != _PNG_SIG:
        raise ValueError("not a PNG: bad 8-byte signature")
    pos = 8
    chunks = []
    total = len(png_bytes)
    while pos + 8 <= total:
        (length,) = struct.unpack(">I", png_bytes[pos:pos + 4])
        ctype = bytes(png_bytes[pos + 4:pos + 8])
        pos += 8
        data = bytes(png_bytes[pos:pos + length])
        if len(data) != length:
            raise ValueError("truncated PNG chunk %r" % (ctype,))
        pos += length
        (crc,) = struct.unpack(">I", png_bytes[pos:pos + 4])
        pos += 4
        import binascii
        if binascii.crc32(ctype + data) & 0xFFFFFFFF != crc:
            raise ValueError("PNG chunk %r CRC mismatch" % (ctype,))
        chunks.append((ctype, data))
        if ctype == b"IEND":
            break
    return chunks


def png_size(png_bytes):
    """Return (width, height) from a PNG's IHDR (pure stdlib)."""
    if not isinstance(png_bytes, (bytes, bytearray)) or not png_bytes:
        raise ValueError("png_size: empty input, not a PNG")
    for ctype, data in _read_chunks(bytes(png_bytes)):
        if ctype == b"IHDR":
            if len(data) != 13:
                raise ValueError("png_size: corrupt IHDR")
            w, h = struct.unpack(">II", data[:8])
            return (w, h)
    raise ValueError("png_size: no IHDR found")


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _decode_png_rgb(png_bytes):
    """Decode a PNG to (w, h, rgb_bytes); supports 8-bit gray/RGB/RGBA.

    Non-interlaced only; palette (type 3) and <8-bit depths raise
    ValueError loudly (vcdrender writes 8-bit RGB, so the pilot path
    never hits this). Gray expands to RGB, alpha is dropped.
    """
    raw = bytes(png_bytes)
    chunks = _read_chunks(raw)
    ihdr = next((d for t, d in chunks if t == b"IHDR"), None)
    if ihdr is None or len(ihdr) != 13:
        raise ValueError("crop: PNG has no valid IHDR")
    w, h, depth, ctype, comp, filt, inter = struct.unpack(">IIBBBBB", ihdr)
    if depth != 8:
        raise ValueError("crop: only 8-bit PNG supported, got depth %d"
                         % (depth,))
    if inter != 0:
        raise ValueError("crop: interlaced PNG unsupported")
    if ctype not in (0, 2, 6):
        raise ValueError("crop: unsupported color type %d "
                         "(need gray/RGB/RGBA)" % (ctype,))
    if comp != 0 or filt != 0:
        raise ValueError("crop: unsupported compression/filter method")
    idat = b"".join(d for t, d in chunks if t == b"IDAT")
    try:
        blob = zlib.decompress(idat)
    except Exception as exc:
        raise ValueError("crop: IDAT zlib decompress failed: %r" % (exc,))
    channels = {0: 1, 2: 3, 6: 4}[ctype]
    stride = w * channels
    if len(blob) != h * (stride + 1):
        raise ValueError("crop: IDAT size %d != %dx%d frame"
                         % (len(blob), w, h))
    rgb = bytearray(w * h * 3)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        f = blob[pos]
        pos += 1
        line = bytearray(blob[pos:pos + stride])
        pos += stride
        if f == 0:
            recon = line
        elif f == 1:
            recon = bytearray(stride)
            for i in range(stride):
                a = recon[i - channels] if i >= channels else 0
                recon[i] = (line[i] + a) & 0xFF
        elif f == 2:
            recon = bytearray((line[i] + prev[i]) & 0xFF
                              for i in range(stride))
        elif f == 3:
            recon = bytearray(stride)
            for i in range(stride):
                a = recon[i - channels] if i >= channels else 0
                recon[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            recon = bytearray(stride)
            for i in range(stride):
                a = recon[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                recon[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        else:
            raise ValueError("crop: unknown PNG filter %d on row %d"
                             % (f, y))
        prev = recon
        dst = y * w * 3
        if channels == 3:
            rgb[dst:dst + w * 3] = recon
        elif channels == 1:
            for x in range(w):
                v = recon[x]
                rgb[dst + 3 * x:dst + 3 * x + 3] = bytes((v, v, v))
        else:  # RGBA: drop alpha
            for x in range(w):
                rgb[dst + 3 * x:dst + 3 * x + 3] = recon[4 * x:4 * x + 3]
    return w, h, bytes(rgb)


def _encode_png_rgb(w, h, rgb):
    """Encode raw RGB bytes to a PNG (8-bit truecolor, filter-0 rows)."""
    if w <= 0 or h <= 0:
        raise ValueError("encode: bad dimensions %dx%d" % (w, h))
    if len(rgb) != w * h * 3:
        raise ValueError("encode: RGB size %d != %dx%dx3"
                         % (len(rgb), w, h))
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]
    import binascii

    def chunk(ctype, data):
        return (struct.pack(">I", len(data)) + ctype + data
                + struct.pack(">I", binascii.crc32(ctype + data)
                              & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (_PNG_SIG + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw)))
            + chunk(b"IEND", b""))


def _clip_box(box, w, h):
    x0 = min(max(int(box["x0"]), 0), w)
    y0 = min(max(int(box["y0"]), 0), h)
    x1 = min(max(int(box["x1"]), 0), w)
    y1 = min(max(int(box["y1"]), 0), h)
    if not (x1 > x0 and y1 > y0):
        raise ValueError("crop: box clips to nothing "
                         "(outside %dx%d): %r" % (w, h, box))
    return x0, y0, x1, y1


def crop_lanes(png_bytes, boxes):
    """Crop lane boxes out of a PNG; return one PNG per box (same order).

    Pure stdlib (no Pillow): decodes once, slices rows/cols, re-encodes
    each crop as 8-bit RGB PNG. Boxes are ``{"x0","y0","x1","y1"}``
    pixel dicts; edges touching/outside the frame are CLIPPED to the
    frame (documented mechanical repair, same precedent as loop.py's
    hunk-count normalization -- content is never altered, only the
    frame edge is enforced). Fully-outside or degenerate boxes raise
    ValueError loudly; an empty box list raises ValueError.
    """
    if not isinstance(boxes, (list, tuple)) or not boxes:
        raise ValueError("crop_lanes: boxes must be a non-empty list")
    w, h, rgb = _decode_png_rgb(bytes(png_bytes))
    crops = []
    for i, box in enumerate(boxes):
        if not isinstance(box, dict):
            raise ValueError("crop_lanes: box %d is not a dict: %r"
                             % (i, box))
        for key in ("x0", "y0", "x1", "y1"):
            val = box.get(key)
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError("crop_lanes: box %d key %r not numeric: %r"
                                 % (i, key, box))
            if not math.isfinite(val):
                raise ValueError("crop_lanes: box %d key %r not finite"
                                 % (i, key))
        x0, y0, x1, y1 = _clip_box(
            {"x0": int(box["x0"]), "y0": int(box["y0"]),
             "x1": int(box["x1"]), "y1": int(box["y1"])}, w, h)
        cw, chh = x1 - x0, y1 - y0
        out = bytearray(cw * chh * 3)
        for yy in range(y0, y1):
            src = (yy * w + x0) * 3
            dst = (yy - y0) * cw * 3
            out[dst:dst + cw * 3] = rgb[src:src + cw * 3]
        crops.append(_encode_png_rgb(cw, chh, bytes(out)))
    return crops


def fixed_grid_lanes(png_bytes, n_lanes):
    """Baseline slicer: split the frame into N equal horizontal strips.

    The fixed-grid control arm for eval/ablation-samlanes.md: no model,
    no semantics -- pure geometry. Returns N ``{"x0","y0","x1","y1"}``
    boxes covering the full frame top-to-bottom (remainder rows go to
    the last lane). Raises ValueError on bad input.
    """
    if isinstance(n_lanes, bool) or not isinstance(n_lanes, int):
        raise ValueError("fixed_grid_lanes: n_lanes must be an int")
    if n_lanes <= 0:
        raise ValueError("fixed_grid_lanes: n_lanes must be positive")
    w, h = png_size(bytes(png_bytes))
    boxes = []
    for i in range(n_lanes):
        y0 = (h * i) // n_lanes
        y1 = (h * (i + 1)) // n_lanes if i + 1 < n_lanes else h
        boxes.append({"x0": 0, "y0": y0, "x1": w, "y1": y1})
    return [b for b in boxes if b["y1"] > b["y0"]]
