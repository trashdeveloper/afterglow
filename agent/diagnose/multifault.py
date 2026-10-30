#!/usr/bin/env python3
"""Task F1: Multi-fault localizer (plan item 61).

Union of per-signal suspect sets, ranked by occurrence count
then cone overlap.

Interface::

  localize(fault_signals, cones) -> [line, ...]

``fault_signals`` maps a signal name to the iterable of suspect
lines for that signal (e.g. ``{"sigA": ["uart.v:42", "uart.v:10"],
"sigB": ["uart.v:42", "uart.v:99"]}``). ``cones`` maps a cone id
to the iterable of lines in that cone (may be ``{}`` or ``None``
when cone data is unavailable).

Ranking: occurrence count (number of per-signal suspect sets
containing the line) descending, then cone overlap (number of
cones containing the line) descending, then the line string
ascending for determinism. The head of the list is the most
shared suspect (e.g. a line blamed by two signals outranks a
line blamed by one).

Errors are loud: a non-mapping ``fault_signals``/``cones``, or a
suspect/cone entry that is a string or non-iterable, raises
TypeError. Empty ``fault_signals`` returns [].

Default-off contract: this module runs ONLY on explicit call. It
imports nothing from agent/loop.py and no caller is wired:
agent/loop.py is untouched.

Pure stdlib (no imports at all); no API, no keys, no network, no I/O.
"""

_VERSION = "1"


def localize(fault_signals, cones=None):
    """Rank the union of per-signal suspect sets.

    Returns lines sorted by (-occurrence, -cone_overlap, line).
    """
    if not isinstance(fault_signals, dict):
        raise TypeError(
            "multifault: fault_signals must be a dict, got %r"
            % (type(fault_signals),))
    if cones is None:
        cones = {}
    if not isinstance(cones, dict):
        raise TypeError(
            "multifault: cones must be a dict or None, got %r"
            % (type(cones),))

    per_signal = {}
    for sig, suspects in fault_signals.items():
        if isinstance(suspects, (str, bytes)):
            raise TypeError(
                "multifault: suspects for signal %r must be an "
                "iterable of lines, not a string" % (sig,))
        try:
            uniq = set(suspects)
        except TypeError:
            raise TypeError(
                "multifault: suspects for signal %r must be an "
                "iterable, got %r" % (sig, type(suspects)))
        per_signal[sig] = uniq

    cone_sets = []
    for cid, lines in cones.items():
        if isinstance(lines, (str, bytes)):
            raise TypeError(
                "multifault: cone %r must be an iterable of lines, "
                "not a string" % (cid,))
        try:
            cone_sets.append(set(lines))
        except TypeError:
            raise TypeError(
                "multifault: cone %r must be an iterable, got %r"
                % (cid, type(lines)))

    counts = {}
    for uniq in per_signal.values():
        for line in uniq:
            counts[line] = counts.get(line, 0) + 1

    def overlap(line):
        return sum(1 for cone in cone_sets if line in cone)

    return sorted(counts, key=lambda ln: (-counts[ln], -overlap(ln), str(ln)))
