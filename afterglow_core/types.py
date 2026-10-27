"""Core contract: Verdict, CaseSpec, SimBackend, decide_verdict.

Verdict.pass ALWAYS comes from the oracle marker in sim output,
never from a process exit code.
"""
from dataclasses import dataclass
from typing import Protocol


class Verdict:
    """Verdict(pass: bool, evidence: dict).

    `pass` ALWAYS comes from the oracle marker in sim output, never from
    a process exit code. `pass` is a Python keyword, so it cannot be a
    literal parameter/attribute name in source: construct with
    Verdict(**{"pass": ..., "evidence": ...}) and read with
    getattr(verdict, "pass").
    """

    def __init__(self, evidence: dict | None = None, **kwargs) -> None:
        if set(kwargs) != {"pass"}:
            raise TypeError(f"Verdict requires exactly 'pass', got: {sorted(kwargs)}")
        setattr(self, "pass", kwargs["pass"])
        self.evidence: dict = evidence if evidence is not None else {}

    def __eq__(self, other) -> bool:
        if not isinstance(other, Verdict):
            return NotImplemented
        return getattr(self, "pass") == getattr(other, "pass") and self.evidence == other.evidence

    def __repr__(self) -> str:
        return f"Verdict(pass={getattr(self, 'pass')!r}, evidence={self.evidence!r})"


@dataclass
class CaseSpec:
    case_id: str
    project: str
    files: list
    oracle_pass: str
    oracle_trap: str
    budget_s: int
    allowlist: list
    lint_cmd: str


class SimBackend(Protocol):
    def build(self, tree) -> None:
        ...

    def run(self, seed: int) -> Verdict:
        ...


def decide_verdict(log_text: str, oracle_pass: str) -> Verdict:
    matched = oracle_pass in log_text
    return Verdict(
        **{
            "pass": matched,
            "evidence": {"oracle_pass": oracle_pass, "matched": matched},
        }
    )
