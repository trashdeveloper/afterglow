"""Task H1 (plan item 81): stranger3 quarantine tests.

stranger3 = a NEW SERV fault/TB pair quarantined from all prior cases:
SERV compare-mux inversion in rtl/serv_alu.v
(``assign o_cmp = i_cmp_eq ? result_eq : result_lt;`` with the two
branches swapped), driven by the new testbench
``sim/tb/serv-cmp-mux_tb.sv``. Same SERV target family as the
``serv-alu-lut`` precedent (existing design precedent), but a different
fault line, different TB, different top, different gate.

STATIC + FIXTURE ONLY: stdlib + repo imports, no network, no Verilator
build, no model calls, no writes to ``eval/``, ``runs/`` or any bundle.
The live RED/GREEN firing is controller-run (plan Step 3, NOT delegated);
this file proves the spec is runnable-shaped and RED-capable in both
directions through fixtures:

- test_spec_loads_*: ``sim/targets/stranger3.yaml`` parses via the REAL
  ``vbackend.load_case`` AND the REAL ``afterglow_core.verilator``
  ``load_case_file``/``load_spec`` path (the same loader
  ``afterglow run --spec`` uses), with a non-blank oracle marker.
- test_pinned_*: the anchor is verbatim in the pinned SERV tree
  (``targets.pin`` serv sha ``727bb40a8784``), exactly once
  (line-anchored inject would multi-fire otherwise); the bug line is
  absent from the pristine tree.
- test_red_*: fixture bug-log (``tohost: FAIL`` + ``TRAP:``) decides RED
  and fixture fixed-log (``tohost: PASS``) decides GREEN via the REAL
  ``decide_verdict`` oracle marker -- RED in the fault direction, GREEN
  in the fix direction; the anchor/bug swap is byte-identical both
  directions; the CaseSpec interface mapping carries the oracle through
  (fail closed).
- test_quarantine_*: top/tb/anchor/bug/gate all differ from the prior
  SERV case (``serv-alu-lut``) with disjoint gate tokens; project/fault
  differ from ``stranger2``; the anchor and bug lines appear in NO other
  ``sim/targets/*.yaml``; the spec carries no fix-leak key (no
  ``ground_truth_fix``, no ``faults`` block); the new TB is byte-distinct
  from ``serv-alu-lut_tb.sv`` and observes ``o_cmp``/``i_cmp_eq`` (not the
  old BOOL_LUT rows); no loop/guard wiring and no live-API surface in
  this task's files.
- test_verdict_keyword_convention: same static scan as
  ``test_stranger2_score.py`` -- Verdict is only ever built as
  ``Verdict(**{"pass": ..., "evidence": ...})`` and read via
  ``getattr(v, "pass")``.

Run: python3 tests/test_stranger3_quarantine.py -v   (from repo root)
"""
import os
import re
import sys
import tokenize
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "sim"))

import vbackend  # noqa: E402
from afterglow_core import verilator as vcore  # noqa: E402
from afterglow_core.types import CaseSpec, Verdict, decide_verdict  # noqa: E402

SPEC_PATH = os.path.join(ROOT, "sim", "targets", "stranger3.yaml")
TB_NEW = os.path.join(ROOT, "sim", "tb", "serv-cmp-mux_tb.sv")
TB_OLD = os.path.join(ROOT, "sim", "tb", "serv-alu-lut_tb.sv")
THIS_FILE = os.path.abspath(__file__)

# The quarantined fault, pinned here exactly as in the spec so drift in
# either direction fails loudly (same convention as DECODE_MUTANT in
# test_fetch_decode_tb.py).
ANCHOR = "assign o_cmp = i_cmp_eq ? result_eq : result_lt;"
BUG = "assign o_cmp = i_cmp_eq ? result_lt : result_eq;"
PIN_SHA = "727bb40a8784c50b081abdcfd784a5bc7c34e40e"

PINNED_TREE = os.path.expanduser(
    "~/.cache/afterglow/work/serv-727bb40a8784")
PINNED_RTL = os.path.join(PINNED_TREE, "rtl", "serv_alu.v")

NEEDS_TREE = unittest.skipUnless(
    os.path.isfile(PINNED_RTL),
    "pinned serv tree absent (no network fetch in tests)")

# Fixture logs in the exact oracle shape the new TB prints: the bug
# direction carries FAIL + TRAP (mux branches swapped, every eq/lt check
# flips), the fix direction carries PASS. Judged ONLY by the oracle
# marker via decide_verdict (never by exit code).
BUG_LOG = (
    "- tb_serv_cmp seed 42\n"
    "- serv cmp mux: eq selects result_eq\n"
    "TRAP: cmp mux mismatch eq=1 rs1=1 op_b=0 got=1 want=0\n"
    "TRAP: cmp mux mismatch eq=1 rs1=0 op_b=1 got=1 want=0\n"
    "TRAP: cmp mux mismatch eq=0 rs1=1 op_b=0 got=0 want=1\n"
    "TRAP: cmp mux mismatch eq=0 rs1=0 op_b=1 got=0 want=1\n"
    "tohost: FAIL 0xdead\n"
    "TRAP: serv cmp mux 4 check(s) failed\n"
    "CYCLES=16\n"
)
FIX_LOG = (
    "- tb_serv_cmp seed 42\n"
    "- serv cmp mux: eq selects result_eq\n"
    "- ok eq=1 rs1=1 op_b=0 cmp=0\n"
    "- ok eq=1 rs1=0 op_b=1 cmp=0\n"
    "- ok eq=0 rs1=1 op_b=0 cmp=1\n"
    "- ok eq=0 rs1=0 op_b=1 cmp=1\n"
    "tohost: PASS\n"
    "CYCLES=16\n"
)


def _load():
    """Parse the H1 spec under the REAL board parser (raises loud)."""
    return vbackend.load_case("stranger3")


def _swap(text, frm, to):
    """Line-anchored swap mirroring vbackend.inject's matching rule
    (stripped line equals stripped anchor -> re-emit with indent + bug),
    on a temp fixture string -- never touches a real tree."""
    hits = 0
    out = []
    for ln in text.splitlines(keepends=True):
        body = ln.rstrip("\n")
        if body.strip() == frm.strip():
            indent = body[:len(body) - len(body.lstrip())]
            out.append("%s%s\n" % (indent, to.strip()))
            hits += 1
        else:
            out.append(ln)
    return "".join(out), hits


def _code_tokens(path):
    """Source tokens minus comments/strings (repo convention: assertion
    prose must never trip a source scan)."""
    with open(path, "rb") as fh:
        toks = list(tokenize.tokenize(fh.readline))
    return " ".join(
        t.string
        for t in toks
        if t.type not in (
            tokenize.COMMENT,
            tokenize.STRING,
            tokenize.FSTRING_START,
            tokenize.FSTRING_MIDDLE,
            tokenize.FSTRING_END,
            tokenize.ENCODING,
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
        )
    )


class TestSpecLoads(unittest.TestCase):
    def test_spec_parses_under_real_parser(self):
        case = _load()
        self.assertEqual(case["name"], "stranger3")

    def test_required_keys_present(self):
        case = _load()
        for req in ("project", "files", "top", "tb", "anchor", "bug",
                    "gate", "oracle_pass"):
            self.assertIn(req, case)

    def test_serv_family_wiring(self):
        case = _load()
        self.assertEqual(case["project"], "serv")
        self.assertEqual(case["files"], ["rtl/serv_alu.v"])
        self.assertEqual(case["top"], "tb_serv_cmp")
        self.assertEqual(case["tb"], "serv-cmp-mux_tb.sv")
        self.assertEqual(case["anchor"].strip(), ANCHOR)
        self.assertEqual(case["bug"].strip(), BUG)
        self.assertTrue(str(case["oracle_pass"]).strip())

    def test_vcore_spec_agrees(self):
        # The same generic loader `afterglow run --spec` uses.
        case = vcore.load_case_file(SPEC_PATH)
        self.assertEqual(case["top"], "tb_serv_cmp")
        self.assertEqual(case["tb"], "serv-cmp-mux_tb.sv")
        self.assertEqual(case["oracle_pass"], "tohost: PASS")
        self.assertEqual(case["project"], "serv")
        spec = vcore.load_spec(SPEC_PATH)
        self.assertEqual(spec.case_id, "stranger3")
        self.assertEqual(spec.oracle_pass, "tohost: PASS")
        # In-tree stem: the loop RED-dispatches sim/sim.py --case <stem>.
        self.assertTrue(os.path.isfile(
            os.path.join(ROOT, "sim", "targets", spec.case_id + ".yaml")))

    def test_tb_file_matches_spec(self):
        case = _load()
        self.assertTrue(os.path.isfile(
            os.path.join(ROOT, "sim", "tb", case["tb"])))

    def test_project_is_pinned(self):
        case = _load()
        pins = {}
        with open(os.path.join(ROOT, "targets.pin")) as fh:
            pin_text = fh.read()
        for raw in pin_text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            pins[parts[0]] = parts[1:]
        self.assertIn(case["project"], pins)
        self.assertEqual(pins["serv"][1], PIN_SHA)

    def test_budget_positive_int(self):
        case = _load()
        self.assertGreater(int(case.get("budget_s", 600)), 0)

    def test_oracle_fail_closed_nonblank(self):
        case = _load()
        self.assertTrue(str(case["oracle_pass"]).strip())


class TestFaultPinned(unittest.TestCase):
    @NEEDS_TREE
    def test_anchor_verbatim_once_in_pinned_tree(self):
        with open(PINNED_RTL, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        hits = [n for n, ln in enumerate(lines, 1)
                if ln.strip() == ANCHOR]
        self.assertEqual(len(hits), 1,
                         "anchor must fire exactly once (line-anchored "
                         "inject would multi-fire otherwise): %r" % hits)

    @NEEDS_TREE
    def test_bug_absent_from_pristine_tree(self):
        with open(PINNED_RTL, encoding="utf-8") as fh:
            text = fh.read()
        stripped = [ln.strip() for ln in text.splitlines()]
        self.assertNotIn(BUG, stripped)


class TestBothDirections(unittest.TestCase):
    def test_anchor_and_bug_differ_and_nonblank(self):
        case = _load()
        self.assertTrue(str(case["anchor"]).strip())
        self.assertTrue(str(case["bug"]).strip())
        self.assertNotEqual(str(case["anchor"]).strip(),
                            str(case["bug"]).strip())

    def test_red_direction_oracle_absent(self):
        case = _load()
        oracle = str(case["oracle_pass"])
        self.assertNotIn(oracle, BUG_LOG)
        verdict = decide_verdict(BUG_LOG, oracle)
        self.assertIs(getattr(verdict, "pass"), False)
        self.assertIn("tohost: FAIL", BUG_LOG)
        self.assertIn(str(case.get("oracle_trap", "TRAP:")), BUG_LOG)

    def test_green_direction_oracle_present(self):
        case = _load()
        oracle = str(case["oracle_pass"])
        self.assertIn(oracle, FIX_LOG)
        verdict = decide_verdict(FIX_LOG, oracle)
        self.assertIs(getattr(verdict, "pass"), True)

    def test_explicit_verdict_kwargs_roundtrip(self):
        case = _load()
        oracle = str(case["oracle_pass"])
        expected = Verdict(
            **{"pass": True,
               "evidence": {"oracle_pass": oracle, "matched": True}})
        self.assertEqual(decide_verdict(FIX_LOG, oracle), expected)

    def test_inject_swap_both_directions_byte_identical(self):
        case = _load()
        anchor = str(case["anchor"])
        bug = str(case["bug"])
        fixture = ("module m;\n"
                   "    %s\n"
                   "    wire x = 1;\n" % anchor.strip())
        injected, hits = _swap(fixture, anchor, bug)
        self.assertEqual(hits, 1)
        self.assertIn(bug.strip(), injected)
        self.assertNotIn(anchor.strip(), injected)
        restored, rhits = _swap(injected, bug, anchor)
        self.assertEqual(rhits, 1)
        self.assertEqual(restored, fixture)

    def test_casespec_interface_mapping(self):
        case = _load()
        with open(
                os.path.join(ROOT, "agent", "allowlist.txt")) as fh:
            allow_text = fh.read()
        allowlist = [
            ln.strip()
            for ln in allow_text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        spec = CaseSpec(
            case_id="stranger3",
            project=str(case["project"]),
            files=list(case["files"]),
            oracle_pass=str(case["oracle_pass"]),
            oracle_trap=str(case.get("oracle_trap", "TRAP:")),
            budget_s=int(case.get("budget_s", 600)),
            allowlist=allowlist,
            lint_cmd="",
        )
        self.assertEqual(
            sorted(vars(spec)),
            sorted(["case_id", "project", "files", "oracle_pass",
                    "oracle_trap", "budget_s", "allowlist", "lint_cmd"]))
        self.assertEqual(spec.oracle_pass, str(case["oracle_pass"]))
        self.assertTrue(spec.oracle_pass.strip())

    def test_missing_stranger_stem_fails_loud(self):
        with self.assertRaises(OSError):
            vbackend.load_case("stranger-does-not-exist")

    def test_tb_carries_both_oracle_markers(self):
        with open(TB_NEW, encoding="utf-8") as fh:
            tb = fh.read()
        self.assertIn("tohost: PASS", tb)
        self.assertIn("tohost: FAIL", tb)
        self.assertIn("TRAP:", tb)
        self.assertIn("CYCLES=", tb)


class TestQuarantineHolds(unittest.TestCase):
    def test_top_tb_gate_differ_from_prior_serv_case(self):
        new = _load()
        old = vbackend.load_case("serv-alu-lut")
        for key in ("top", "tb", "anchor", "bug", "gate"):
            self.assertNotEqual(new[key], old[key],
                                "stranger3.%s collides with serv-alu-lut "
                                "(not quarantined)" % key)
        # Shared SERV family file is allowed; the fault/TB pair is new.
        self.assertEqual(new["files"], old["files"])
        new_toks = set(str(new["gate"]).split())
        old_toks = set(str(old["gate"]).split())
        self.assertEqual(new_toks & old_toks, set(),
                         "gate tokens overlap serv-alu-lut: %r"
                         % (new_toks & old_toks,))

    def test_project_and_fault_differ_from_stranger2(self):
        new = _load()
        s2 = vbackend.load_case("stranger2")
        self.assertNotEqual(new["project"], s2["project"])
        self.assertNotEqual(new["anchor"].strip(), s2["anchor"].strip())
        self.assertNotEqual(new["bug"].strip(), s2["bug"].strip())
        self.assertNotEqual(new["top"], s2["top"])
        self.assertNotEqual(new["tb"], s2["tb"])

    def test_anchor_bug_unique_across_all_targets(self):
        anchor_hit, bug_hit = [], []
        targets = os.path.join(ROOT, "sim", "targets")
        for name in sorted(os.listdir(targets)):
            if not name.endswith(".yaml"):
                continue
            with open(os.path.join(targets, name), encoding="utf-8",
                      errors="replace") as fh:
                text = fh.read()
            if ANCHOR in text:
                anchor_hit.append(name)
            if BUG in text:
                bug_hit.append(name)
        self.assertEqual(anchor_hit, ["stranger3.yaml"],
                         "anchor leaks into other targets: %r" % anchor_hit)
        self.assertEqual(bug_hit, ["stranger3.yaml"],
                         "bug leaks into other targets: %r" % bug_hit)

    def test_yaml_leaks_no_ground_truth(self):
        with open(SPEC_PATH, "r", encoding="utf-8") as fh:
            text = fh.read()
        for forbidden in ("ground_truth_fix", "ground-truth",
                          "manual_baseline", "fix_commit", "solution",
                          "MODEL_API_KEY"):
            self.assertNotIn(forbidden, text)
        case = _load()
        self.assertNotIn("faults", case)
        for key in case:
            self.assertNotIn("fix", key.lower())

    def test_tb_is_new_and_observes_the_new_fault(self):
        with open(TB_NEW, encoding="utf-8") as fh:
            new = fh.read()
        with open(TB_OLD, encoding="utf-8") as fh:
            old = fh.read()
        self.assertNotEqual(new, old)
        self.assertIn("module tb_serv_cmp", new)
        self.assertNotIn("tb_serv_alu", new)
        # The TB must observe the new fault line's signal (o_cmp under
        # i_cmp_eq), not the old BOOL_LUT rows: strip // comments, then
        # the LUT name must be gone and i_bool_op must stay pinned at
        # its declaration init (the port connection is required; driving
        # it is what would couple this TB to the old fault).
        code = "\n".join(
            ln.split("//", 1)[0] for ln in new.splitlines())
        self.assertIn("o_cmp", code)
        self.assertIn("i_cmp_eq", code)
        self.assertNotIn("BOOL_LUT", code)
        self.assertEqual(len(re.findall(r"i_bool_op\s*=", code)), 1)

    def test_no_loop_guard_or_live_api_wiring(self):
        code = _code_tokens(THIS_FILE)
        for pat in (r"\bagent\s*\.\s*loop\b",
                    r"\bagent\s*\.\s*guard\b",
                    r"\bimport\s+requests\b",
                    r"\bimport\s+urllib\b",
                    r"MODEL_API_KEY"):
            self.assertIsNone(
                re.search(pat, code),
                "wiring/live-API hit in quarantine gate: %s" % pat)

    def test_verdict_keyword_convention(self):
        code = _code_tokens(THIS_FILE)
        self.assertIsNone(
            re.search(r"Verdict\s*\((?!\s*\*)", code),
            "positional Verdict(...) construction found",
        )
        self.assertIsNone(
            re.search(r"\.\s*pass\b", code),
            "attribute-style .pass access found",
        )


if __name__ == "__main__":
    unittest.main()
