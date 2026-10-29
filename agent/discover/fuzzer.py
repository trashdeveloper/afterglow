#!/usr/bin/env python3
"""Task A1: input fuzzer (plan item 1, Wave A FIND BUGS).

Random + structured stimulus mutations over TB firmware / `+args`;
`run_corpus` loops them over the picorv32 fetch TB (or any case dict
with the same oracle contract).

Interfaces (plan shared):
  StimulusCorpus(seed, n=200, size=16) -> list[bytes]
  run_corpus(tree, case, corpus, build_fn=None, run_fn=None) -> list[Verdict]

Honesty contract (review focus: "a fuzzer that never finds anything
must say so loudly, not fake a RED"):
  - Empty corpus raises ValueError. There is no verdict to report from
    zero stimuli, so none is invented.
  - Any build/run error propagates loudly (never converted into a fake
    RED or PASS verdict). A sim that RUNS but prints no oracle marker
    is a genuine RED per the oracle contract (decide_verdict), not an
    error path: only exceptions are loud.
  - Discovery writes eval/discovery_fuzzer.md reports (counts only),
    never verdicts.

Stimulus encoding: each stimulus is `size` bytes. Bytes 0..3 are the
little-endian `+seed=` value passed to the TB; the remaining bytes are
opaque firmware-mutation material (reserved for TBs that consume
`+stimulus=<hex>`; the fetch TB logs the seed only).
"""

import os
import random
import subprocess
import sys

_DISCOVER_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_DISCOVER_DIR)
_ROOT = os.path.dirname(_AGENT_DIR)
sys.path.insert(0, os.path.join(_ROOT, "sim"))
sys.path.insert(0, _ROOT)

import vbackend  # noqa: E402
from afterglow_core.types import decide_verdict  # noqa: E402

STIMULUS_SIZE = 16
DEFAULT_CORPUS_SIZE = 200

# Structured edge vectors first (deterministic, seed-independent):
# blank firmware, stuck-at firmware, alternating buses, misaligned
# low-bits (0b10, the fetch-misalign shape), single-bit corners.
STRUCTURED_VECTORS = [
    b"\x00" * STIMULUS_SIZE,
    b"\xff" * STIMULUS_SIZE,
    b"\xaa" * STIMULUS_SIZE,
    b"\x55" * STIMULUS_SIZE,
    b"\x02" * STIMULUS_SIZE,
    b"\x01" + b"\x00" * (STIMULUS_SIZE - 1),
    b"\x00" * (STIMULUS_SIZE - 1) + b"\x80",
    b"\x02\x00\x02\x00" * (STIMULUS_SIZE // 4),
]


def StimulusCorpus(seed, n=DEFAULT_CORPUS_SIZE, size=STIMULUS_SIZE):
    """Deterministic stimulus list: identical seeds reproduce identically.

    First the pinned STRUCTURED_VECTORS, then random.Random(seed)
    bytes. Same seed -> byte-identical list, every time.
    """
    if n < 0:
        raise ValueError("corpus size n must be >= 0, got %r" % (n,))
    if size <= 0:
        raise ValueError("stimulus size must be > 0, got %r" % (size,))
    rng = random.Random(seed)
    corpus = [bytes(v[:size].ljust(size, b"\x00")) for v in STRUCTURED_VECTORS]
    while len(corpus) < n:
        corpus.append(rng.getrandbits(size * 8).to_bytes(size, "little"))
    return corpus[:n]


def stimulus_to_seed(stimulus):
    """`+seed=` value for one stimulus: little-endian bytes 0..3."""
    if not isinstance(stimulus, (bytes, bytearray)) or len(stimulus) < 4:
        raise ValueError("stimulus must be bytes of length >= 4, got %r"
                         % (stimulus,))
    return int.from_bytes(bytes(stimulus[:4]), "little")


def _default_build(tree, case):
    return vbackend.build(tree, case)


def _default_run(binary, seed, stimulus):
    del stimulus  # reserved: TBs with +stimulus=<hex> consume it later
    proc = subprocess.run(
        [str(binary), "+seed=%d" % seed],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=300)
    # NOTE: exit code is deliberately ignored here: the verdict comes
    # from the oracle marker in the log (afterglow_core.types contract).
    # A crash/timeout raises (TimeoutExpired propagates); a run that
    # finishes without the marker is a genuine RED, not an error.
    return proc.stdout


def run_corpus(tree, case, corpus, build_fn=None, run_fn=None):
    """Run one sim per stimulus; return one Verdict per stimulus.

    tree: RTL tree path. case: vbackend case dict (or case-name string,
      loaded via vbackend.load_case). corpus: non-empty list[bytes].
    Empty corpus raises ValueError; build/run exceptions propagate --
    neither is ever converted into a fake verdict.
    """
    corpus = list(corpus)
    if not corpus:
        raise ValueError(
            "run_corpus refused empty corpus: zero stimuli produce zero "
            "verdicts; refusing to invent a RED (or a PASS) from nothing.")
    if isinstance(case, str):
        case = vbackend.load_case(case)
    try:
        oracle = case["oracle_pass"]
    except (KeyError, TypeError):
        raise KeyError("case is missing required key 'oracle_pass'")
    if not str(oracle).strip():
        raise ValueError("case oracle_pass is blank; verdict would be "
                         "meaningless -- refusing to run.")
    build = build_fn or _default_build
    run = run_fn or _default_run
    binary = build(tree, case)  # raises loudly on build failure
    verdicts = []
    for stimulus in corpus:
        seed = stimulus_to_seed(stimulus)  # raises loudly on bad stimulus
        log = run(binary, seed, stimulus)  # raises loudly on sim error
        if not isinstance(log, str):
            raise TypeError("run_fn must return sim log text, got %r"
                            % type(log))
        verdict = decide_verdict(log, oracle)
        verdict.evidence.update({
            "case": case.get("name", "?"),
            "seed": seed,
            "stimulus_hex": bytes(stimulus).hex(),
        })
        verdicts.append(verdict)
    return verdicts


def summarize_verdicts(verdicts):
    """Count REDs/PASSes. Zero hits is reported as zero, never hidden."""
    reds = sum(1 for v in verdicts if not getattr(v, "pass"))
    return {"n": len(verdicts), "red": reds, "green": len(verdicts) - reds}


def write_discovery_report(path, case_name, seed, verdicts):
    """Write eval/discovery_fuzzer.md-style report: counts only, loud on zero.

    Discovery reports NEVER carry verdicts; they count them. A zero-hit
    corpus says so explicitly instead of faking signal.
    """
    summary = summarize_verdicts(verdicts)
    lines = [
        "# Discovery: input fuzzer (Task A1)",
        "",
        "- case: %s" % case_name,
        "- corpus seed: %s" % seed,
        "- stimuli fired: %d" % summary["n"],
        "- RED hits: %d" % summary["red"],
        "- GREEN: %d" % summary["green"],
        "",
    ]
    if summary["red"] == 0:
        lines.append("Zero RED hits: the fuzzer found nothing on this "
                     "tree/case. No verdict invented; corpus + seeds "
                     "above reproduce this exact run.")
    else:
        lines.append("RED-triggering seeds (re-fire with +seed=<n>):")
        for v in verdicts:
            if not getattr(v, "pass"):
                lines.append("- seed %s stim %s"
                             % (v.evidence.get("seed"),
                                v.evidence.get("stimulus_hex")))
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
