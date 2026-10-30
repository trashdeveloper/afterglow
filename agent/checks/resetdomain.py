#!/usr/bin/env python3
"""Task F4: Reset-domain crossing check (plan item 64).

Flags registers whose reset source differs from their clock domain's
reset.

Interface:
  check(modules, reset_map) -> [violation...]
    modules: list/tuple of dicts, each with "name", "clk_domain",
      "reset_source".
    reset_map: mapping {domain: reset_net} giving the expected reset
      net for each clock domain.
    Returns a list of violations, one per mismatched register, in
    input order. Each violation is a dict:
      {"register": <name>, "expected": <reset_map[clk_domain]>,
       "actual": <reset_source>}.
    Registers whose reset_source equals their domain's reset_net are
    not flagged. An empty modules list yields [].
    A module whose clk_domain is absent from reset_map raises KeyError
    loudly (fail-closed: an unknown domain is never silently passed).
    Malformed inputs (non-list modules, non-dict entries, missing
    keys) raise TypeError/ValueError/KeyError loudly.

Notes: stdlib only, in-memory only (never writes files), no live API.
Deterministic: input order in, input order out.
"""


def check(modules, reset_map):
    """Flag registers on the wrong reset for their clock domain."""
    if isinstance(modules, (str, bytes)) or not isinstance(
            modules, (list, tuple)):
        raise TypeError("modules must be a list/tuple of dicts, got %r"
                        % type(modules).__name__)
    if not isinstance(reset_map, dict):
        raise TypeError("reset_map must be a dict {domain: reset_net}, got %r"
                        % type(reset_map).__name__)
    violations = []
    for entry in modules:
        if not isinstance(entry, dict):
            raise TypeError("each module must be a dict, got %r"
                            % type(entry).__name__)
        try:
            name = entry["name"]
            domain = entry["clk_domain"]
            actual = entry["reset_source"]
        except KeyError:
            raise
        if not isinstance(name, str) or not name:
            raise ValueError("module 'name' must be a non-empty string, got %r"
                             % (name,))
        if not isinstance(domain, str) or not domain:
            raise ValueError("module 'clk_domain' must be a non-empty string, "
                             "got %r" % (domain,))
        try:
            expected = reset_map[domain]
        except KeyError:
            raise KeyError(
                "unknown clk_domain %r for register %r: no entry in reset_map"
                % (domain, name))
        if actual != expected:
            violations.append({"register": name,
                               "expected": expected,
                               "actual": actual})
    return violations
