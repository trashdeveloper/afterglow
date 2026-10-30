"""Task G4: Equivalent-mutant filter (plan item 74, Wave G STRONGER REPAIR).

Screens the mutant board with the C3 equivalence check
(``agent/repair/equiv.py::check_equivalence``): a behavior-identical
"mutant" is labeled ``NOT-A-BUG`` (never patched); a behavior-changed
mutant is labeled ``REAL-FAULT``.

Interface (plan text, verbatim)::

  screen(mutant_tree, pristine_tree, case) -> "REAL-FAULT"|"NOT-A-BUG"

``mutant_tree``/``pristine_tree``: RTL tree paths (non-blank strings).
``case``: vbackend case dict (or a registered case-name string, loaded
via vbackend inside equiv). ``master_seed``/``n`` select the
deterministic stimulus (see ``equiv.generate_stimulus``);
``build_fn``/``run_fn`` inject the sim build/run primitives (default:
the real vbackend path -- exactly like equiv itself).

The verdict comes from ONE real C3 equivalence run (mutant vs
pristine under ``case``): identical traces on every stimulus seed mean
``NOT-A-BUG``; any per-seed trace/checksum divergence means
``REAL-FAULT``. The return is a bare label string only -- screen never
patches, never writes, never flips a guard verdict.

Honesty contract (mirrors equiv/tbstrength/falsefix):
- The verdict is never invented: every build/run failure, blank tree,
  bad case, or bad (master_seed, n) propagates loudly from equiv
  (raises; never a fake label over errors).

Default-off contract (Wave G header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib plus the existing agent/repair/equiv.py check; no API, no
keys, no network, no I/O.
"""

from agent.repair import equiv

REAL_FAULT = "REAL-FAULT"
NOT_A_BUG = "NOT-A-BUG"

DEFAULT_MASTER_SEED = 1234
DEFAULT_N = equiv.DEFAULT_N


def screen(mutant_tree, pristine_tree, case,
           master_seed=DEFAULT_MASTER_SEED, n=DEFAULT_N,
           build_fn=None, run_fn=None):
    """Screen one mutant tree: REAL-FAULT or NOT-A-BUG.

    Runs ``equiv.check_equivalence(mutant_tree, pristine_tree, case,
    ...)`` and maps ``equivalent True`` -> ``"NOT-A-BUG"`` (identical
    traces: not a bug, never patched) and ``equivalent False`` ->
    ``"REAL-FAULT"`` (behavior changed). Every failure mode raises
    loudly (never an invented label).
    """
    verdict = equiv.check_equivalence(
        mutant_tree, pristine_tree, case,
        master_seed=master_seed, n=n,
        build_fn=build_fn, run_fn=run_fn)
    if verdict.get("equivalent"):
        return NOT_A_BUG
    return REAL_FAULT
