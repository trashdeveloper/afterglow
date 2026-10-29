#!/usr/bin/env python3
"""Task B7: Fault interaction graph (plan item 17, Wave B SEE MORE).

For pair specs (faults-list format, see sim/targets/mut-pair-*.yaml),
records which fault's fix changes the other's symptom (masking order).

Given the pair RED log plus the two single-fault RED logs (the caller
passes log TEXTS -- this module never runs sims, opens no files, and
touches no simulator), determine the fix order: fix fault A first,
does fault B's symptom persist?

Oracle markers: per-check ``CHECK <name>: PASS/FAIL`` lines (the B6
multisig format) plus ``tohost: PASS/FAIL`` and ``TRAP:`` markers.

Interfaces::

  parse_log_markers(text) -> dict with keys:
    checks (name -> "PASS"/"FAIL"), failed (set of failing names),
    tohost ("PASS"/"FAIL"/None), traps ([raw TRAP payloads]),
    has_checks (bool), is_red (bool)
  fault_label(fault, index) -> "fault-<i> <file>:<line>"
  determine_order(pair_spec, pair_log, log_a, log_b) -> dict with keys:
    verdict ("ordered" | "independent" | "unknown"),
    first / second (fault labels or None),
    reason (str, always non-empty),
    evidence ([str, ...] cited marker lines)

Masking rule (never invents order):
  Let FA / FB be the failing CHECK sets of the single-fault logs and
  FP the pair's. onlyA = FA - FB, onlyB = FB - FA are the distinctive
  symptoms. A masks B iff the pair shows A's distinctive symptom
  while B's is absent (fixing A first unmasks B -> order A, B), and
  symmetrically. Both distinctive symptoms present -> independent.
  Anything else (no CHECK lines anywhere, a log not RED, identical
  or subset single symptoms, pair matching neither) -> "unknown"
  with a reason -- order is never guessed.

Default-off contract (Wave B header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py and no caller is wired: agent/loop.py is untouched.

Honesty contract: malformed specs / non-text logs raise ValueError
loudly (caller bug, never a silent guess); insufficient LOGS yield
"unknown" with a reason (a data gap, never an invented order).

Pure stdlib (re); no API, no keys, no network.
"""
import re

_CHECK_RE = re.compile(r"^\s*CHECK\s+([A-Za-z0-9_]+)\s*:\s*(PASS|FAIL)\b",
                       re.MULTILINE)
_TOHOST_RE = re.compile(r"^\s*tohost\s*:\s*(PASS|FAIL)\b", re.MULTILINE)
_TRAP_RE = re.compile(r"^\s*TRAP\s*:\s*(.*?)\s*$", re.MULTILINE)

_VERDICTS = ("ordered", "independent", "unknown")


def parse_log_markers(text):
    """Parse oracle markers from one sim log text -> marker dict.

    CHECK lines: ``CHECK <name>: PASS/FAIL`` (B6 multisig format).
    A check is FAIL if ANY of its lines says FAIL (a recorded failure
    is never talked away by a later PASS line); anything else that
    merely starts with "CHECK" (e.g. "CHECK control: idle-ack-low
    ok") is chatter, not a verdict, and is ignored.
    """
    if not isinstance(text, str):
        raise ValueError(
            "interaction: log text must be a string, got %r" % (type(text),))
    checks = {}
    for match in _CHECK_RE.finditer(text):
        name, verdict = match.group(1), match.group(2)
        if verdict == "FAIL":
            checks[name] = "FAIL"
        else:
            checks.setdefault(name, "PASS")
    tohost = None
    for match in _TOHOST_RE.finditer(text):
        tohost = match.group(1)  # last overall verdict wins
    traps = [m.group(1) for m in _TRAP_RE.finditer(text) if m.group(1)]
    failed = {name for name, verdict in checks.items()
              if verdict == "FAIL"}
    return {"checks": checks, "failed": failed, "tohost": tohost,
            "traps": traps, "has_checks": bool(checks),
            "is_red": bool(failed) or tohost == "FAIL"}


def fault_label(fault, index):
    """Short citable label for one faults-list entry."""
    if not isinstance(fault, dict):
        raise ValueError(
            "interaction: fault #%d must be a dict, got %r"
            % (index, fault))
    path = fault.get("file")
    line = fault.get("line")
    if not isinstance(path, str) or not path.strip():
        raise ValueError(
            "interaction: fault #%d needs a non-empty 'file' "
            "(refusing to label a fault without a location)" % (index,))
    if isinstance(line, bool) or not isinstance(line, int):
        raise ValueError(
            "interaction: fault #%d needs an int 'line', got %r "
            "(refusing to label a fault without a location)"
            % (index, line))
    return "fault-%d %s:%d" % (index, path, line)


def _validate_spec(pair_spec):
    """Extract [label0, label1] from a pair spec (ValueError if not a pair)."""
    if not isinstance(pair_spec, dict) or not isinstance(
            pair_spec.get("faults"), list):
        raise ValueError(
            "interaction: pair_spec must be a dict with a 'faults' list "
            "(faults-list format, see sim/targets/mut-pair-*.yaml)")
    faults = pair_spec["faults"]
    if len(faults) != 2:
        raise ValueError(
            "interaction: masking order needs exactly 2 faults, got %d "
            "(refusing to guess which pair to order)" % (len(faults),))
    return [fault_label(fault, i) for i, fault in enumerate(faults)]


def _unknown(reason, evidence):
    return {"verdict": "unknown", "first": None, "second": None,
            "reason": reason, "evidence": list(evidence)}


def determine_order(pair_spec, pair_log, log_a, log_b):
    """Masking order for a fault pair -> result dict (never invents order).

    pair_spec: faults-list dict (exactly 2 entries). pair_log: the
    pair RED log text. log_a / log_b: single-fault RED log texts for
    faults[0] / faults[1]. All logs are caller-passed strings.
    """
    labels = _validate_spec(pair_spec)
    for tag, log in (("pair", pair_log), ("fault-0 single", log_a),
                     ("fault-1 single", log_b)):
        if not isinstance(log, str):
            raise ValueError(
                "interaction: %s log must be text, got %r "
                "(caller passes log texts, never sim handles)"
                % (tag, type(log)))
    parsed = [parse_log_markers(log) for log in (pair_log, log_a, log_b)]
    pair_m, ma, mb = parsed

    for tag, log, m in (("pair", pair_log, pair_m),
                        ("fault-0 single", log_a, ma),
                        ("fault-1 single", log_b, mb)):
        if not log.strip():
            return _unknown(
                "%s log is empty: no oracle markers to order by "
                "(refusing to invent order)" % (tag,), [])
        if not m["checks"] and m["tohost"] is None and not m["traps"]:
            return _unknown(
                "%s log carries no oracle markers (no CHECK/tohost/TRAP "
                "lines): insufficient log (refusing to invent order)"
                % (tag,), [])

    evidence = [
        "pair %s fails {%s}" % (
            pair_spec.get("mutant_id", "pair"),
            ", ".join(sorted(pair_m["failed"])) or "none"),
        "single %s fails {%s}" % (
            labels[0], ", ".join(sorted(ma["failed"])) or "none"),
        "single %s fails {%s}" % (
            labels[1], ", ".join(sorted(mb["failed"])) or "none"),
    ]

    if not pair_m["is_red"]:
        return _unknown(
            "pair log is not RED (no failing CHECK, no tohost: FAIL): "
            "nothing to order (refusing to invent order)", evidence)
    for tag, m, label in (("fault-0 single", ma, labels[0]),
                          ("fault-1 single", mb, labels[1])):
        if not m["is_red"]:
            return _unknown(
                "%s log for %s is not RED (no failing CHECK, no tohost: "
                "FAIL): masking needs both singles RED "
                "(refusing to invent order)" % (tag, label), evidence)

    if not (pair_m["has_checks"] and ma["has_checks"]
            and mb["has_checks"]):
        return _unknown(
            "logs lack per-check CHECK <name>: PASS/FAIL lines (only "
            "coarse tohost/TRAP markers present): per-fault symptoms "
            "unknowable (refusing to invent order)", evidence)

    fa, fb, fp = ma["failed"], mb["failed"], pair_m["failed"]
    if not fa or not fb:
        missing = labels[0] if not fa else labels[1]
        return _unknown(
            "single-fault log for %s shows no failing CHECK (RED only "
            "via coarse tohost): its per-check symptom is unknown "
            "(refusing to invent order)" % (missing,), evidence)
    if not fp:
        return _unknown(
            "pair log is RED only via coarse tohost (no failing CHECK): "
            "pair per-check symptom unknown "
            "(refusing to invent order)", evidence)

    only_a = fa - fb
    only_b = fb - fa
    if not only_a and not only_b:
        return _unknown(
            "single-fault symptoms identical {%s}: masking "
            "indistinguishable (refusing to invent order)"
            % (", ".join(sorted(fa)),), evidence)
    if not only_a or not only_b:
        subset = labels[1] if not only_b else labels[0]
        return _unknown(
            "%s has no distinctive failing CHECK (its failures are a "
            "subset of the other fault's): masking unknowable "
            "(refusing to invent order)" % (subset,), evidence)

    a_in = bool(only_a & fp)
    b_in = bool(only_b & fp)
    if a_in and not b_in:
        return {"verdict": "ordered", "first": labels[0],
                "second": labels[1],
                "reason": "%s masks %s: pair shows %s-only symptom {%s} "
                          "while %s-only symptom {%s} is absent; fixing %s "
                          "first unmasks %s"
                          % (labels[0], labels[1], labels[0],
                             ", ".join(sorted(only_a)), labels[1],
                             ", ".join(sorted(only_b)), labels[0],
                             labels[1]),
                "evidence": evidence + [
                    "masking: %s-only {%s} persists in pair, %s-only {%s} "
                    "absent" % (labels[0], ", ".join(sorted(only_a)),
                                labels[1], ", ".join(sorted(only_b)))]}
    if b_in and not a_in:
        return {"verdict": "ordered", "first": labels[1],
                "second": labels[0],
                "reason": "%s masks %s: pair shows %s-only symptom {%s} "
                          "while %s-only symptom {%s} is absent; fixing %s "
                          "first unmasks %s"
                          % (labels[1], labels[0], labels[1],
                             ", ".join(sorted(only_b)), labels[0],
                             ", ".join(sorted(only_a)), labels[1],
                             labels[0]),
                "evidence": evidence + [
                    "masking: %s-only {%s} persists in pair, %s-only {%s} "
                    "absent" % (labels[1], ", ".join(sorted(only_b)),
                                labels[0], ", ".join(sorted(only_a)))]}
    if a_in and b_in:
        return {"verdict": "independent", "first": None, "second": None,
                "reason": "independent: both faults' distinctive symptoms "
                          "{%s} and {%s} persist in the pair log; neither "
                          "fix changes the other's symptom"
                          % (", ".join(sorted(only_a)),
                             ", ".join(sorted(only_b))),
                "evidence": evidence + [
                    "independent: %s-only {%s} and %s-only {%s} both "
                    "present in pair" % (
                        labels[0], ", ".join(sorted(only_a)),
                        labels[1], ", ".join(sorted(only_b)))]}
    return _unknown(
        "pair symptom {%s} matches neither fault's distinctive failures "
        "{%s} vs {%s}: masking unknowable (refusing to invent order)"
        % (", ".join(sorted(fp)), ", ".join(sorted(only_a)),
           ", ".join(sorted(only_b))), evidence)
