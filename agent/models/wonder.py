"""Second-model diagnosis path: muse-wonder-1.0 via a hosted endpoint.

Meta-hosted OpenAI-compatible ``chat-completions`` API for
``muse-wonder-1.0``. Stdlib only (``urllib``).

CLIENT ONLY -- no ``loop.py`` wiring here. Wiring the loop's text-call
path to this client is a later task; this module just builds the wire
body, validates the wire response, and performs the timed POST.

Proven live shape (per task brief, on our key):
  POST https://api.meta.ai/v1/chat/completions
  model muse-wonder-1.0, standard OpenAI shape,
  response ``usage`` carries ``reasoning_tokens``.

Failure semantics mirror ``agent/loop.py`` ``LoopError``: fatal and
loud, never a silent heuristic fallback. ``WonderError`` carries
the HTTP ``.status`` (``None`` for transport/timeout failures).

Key/base envs are the SAME names the Spark path uses in loop.py:
  key  from MODEL_API_KEY / SPARK_API_KEY / META_API_KEY
  base from SPARK_API_URL (default https://api.meta.ai)
See helpers ``api_key()`` / ``base_url()`` below; the three core
functions (build_request / parse_response / call_endpoint) take
explicit key/base args exactly like openweights.py so callers --
including loop.py's --wonder path -- stay explicit and testable.
"""

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_MODEL = "muse-wonder-1.0"
DEFAULT_MAX_TOKENS = 1024
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

DEFAULT_BASE = "https://api.meta.ai"
KEY_ENV_VARS = ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY")
BASE_ENV_VAR = "SPARK_API_URL"


class WonderError(RuntimeError):
    """Fatal wonder call failure (LoopError-style, always loud)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def api_key():
    """Resolve the text API key from the shared env names.

    Same names as loop.py ``api_key()``: MODEL_API_KEY, then
    SPARK_API_KEY, then META_API_KEY. Raises WonderError when none
    is set -- the caller fails loudly instead of firing unauthenticated.
    """
    for var in KEY_ENV_VARS:
        key = os.environ.get(var, "").strip()
        if key:
            return key
    raise WonderError(
        "missing text API key: set MODEL_API_KEY "
        "(SPARK_API_KEY / META_API_KEY also accepted); "
        "refusing to continue without the live decider")


def base_url():
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


def build_request(prompt, model=DEFAULT_MODEL, max_tokens=DEFAULT_MAX_TOKENS):
    """Return the exact JSON-serializable chat-completions request body."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("build_request: prompt must be a non-empty string")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("build_request: model must be a non-empty string")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError("build_request: max_tokens must be a positive int")
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }


def parse_response(resp_json):
    """Extract assistant text from a chat-completions response object.

    Accepts a decoded dict, or raw str/bytes (parsed as JSON first).
    Raises WonderError on: non-JSON input, API ``error`` payload,
    missing/empty ``choices``, missing ``message``, missing or empty
    ``content``.

    Note: the live wonder endpoint returns ``usage.reasoning_tokens``
    alongside the text. That meter lives in the raw payload (preserved
    by the caller / loop envelope); parsing only gates on the text
    being present and non-empty, exactly like the Spark path.
    """
    if isinstance(resp_json, (str, bytes)):
        try:
            text_in = resp_json.decode("utf-8") if isinstance(resp_json, bytes) else resp_json
            resp_json = json.loads(text_in)
        except Exception as exc:
            raise WonderError("wonder response is not JSON: %r" % (exc,))
    if not isinstance(resp_json, dict):
        raise WonderError(
            "wonder response is not a JSON object: %r" % type(resp_json).__name__)
    err = resp_json.get("error")
    if err:
        raise WonderError("wonder API error: %s" % (json.dumps(err)[:500],))
    choices = resp_json.get("choices")
    if not isinstance(choices, list) or not choices:
        raise WonderError("wonder response missing non-empty 'choices'")
    first = choices[0]
    if not isinstance(first, dict):
        raise WonderError("wonder response choices[0] is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise WonderError("wonder response missing 'choices[0].message'")
    content = message.get("content")
    if isinstance(content, list):  # content-block style payloads
        content = "".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str))
    if not isinstance(content, str) or not content.strip():
        raise WonderError("wonder response has empty 'content'; refusing to invent text")
    return content


def _endpoint_url(base_url):
    base = (base_url or "").rstrip("/")
    if not base:
        raise WonderError("call_endpoint: base_url must be non-empty")
    if base.endswith("chat/completions"):
        return base
    return base + CHAT_COMPLETIONS_PATH


def call_endpoint(api_key, base_url, body, timeout=60):
    """POST body to the chat-completions endpoint.

    Returns ``(text, latency_ms, request_id)``. Raises WonderError
    with ``.status`` set on HTTP failures (e.g. 429/500), and with
    ``.status=None`` on transport/timeout failures or bad payloads.
    """
    if not api_key:
        raise WonderError("call_endpoint: api_key must be non-empty")
    url = _endpoint_url(base_url)
    try:
        raw_body = json.dumps(body).encode("utf-8")
    except Exception as exc:
        raise WonderError("call_endpoint: body is not JSON-serializable: %r" % (exc,))
    req = urllib.request.Request(
        url, data=raw_body, method="POST",
        headers={"Authorization": "Bearer " + api_key,
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
        raise WonderError(
            "wonder HTTP %s: %s" % (exc.code, detail), status=exc.code)
    except Exception as exc:
        raise WonderError("wonder call failed (down/timeout): %r" % (exc,))
    latency_ms = int((time.monotonic() - t0) * 1000)
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise WonderError("wonder response is not JSON: %r" % (exc,))
    text = parse_response(parsed)
    request_id = str(parsed.get("id") or parsed.get("request_id")
                     or headers.get("x-request-id", ""))
    return text, latency_ms, request_id
