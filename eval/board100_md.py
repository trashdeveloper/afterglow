#!/usr/bin/env python3
"""Render eval/mutant_board_100.md from eval/mutant_board_100.json.

Pure rendering, no sim. Run after the H10 campaign completes:
  python3 eval/board100_md.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOARD_JSON = os.path.join(ROOT, "eval", "mutant_board_100.json")
BOARD_MD = os.path.join(ROOT, "eval", "mutant_board_100.md")


def render(board):
    a = board["accounting"]
    lines = []
    lines.append("# Mutant board 100 (H10)")
    lines.append("")
    lines.append("Fresh-seed (777) nightly mining: %d kept RED-proven "
                 "mutants, quarantined from pilot / big-N / pair / "
                 "stranger families (new mut-100-* ids, fresh draw)." % len(
                     board["kept"]))
    lines.append("")
    lines.append("drawn=%d kept_new=%d pilot_red=%d equivalent=%d "
                 "build_break=%d other=%d untried=%d" % (
                     a["drawn"], a["kept_new"],
                     board["pilot_import"].get("red_count", "?"),
                     a["equivalent"], a["build_break"], a["other"],
                     len(board.get("untried", []))))
    lines.append("")
    lines.append("| id | file:line | op | parent | cycles |")
    lines.append("|---|---|---|---|---|")
    for k in board["kept"]:
        lines.append("| %s | %s:%d | %s | %s | %s |" % (
            k["mutant_id"], k["file"], k["line"], k["op"],
            k["parent_case"], k["red"]["cycles"]))
    lines.append("")
    return "\n".join(lines) + "\n"


def main():
    with open(BOARD_JSON, encoding="utf-8") as fh:
        board = json.load(fh)
    assert board.get("complete"), "board not complete"
    with open(BOARD_MD, "w", encoding="utf-8") as fh:
        fh.write(render(board))
    print("wrote %s (%d kept)" % (BOARD_MD, len(board["kept"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
