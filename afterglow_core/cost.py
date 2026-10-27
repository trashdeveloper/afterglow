"""Per-run token+$ cost ledger (AGENTS.md proof bundle: cost.json).

Every live run calls the Meta Model API (Text diagnose + Image explain).
We have a $100 experiment budget, so every run must account for itself.

Pricing below covers the two Muse models in use:
  text  : muse-spark-1.3-contributor (loop.TEXT_MODEL)
  image : muse-image-1.0             (visual.IMAGE_MODEL)

ALL RATES ARE ESTIMATES -- clearly labeled, editable in this one place.
Replace them with contract rates when Finance confirms; the calculator
below does not change. BUDGET_USD is the experiment cap the ledger
tracks against.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any, Dict

ESTIMATES_LABEL = (
    "ESTIMATES ONLY -- placeholder rates, not billed prices. "
    "Edit PRICING in afterglow_core/cost.py when contract rates land."
)

BUDGET_USD = 100.0

# Single editable pricing table. Units: USD per 1000 tokens (text),
# USD per generated image (flat, no token meter on explain.png).
PRICING: Dict[str, Dict[str, float]] = {
    "muse-spark-1.3-contributor": {
        "input_usd_per_1k": 0.002,
        "output_usd_per_1k": 0.008,
    },
    "muse-image-1.0": {
        "usd_per_image": 0.03,
    },
}

TEXT_MODEL_DEFAULT = "muse-spark-1.3-contributor"
IMAGE_MODEL_DEFAULT = "muse-image-1.0"


def _as_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def compute_run_cost(run_dir: str) -> Dict[str, Any]:
    """Cost a run bundle dir. Missing files/fields tolerate to zero.

    Reads api/text_response.json (usage.prompt/completion/total_tokens)
    and api/image_response.json (success = no error key). Never raises
    on absent or malformed data -- an incomplete bundle costs $0 here
    and stays honest instead of crashing the loop's finish() path.

    Returns {text_tokens, image_calls, est_usd} plus an audit breakdown.
    """
    text_resp = _read_json(os.path.join(run_dir, "api", "text_response.json"))
    img_resp = _read_json(os.path.join(run_dir, "api", "image_response.json"))

    usage = text_resp.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    prompt_tokens = _as_int(usage.get("prompt_tokens"))
    completion_tokens = _as_int(usage.get("completion_tokens"))
    total_tokens = _as_int(usage.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens

    image_ok = bool(img_resp) and not img_resp.get("error")
    image_calls = 1 if image_ok else 0

    text_model = str(text_resp.get("model") or TEXT_MODEL_DEFAULT)
    rates = PRICING.get(text_model, PRICING[TEXT_MODEL_DEFAULT])
    image_model = str(img_resp.get("model") or IMAGE_MODEL_DEFAULT)
    img_rate = PRICING.get(image_model,
                            PRICING[IMAGE_MODEL_DEFAULT]).get(
        "usd_per_image", 0.0)

    est_usd = round(
        prompt_tokens / 1000 * float(rates.get("input_usd_per_1k", 0.0))
        + completion_tokens / 1000 * float(rates.get("output_usd_per_1k", 0.0))
        + image_calls * float(img_rate), 6)

    return {
        "text_tokens": total_tokens,
        "image_calls": image_calls,
        "est_usd": est_usd,
        "text_model": text_model,
        "text_input_tokens": prompt_tokens,
        "text_output_tokens": completion_tokens,
        "image_model": image_model if image_ok else "",
        "budget_usd": BUDGET_USD,
        "pricing_note": ESTIMATES_LABEL,
        "computed_utc": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
