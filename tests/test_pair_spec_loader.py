"""Pair-spec loader tests (TDD red-first, pure code, no sim runs).

Background: sim/targets/mut-pair-*.yaml carry a nested ``faults:`` list,
but afterglow_core/verilator.py load_case_file was a FLAT key:value parser:
it stripped indentation and split on ':', silently mangling nested faults
into garbage keys (``- file``, ``line`` ...) with no ``faults`` list
surviving -- so agent/loop.py is_mutant_case returned False and RED fell
through to the single-fault sim path.

Covers:
- nested round-trip: loading mut-pair-01.yaml equals the hand-built dict
- mangle-proof: no garbage dash-keys in parsed output
- flat-spec regression: non-pair specs parse byte-identically to the
  legacy flat parser
- refusal: vbackend inject/run_case REFUSE pair stems loudly (same style
  as the existing mut-pilot refusal); RED for pairs may ONLY go through
  loop prepare_mutant_tree
- bad nesting: any nested/dash structure the loader does not understand
  fails LOUDLY (ValueError), never silently mangled

Run: python3 -m unittest tests.test_pair_spec_loader -v  (from repo root)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import verilator as av  # noqa: E402
import vbackend  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = os.path.join(ROOT, "sim", "targets")

PAIR_01 = os.path.join(TARGETS, "mut-pair-01.yaml")


def _legacy_flat_parse(path):
    """Byte-exact replica of the pre-fix flat parser (for regression)."""
    def _unquote(v):
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            return v[1:-1]
        return v

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    case = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError("bad yaml line in %s: %r" % (path, raw))
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items = []
            if inner:
                items = [_unquote(x) for x in inner.split(",")]
            case[key] = items
        else:
            case[key] = _unquote(val)
    for req in ("project", "files", "top", "tb", "anchor", "bug", "gate",
                "oracle_pass"):
        if req not in case:
            raise KeyError("spec %s missing key %s" % (path, req))
    if isinstance(case["files"], str):
        case["files"] = [case["files"]]
    case.setdefault("oracle_trap", "TRAP:")
    case.setdefault("vflags", [])
    if isinstance(case["vflags"], str):
        case["vflags"] = [case["vflags"]] if case["vflags"] else []
    stem = os.path.splitext(os.path.basename(path))[0]
    case["name"] = stem
    case["spec_path"] = os.path.abspath(path)
    return case


def _handbuilt_pair_01():
    return {
        "project": "picorv32",
        "files": ["picosoc/simpleuart.v"],
        "top": "tb_picosoc_uart",
        "tb": "picosoc-uart-div_tb.sv",
        "vflags": ["-Wno-WIDTH"],
        "gate": "dual-fault pilot simpleuart pair",
        "oracle_pass": "tohost: PASS",
        "oracle_trap": "TRAP:",
        "mutant_id": "mut-pair-01",
        "mutant_seed": "1234",
        "mutant_op": "pair",
        "faults": [
            {"file": "picosoc/simpleuart.v", "line": 39,
             "anchor": "reg [3:0] recv_state;",
             "bug": "reg [3:1] recv_state;"},
            {"file": "picosoc/simpleuart.v", "line": 114,
             "anchor": "send_pattern <= ~0;",
             "bug": "send_pattern <= ~1;"},
        ],
        "parent_case": "picosoc-uart-div",
        "name": "mut-pair-01",
        "spec_path": os.path.abspath(PAIR_01),
    }


class TestPairNestedParse(unittest.TestCase):
    def test_pair_01_roundtrip_equals_handbuilt(self):
        self.assertEqual(av.load_case_file(PAIR_01), _handbuilt_pair_01())

    def test_pair_01_fault_lines_are_int(self):
        case = av.load_case_file(PAIR_01)
        for f in case["faults"]:
            self.assertIsInstance(f["line"], int)

    def test_no_garbage_dash_keys(self):
        case = av.load_case_file(PAIR_01)
        for key in case:
            self.assertFalse(key.startswith("-"),
                             "mangled dash-key survived: %r" % key)
            self.assertNotEqual(key.strip(), "file")
        self.assertNotIn("line", case,
                         "nested fault 'line' leaked to top level")
        self.assertIsInstance(case["faults"], list)
        self.assertEqual(len(case["faults"]), 2)

    def test_all_pair_specs_load_with_two_intlined_faults(self):
        for stem in ("mut-pair-02", "mut-pair-03",
                     "mut-pair-04", "mut-pair-05"):
            path = os.path.join(TARGETS, stem + ".yaml")
            case = av.load_case_file(path)
            self.assertIsInstance(case["faults"], list, stem)
            self.assertEqual(len(case["faults"]), 2, stem)
            for f in case["faults"]:
                self.assertEqual(set(f), {"file", "line", "anchor",
                                          "bug"}, stem)
                self.assertIsInstance(f["line"], int, stem)
                self.assertTrue(f["file"].strip(), stem)
                self.assertTrue(f["anchor"].strip(), stem)
                self.assertTrue(f["bug"].strip(), stem)


class TestFlatRegression(unittest.TestCase):
    def test_flat_specs_byte_identical_to_legacy(self):
        stems = sorted(
            os.path.splitext(f)[0] for f in os.listdir(TARGETS)
            if f.endswith(".yaml") and not f.startswith("mut-pair-"))
        self.assertGreater(len(stems), 10)
        for stem in stems:
            path = os.path.join(TARGETS, stem + ".yaml")
            self.assertEqual(av.load_case_file(path),
                             _legacy_flat_parse(path), stem)


class TestBadNestingLoud(unittest.TestCase):
    def _write(self, tmp, body):
        path = os.path.join(tmp, "bad.yaml")
        with open(path, "w") as fh:
            fh.write(body)
        return path

    def test_unknown_nested_block_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, 'project: "p"\nfoo:\n'
                                    '  bar: "x"\n')
            with self.assertRaises(ValueError):
                av.load_case_file(path)

    def test_dash_item_outside_faults_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, 'project: "p"\n'
                                    '  - file: "x"\n')
            with self.assertRaises(ValueError):
                av.load_case_file(path)

    def test_indented_key_outside_faults_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, 'project: "p"\n'
                                    '    line: 39\n')
            with self.assertRaises(ValueError):
                av.load_case_file(path)

    def test_fault_missing_field_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp, 'project: "p"\n'
                     'faults:\n'
                     '  - file: "f.v"\n'
                     '    line: 39\n'
                     '    anchor: "a;"\n')
            with self.assertRaises(ValueError):
                av.load_case_file(path)

    def test_fault_non_integer_line_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp, 'project: "p"\n'
                     'faults:\n'
                     '  - file: "f.v"\n'
                     '    line: abc\n'
                     '    anchor: "a;"\n'
                     '    bug: "b;"\n')
            with self.assertRaises(ValueError):
                av.load_case_file(path)

    def test_fault_unknown_subkey_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp, 'project: "p"\n'
                     'faults:\n'
                     '  - file: "f.v"\n'
                     '    line: 39\n'
                     '    anchor: "a;"\n'
                     '    bug: "b;"\n'
                     '    seed: 1\n')
            with self.assertRaises(ValueError):
                av.load_case_file(path)


class TestPairStemRefusal(unittest.TestCase):
    def test_vbackend_load_case_sees_faults_list(self):
        case = vbackend.load_case("mut-pair-01")
        self.assertIsInstance(case["faults"], list)
        self.assertEqual(len(case["faults"]), 2)
        self.assertIsInstance(case["faults"][0]["line"], int)

    def test_vbackend_inject_refuses_pair_stem_loudly(self):
        case = vbackend.load_case("mut-pair-01")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as ctx:
                vbackend.inject(tmp, case)
        self.assertIn("loop", str(ctx.exception).lower())

    def test_vbackend_run_case_refuses_pair_stem_loudly(self):
        with self.assertRaises(RuntimeError) as ctx:
            vbackend.run_case("mut-pair-01", seed=42,
                              boot="/tmp/nope-pair.log",
                              verdict="/tmp/nope-pair.json")
        self.assertIn("loop", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
