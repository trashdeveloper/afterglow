"""Per-run token+$ cost ledger tests (static fixtures, no model calls).

Run: python3 tests/test_cost.py -v   (from repo root)

- test_pricing_table_single_place: afterglow_core.cost exposes one
  clearly-labeled ESTIMATE table covering both Muse models in use
  (muse-spark-1.3-contributor text, muse-image-1.0 image).
- test_typical_bundle: a fixture mirroring runs/live-20261026T2248Z
  (usage prompt 1457 / completion 5940 / total 7397 + successful
  image_response) yields text_tokens 7397, image_calls 1, and a
  hand-checkable est_usd.
- test_missing_fields_tolerance: missing usage keys, missing
  image_response.json, or a bare empty dir all yield zeros --
  never raise.
- test_failed_image_counts_zero: image_response with error/FAILED
  counts 0 image calls (no charge for a diagram we never got).
- test_loop_writes_cost_json: loop.write_cost_json() creates
  cost.json with the required keys in the NEW bundle dir and leaves
  an adjacent OLD bundle byte-identical (window rule: never edit
  old bundles).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import cost as cost_ledger
from agent import loop


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, indent=2) + "\n")


def _typical_bundle(run_dir):
    _write(os.path.join(run_dir, "api", "text_response.json"), {
        "model": "muse-spark-1.3-contributor",
        "latency_ms": 46457,
        "request_id": "chatcmpl-test",
        "usage": {
            "prompt_tokens": 1457,
            "completion_tokens": 5940,
            "total_tokens": 7397,
        },
    })
    _write(os.path.join(run_dir, "api", "image_response.json"), {
        "model": "muse-image-1.0",
        "request_id": "img-test",
        "latency_ms": 36501,
        "bytes": 1593195,
        "sha256": "e880e36761ac7d5c6705f62f82bbf6b25c5bf95ed00f5906338999f2d3020f9c",
    })


class TestCostLedger(unittest.TestCase):
    def test_pricing_table_single_place(self):
        table = cost_ledger.PRICING
        self.assertIn("muse-spark-1.3-contributor", table)
        self.assertIn("muse-image-1.0", table)
        text = table["muse-spark-1.3-contributor"]
        self.assertGreater(text["input_usd_per_1k"], 0)
        self.assertGreater(text["output_usd_per_1k"], 0)
        self.assertGreater(table["muse-image-1.0"]["usd_per_image"], 0)
        self.assertTrue(cost_ledger.ESTIMATES_LABEL)

    def test_typical_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            _typical_bundle(tmp)
            got = cost_ledger.compute_run_cost(tmp)
            self.assertEqual(got["text_tokens"], 7397)
            self.assertEqual(got["image_calls"], 1)
            rates = cost_ledger.PRICING["muse-spark-1.3-contributor"]
            img = cost_ledger.PRICING["muse-image-1.0"]["usd_per_image"]
            expected = round(
                1457 / 1000 * rates["input_usd_per_1k"]
                + 5940 / 1000 * rates["output_usd_per_1k"]
                + img, 6)
            self.assertAlmostEqual(got["est_usd"], expected, places=6)
            # Pinned hand-verified value for runs/live-20261026T2248Z
            # (1457/1000*0.002 + 5940/1000*0.008 + 0.03 = 0.080434);
            # locked in code, not just prose.
            self.assertAlmostEqual(got["est_usd"], 0.080434, places=6)
            self.assertGreater(got["est_usd"], 0)

    def test_missing_fields_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            # usage block absent entirely
            _write(os.path.join(tmp, "api", "text_response.json"),
                   {"model": "muse-spark-1.3-contributor"})
            got = cost_ledger.compute_run_cost(tmp)
            self.assertEqual(got["text_tokens"], 0)
            self.assertEqual(got["image_calls"], 0)
            self.assertEqual(got["est_usd"], 0.0)
        with tempfile.TemporaryDirectory() as tmp:
            # bare dir, no api/ at all
            got = cost_ledger.compute_run_cost(tmp)
            self.assertEqual(
                (got["text_tokens"], got["image_calls"], got["est_usd"]),
                (0, 0, 0.0))

    def test_failed_image_counts_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(os.path.join(tmp, "api", "text_response.json"), {
                "model": "muse-spark-1.3-contributor",
                "usage": {"prompt_tokens": 100, "completion_tokens": 200,
                          "total_tokens": 300},
            })
            _write(os.path.join(tmp, "api", "image_response.json"),
                   {"error": "live image call failed", "status": "FAILED"})
            got = cost_ledger.compute_run_cost(tmp)
            self.assertEqual(got["text_tokens"], 300)
            self.assertEqual(got["image_calls"], 0)
            self.assertEqual(got["sam_calls"], 0)
            rates = cost_ledger.PRICING["muse-spark-1.3-contributor"]
            expected = round(
                100 / 1000 * rates["input_usd_per_1k"]
                + 200 / 1000 * rates["output_usd_per_1k"], 6)
            self.assertAlmostEqual(got["est_usd"], expected, places=6)

    def test_sam_success_counts_one_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(os.path.join(tmp, "api", "text_response.json"),
                    {"usage": {"total_tokens": 0}})
            _write(os.path.join(tmp, "api", "image_response.json"),
                    {"error": "skipped"})
            _write(os.path.join(tmp, "api", "sam_response.json"),
                    {"model": "sam-3.1", "ok": True, "lanes": 4})
            got = cost_ledger.compute_run_cost(tmp)
            self.assertEqual(got["sam_calls"], 1)
            self.assertEqual(got["image_calls"], 0)
            expected = round(
                cost_ledger.PRICING["sam-3.1"]["usd_per_call"], 6)
            self.assertAlmostEqual(got["est_usd"], expected, places=6)

    def test_sam_failure_counts_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(os.path.join(tmp, "api", "text_response.json"),
                    {"usage": {"total_tokens": 0}})
            _write(os.path.join(tmp, "api", "sam_response.json"),
                    {"model": "sam-3.1", "ok": False,
                     "error": "transport down"})
            got = cost_ledger.compute_run_cost(tmp)
            self.assertEqual(got["sam_calls"], 0)
            self.assertAlmostEqual(got["est_usd"], 0.0, places=6)

    def test_loop_writes_cost_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "runs", "old-bundle")
            new = os.path.join(tmp, "runs", "new-bundle")
            os.makedirs(os.path.join(old, "api"))
            _write(os.path.join(old, "api", "text_response.json"),
                   {"usage": {"total_tokens": 5}})
            sentinel = os.path.join(old, "meta.json")
            _write(sentinel, {"run_id": "old-bundle"})
            with open(sentinel, "rb") as fh:
                before = fh.read()
            _typical_bundle(new)
            info = loop.write_cost_json(new)
            self.assertEqual(
                (info["text_tokens"], info["image_calls"]),
                (7397, 1))
            with open(os.path.join(new, "cost.json"),
                      encoding="utf-8") as fh:
                on_disk = json.load(fh)
            self.assertEqual(on_disk["text_tokens"], 7397)
            self.assertEqual(on_disk["image_calls"], 1)
            self.assertIn("est_usd", on_disk)
            with open(sentinel, "rb") as fh:
                after = fh.read()
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
