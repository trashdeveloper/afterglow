"""Task H2 (plan item 82): non-CPU stranger quarantine tests.

stranger-nocpu = a NEW bus fabric/peripheral fault quarantined from all
prior cases: SPI memory-mapped bridge (spimemio) ready-handshake fault
in ``picosoc/spimemio.v`` (picorv32 project, a NON-CPU block -- never
the picorv32.v CPU core, never the simpleuart peripheral):

    anchor: ``assign ready = valid && (addr == rd_addr) && rd_valid;``
    bug:    ``assign ready = valid && (addr == rd_addr) || rd_valid;``

(``||`` for the second ``&&``: ready asserts whenever rd_valid holds,
even on an address mismatch -- a stale-data hit on any non-sequential
access. The new testbench ``sim/tb/picosoc-spimemio_tb.sv`` drives the
valid/ready handshake with a sequential read followed by a jump read.)

STATIC + FIXTURE ONLY: stdlib + repo imports, no network, no Verilator
build, no model calls, no writes to ``eval/``, ``runs/`` or any bundle.
Plan Step 1 (controller authors the stranger design + TB + oracle +
real fault) and Step 3 (live RED/GREEN firing) are NOT delegated and
are NOT claimed here; this file proves the spec is runnable-shaped and
RED-capable in both directions through fixtures:

- test_spec_loads_*: ``sim/targets/stranger-nocpu.yaml`` parses via the
  REAL ``vbackend.load_case`` AND the REAL ``afterglow_core.verilator``
  ``load_case_file``/``load_spec`` path (the same loader
  ``afterglow run --spec`` uses), with a non-blank oracle marker.
- test_pinned_*: the anchor is verbatim in the pinned picorv32 tree
  (``targets.pin`` picorv32 sha ``ef203c2b0a3f``), exactly once
  (line-anchored inject would multi-fire otherwise); the bug line is
  absent from the pristine tree.
- test_red_*: fixture bug-log (``tohost: FAIL`` + ``TRAP:``) decides RED
  and fixture fixed-log (``tohost: PASS``) decides GREEN via the REAL
  ``decide_verdict`` oracle marker -- RED in the fault direction, GREEN
  in the fix direction; the anchor/bug swap is byte-identical both
  directions; the CaseSpec interface mapping carries the oracle through
  (fail closed).
- test_quarantine_*: the file is a block NO prior target uses
  (``picosoc/spimemio.v`` -- all prior picorv32-project targets use
  ``picorv32.v`` or ``picosoc/simpleuart.v``); top/tb/anchor/bug/gate
  differ from EVERY prior ``sim/targets/*.yaml`` with disjoint gate
  tokens; the project/fault differ from ``stranger2``/``stranger3``;
  the anchor and bug lines appear in NO other ``sim/targets/*.yaml``;
  the spec carries no fix-leak key (no ``ground_truth_fix``,
  no ``faults`` block); no loop/guard wiring and no live-API surface
  in this task's files.
- test_verdict_keyword_convention: same static scan as
  ``test_stranger3_quarantine.py`` -- Verdict is only ever built as
  ``Verdict(**{"pass": ..., "evidence": ...})`` and read via
  ``getattr(v, "pass")``.

The new testbench file itself is controller-authored (plan Step 1, NOT
delegated): TB-content assertions that need the real file are marked
to skip until the controller lands it; string-level TB quarantine (the
tb/top names collide with nothing prior) runs unconditionally.

Run: python3 tests/test_stranger_nocpu.py -v   (from repo root)
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

SPEC_PATH = os.path.join(ROOT, "sim", "targets", "stranger-nocpu.yaml")
TB_NEW_NAME = "picosoc-spimemio_tb.sv"
TB_NEW = os.path.join(ROOT, "sim", "tb", TB_NEW_NAME)
TOP_NEW = "tb_picosoc_spimemio"
THIS_FILE = os.path.abspath(__file__)

# The quarantined fault, pinned here exactly as in the spec so drift in
# either direction fails loudly (same convention as DECODE_MUTANT in
# test_fetch_decode_tb.py).
ANCHOR = "assign ready = valid && (addr == rd_addr) && rd_valid;"
BUG = "assign ready = valid && (addr == rd_addr) || rd_valid;"
PIN_SHA = "ef203c2b0a3fb793280f5114941416c425c5b461"

PINNED_TREE = os.path.expanduser(
    "~/.cache/afterglow/work/picorv32-ef203c2b0a3f")
PINNED_RTL = os.path.join(PINNED_TREE, "picosoc", "spimemio.v")

NEEDS_TREE = unittest.skipUnless(
    os.path.isfile(PINNED_RTL),
    "pinned picorv32 tree absent (no network fetch in tests)")
NEEDS_TB = unittest.skipUnless(
    os.path.isfile(TB_NEW),
    "controller-authored TB not landed yet (plan Step 1, NOT delegated)")

# Fixture logs in the exact oracle shape the TB prints: the bug
# direction carries FAIL + TRAP (stale ready on the jump read), the fix
# direction carries PASS. Judged ONLY by the oracle marker via
# decide_verdict (never by exit code).
BUG_LOG = (
    "- tb_picosoc_spimemio seed 42\n"
    "- spimemio handshake: seq read then jump read\n"
    "TRAP: spimemio ready stale hit addr=0x10 rd_addr=0x00\n"
    "TRAP: spimemio jump read got stale data\n"
    "tohost: FAIL 0xdead\n"
    "TRAP: spimemio 2 check(s) failed\n"
    "CYCLES=96\n"
)
FIX_LOG = (
    "- tb_picosoc_spimemio seed 42\n"
    "- spimemio handshake: seq read then jump read\n"
    "- ok seq read addr=0x00 data=0x03020100\n"
    "- ok jump read addr=0x10 data=0x13121110\n"
    "tohost: PASS\n"
    "CYCLES=96\n"
)


def _load():
    """Parse the H2 spec under the REAL board parser (raises loud)."""
    return vbackend.load_case("stranger-nocpu")


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


def _all_targets():
    """Every prior + current target yaml (flat single-fault schema)."""
    targets = os.path.join(ROOT, "sim", "targets")
    out = {}
    for name in sorted(os.listdir(targets)):
        if not name.endswith(".yaml"):
            continue
        stem = name[:-len(".yaml")]
        try:
            out[stem] = vbackend.load_case(stem)
        except (OSError, ValueError, KeyError):
            # Pair specs / line-pinned mutants use a different schema;
            # they still count for string-level quarantine below via a
            # raw-text scan, so skipping the flat parse here is safe.
            continue
    return out


class TestSpecLoads(unittest.TestCase):
    def test_spec_parses_under_real_parser(self):
        case = _load()
        self.assertEqual(case["name"], "stranger-nocpu")

    def test_required_keys_present(self):
        case = _load()
        for req in ("project", "files", "top", "tb", "anchor", "bug",
                    "gate", "oracle_pass"):
            self.assertIn(req, case)

    def test_non_cpu_wiring(self):
        case = _load()
        self.assertEqual(case["project"], "picorv32")
        self.assertEqual(case["files"], ["picosoc/spimemio.v"])
        self.assertEqual(case["top"], TOP_NEW)
        self.assertEqual(case["tb"], TB_NEW_NAME)
        self.assertEqual(case["anchor"].strip(), ANCHOR)
        self.assertEqual(case["bug"].strip(), BUG)
        self.assertTrue(str(case["oracle_pass"]).strip())

    def test_vcore_spec_agrees(self):
        # The same generic loader `afterglow run --spec` uses.
        case = vcore.load_case_file(SPEC_PATH)
        self.assertEqual(case["top"], TOP_NEW)
        self.assertEqual(case["tb"], TB_NEW_NAME)
        self.assertEqual(case["oracle_pass"], "tohost: PASS")
        self.assertEqual(case["project"], "picorv32")
        spec = vcore.load_spec(SPEC_PATH)
        self.assertEqual(spec.case_id, "stranger-nocpu")
        self.assertEqual(spec.oracle_pass, "tohost: PASS")
        # In-tree stem: the loop RED-dispatches sim/sim.py --case <stem>.
        self.assertTrue(os.path.isfile(
            os.path.join(ROOT, "sim", "targets", spec.case_id + ".yaml")))

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
        self.assertEqual(pins["picorv32"][1], PIN_SHA)

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
            case_id="stranger-nocpu",
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

    @NEEDS_TB
    def test_tb_carries_both_oracle_markers(self):
        with open(TB_NEW, encoding="utf-8") as fh:
            tb = fh.read()
        self.assertIn("tohost: PASS", tb)
        self.assertIn("tohost: FAIL", tb)
        self.assertIn("TRAP:", tb)
        self.assertIn("CYCLES=", tb)


class TestQuarantineHolds(unittest.TestCase):
    def test_block_is_new_no_prior_target_uses_this_file(self):
        ours = _load()["files"]
        self.assertEqual(ours, ["picosoc/spimemio.v"])
        for stem, case in _all_targets().items():
            if stem == "stranger-nocpu":
                continue
            self.assertNotIn(
                "picosoc/spimemio.v", list(case.get("files", [])),
                "stranger-nocpu file collides with %s (not quarantined)"
                % stem)

    def test_top_tb_anchor_bug_gate_differ_from_every_prior_target(self):
        ours = _load()
        for stem, case in _all_targets().items():
            if stem == "stranger-nocpu":
                continue
            keys = ["top", "tb", "gate"]
            if "anchor" in case and "bug" in case:
                # Pair specs (mut-pair-*) pin a faults: block instead of
                # one anchor/bug; their raw-text quarantine is covered
                # by test_anchor_bug_unique_across_all_targets.
                keys += ["anchor", "bug"]
            for key in keys:
                self.assertNotEqual(
                    ours[key], case[key],
                    "stranger-nocpu.%s collides with %s (not quarantined)"
                    % (key, stem))

    def test_gate_tokens_disjoint_from_every_prior_gate(self):
        ours = set(str(_load()["gate"]).split())
        for stem, case in _all_targets().items():
            if stem == "stranger-nocpu":
                continue
            other = set(str(case.get("gate", "")).split())
            self.assertEqual(
                ours & other, set(),
                "gate tokens overlap %s: %r" % (stem, ours & other))

    def test_project_and_fault_differ_from_strangers(self):
        ours = _load()
        for stem in ("stranger2", "stranger3"):
            other = vbackend.load_case(stem)
            self.assertNotEqual(ours["project"], other["project"])
            self.assertNotEqual(ours["anchor"].strip(),
                                other["anchor"].strip())
            self.assertNotEqual(ours["bug"].strip(), other["bug"].strip())
            self.assertNotEqual(ours["top"], other["top"])
            self.assertNotEqual(ours["tb"], other["tb"])

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
        self.assertEqual(anchor_hit, ["stranger-nocpu.yaml"],
                         "anchor leaks into other targets: %r" % anchor_hit)
        self.assertEqual(bug_hit, ["stranger-nocpu.yaml"],
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

    def test_no_loop_guard_or_live_api_wiring(self):
        code = _code_tokens(THIS_FILE)
        for pat in (r"\bagent\s*\.\s*loop\b",
                    r"\bagent\s*\.\s*guard\b",
                    r"\bimport\s+requests\b",
                    r"\bimport\s+urllib\b",
                    "MODEL_API_KEY"):
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


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for leaf in (os.path.join(ROOT, "agent", "loop.py"),
                     os.path.join(ROOT, "agent", "guard.py")):
            with open(leaf, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("stranger-nocpu", text,
                             "loop/guard references the new case: %s" % leaf)

    def test_no_live_api(self):
        for path in (SPEC_PATH, THIS_FILE):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            code = _code_tokens(path) if path.endswith(".py") else text
            for pat in (r"\bimport\s+requests\b",
                        r"\bimport\s+urllib\b",
                        "MODEL_API_KEY"):
                self.assertIsNone(
                    re.search(pat, code),
                    "live-API surface in %s: %s"
                    % (os.path.basename(path), pat))


if __name__ == "__main__":
    unittest.main()
