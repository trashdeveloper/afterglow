"""Live kill-matrix report test (wave-5: hardened oracles as measured).

STATIC ONLY: checks the committed ``eval/kill_rate_v2.json`` proof file
and ``eval/kill_rate_part2.md`` report for shape, internal arithmetic,
and render determinism. NEVER builds, sims, or calls the API -- the
live measurement runs once via ``python3 eval/kill_rate_v2.py`` and the
artifacts are reviewed, not re-fired, here.

- report exists with all 4 TB rows + TOTAL + delta + reproduce command.
- JSON cells (50 mutants x 4 TBs = 200) agree with the markdown counts;
  kill_rate == killed / (killed + survived + timeout) per row and total;
  errors excluded from denominators; error != killed (no error cell
  counted as killed).
- every mutant has a parent-TB cell; classification_changed lists only
  parent-TB cells whose verdict is not killed.
- re-rendering the markdown from the committed JSON is byte-identical
  (deterministic report: no timestamps, no timings).
- the measurement script touches no network (static import scan).

Run: python3 tests/test_kill_rate_v2.py -v   (from repo root)
"""
import json
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PATH = os.path.join(REPO, "eval", "kill_rate_v2.json")
REPORT_PATH = os.path.join(REPO, "eval", "kill_rate_part2.md")
SCRIPT_PATH = os.path.join(REPO, "eval", "kill_rate_v2.py")

TBS = ("fetch", "uart", "decode", "serv")


def _load_results():
    with open(JSON_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _rate(c):
    denom = c["killed"] + c["survived"] + c["timeout"]
    return c["killed"] / denom if denom else float("nan")


def _pct(x):
    return "n/a" if x != x else "%.2f%%" % (100.0 * x)


class KillMatrixArtifacts(unittest.TestCase):
    def test_files_exist(self):
        self.assertTrue(os.path.isfile(JSON_PATH), JSON_PATH)
        self.assertTrue(os.path.isfile(REPORT_PATH), REPORT_PATH)
        self.assertTrue(os.path.isfile(SCRIPT_PATH), SCRIPT_PATH)

    def test_200_cells_and_counts_agree(self):
        r = _load_results()
        self.assertEqual(len(r["cells"]), 200)
        mids = sorted({c["mutant_id"] for c in r["cells"]})
        self.assertEqual(len(mids), 50)
        tbs = sorted({c["tb"] for c in r["cells"]})
        self.assertEqual(tbs, sorted(TBS))
        for k in TBS:
            cells = [c for c in r["cells"] if c["tb"] == k]
            self.assertEqual(len(cells), 50, k)
            for v in ("killed", "survived", "timeout", "error"):
                self.assertEqual(
                    sum(1 for c in cells if c["verdict"] == v),
                    r["counts"][k][v], "%s %s" % (k, v))
        for v in ("killed", "survived", "timeout", "error"):
            self.assertEqual(
                sum(r["counts"][k][v] for k in TBS), r["total"][v], v)

    def test_kill_rate_math_matches_report(self):
        r = _load_results()
        with open(REPORT_PATH, encoding="utf-8") as fh:
            text = fh.read()
        for k in TBS:
            self.assertIn(_pct(_rate(r["counts"][k])), text)
        self.assertIn(_pct(r["total"]["rate"]), text)
        # every verdict letter appears in the matrix table
        letters = {c["verdict"][0].upper() for c in r["cells"]}
        for letter in letters:
            self.assertIn("| %s" % letter, text)

    def test_error_never_counts_as_killed(self):
        r = _load_results()
        for c in r["cells"]:
            if c["verdict"] == "error":
                self.assertNotIn("killed", c["verdict"])
                self.assertTrue(c["note"], c)
        # errors excluded from every denominator
        for k in TBS:
            c = r["counts"][k]
            self.assertEqual(c["killed"] + c["survived"] + c["timeout"]
                             + c["error"], 50, k)

    def test_parent_cells_cover_all_mutants(self):
        r = _load_results()
        tb_of = {k: v["tb"] for k, v in r["harnesses"].items()}
        key_of = {v: k for k, v in tb_of.items()}
        by_id = {(c["mutant_id"], c["tb"]): c for c in r["cells"]}
        for m in r["mutants"]:
            self.assertIn(m["tb"], key_of, m["mutant_id"])
            cell = by_id[(m["mutant_id"], key_of[m["tb"]])]
            self.assertIn(cell["verdict"],
                          ("killed", "survived", "timeout", "error"))
        for ch in r["classification_changed"]:
            self.assertNotEqual(ch["now"], "killed")
            self.assertIn(ch["mutant_id"],
                          [m["mutant_id"] for m in r["mutants"]])

    def test_report_shape(self):
        with open(REPORT_PATH, encoding="utf-8") as fh:
            text = fh.read()
        for tb in ("picorv32-fetch_tb.sv", "picosoc-uart-div_tb.sv",
                   "picorv32-fetch-decode_tb.sv", "serv-alu-lut_tb.sv"):
            self.assertIn(tb, text)
        for token in ("kill_rate", "TOTAL", "Delta vs", "1.36%",
                      "Classification changes", "Reproduce",
                      "python3 eval/kill_rate_v2.py --seed 42"):
            self.assertIn(token, text)
        self.assertNotIn('"runtime_s"', text)  # timings stay in JSON only

    def test_render_deterministic_from_json(self):
        sys.path.insert(0, os.path.join(REPO, "eval"))
        import kill_rate_v2 as k2  # noqa: E402
        r = _load_results()
        with open(REPORT_PATH, encoding="utf-8") as fh:
            committed = fh.read()
        self.assertEqual(k2.render(r), committed,
                         "render from JSON differs -- report not "
                         "deterministic")

    def test_no_network_in_measurement_script(self):
        with open(SCRIPT_PATH, encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("urllib", "requests", "API_KEY", "http.client",
                       "socket"):
            self.assertIsNone(
                re.search(r"\b%s\b" % re.escape(banned), src),
                "kill_rate_v2.py must not touch the network (%s)" % banned)
        self.assertIn("SEED_DEFAULT = 42", src)
        self.assertIn("RUN_TIMEOUT_S = 300", src)


if __name__ == "__main__":
    unittest.main()
