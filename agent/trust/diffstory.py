"""Human-readable diff story (Task D7-TRUE, plan item 37, Wave D).

Renders a patch explanation in the design's own signal names:
file, hunk line, signals changed, and direction (invert / constant /
widen / narrow / edit). stdlib only (`re`); no loop/guard wiring
(default-off).
"""
import re

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_FILE_RE = re.compile(r"^\+\+\+ [ab]/(.+)$")
_SIGNAL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_KEYWORDS = frozenset([
    "if", "else", "begin", "end", "always", "assign", "wire", "reg",
    "input", "output", "inout", "module", "endmodule", "case",
    "endcase", "for", "while", "posedge", "negedge", "or", "and",
    "not", "parameter", "localparam", "function", "endfunction",
    "task", "endtask", "generate", "endgenerate", "initial", "integer",
])


def _is_signal(word):
    return bool(word) and word not in _KEYWORDS and (
        "_" in word or any(ch.isdigit() for ch in word))


def _signals_in_order(lines):
    seen = []
    for line in lines:
        for word in _SIGNAL_RE.findall(line):
            if _is_signal(word) and word not in seen:
                seen.append(word)
    return seen


def _hunk_line_number(new_start, body):
    # 1-based index of the first removed line inside the hunk body,
    # falling back to the first added line (pure additions).
    for index, line in enumerate(body, start=1):
        if line.startswith("-") and not line.startswith("---"):
            return new_start + index
    for index, line in enumerate(body, start=1):
        if line.startswith("+") and not line.startswith("+++"):
            return new_start + index
    return new_start


def _classify(minus_lines, plus_lines):
    minus = [ln[1:].strip() for ln in minus_lines]
    plus = [ln[1:].strip() for ln in plus_lines]
    if len(minus) == 1 and len(plus) == 1:
        before, after = minus[0], plus[0]
        inv = before.replace("==", "!=") == after
        inv_back = before.replace("!=", "==") == after
        if inv:
            return "inverted (`==` -> `!=`)"
        if inv_back:
            return "inverted (`!=` -> `==`)"
        set_match = re.match(r"^(\S+)\s*<=?\s*(\S+?)\s*;$", before)
        set_match_after = re.match(r"^(\S+)\s*<=?\s*(\S+?)\s*;$", after)
        if set_match and set_match_after and \
                set_match.group(1) == set_match_after.group(1):
            return "set to constant %s (was %s)" % (
                set_match_after.group(2), set_match.group(2))
        range_match = re.match(r"^(.*)\[(\d+):(\d+)\](.*)$", before)
        if range_match:
            after_m = re.match(r"^(.*)\[(\d+):(\d+)\](.*)$", after)
            if after_m and (range_match.group(1), range_match.group(4)) == \
                    (after_m.group(1), after_m.group(4)):
                before_w = int(range_match.group(2)) - \
                    int(range_match.group(3)) + 1
                after_w = int(after_m.group(2)) - \
                    int(after_m.group(3)) + 1
                if after_w > before_w:
                    return "widened ([%s:%s] -> [%s:%s])" % (
                        range_match.group(2), range_match.group(3),
                        after_m.group(2), after_m.group(3))
                if after_w < before_w:
                    return "narrowed ([%s:%s] -> [%s:%s])" % (
                        range_match.group(2), range_match.group(3),
                        after_m.group(2), after_m.group(3))
    return "edited (`%s` -> `%s`)" % (" | ".join(minus), " | ".join(plus))


def _parse_hunks(diff_text):
    files = []
    hunks = []
    current_file = None
    new_start = None
    body = []

    def flush():
        if current_file is not None and body:
            hunks.append({"file": current_file, "new_start": new_start,
                          "body": list(body)})

    for raw in diff_text.splitlines():
        file_match = _FILE_RE.match(raw)
        if file_match:
            flush()
            current_file = file_match.group(1)
            body = []
            if current_file not in files:
                files.append(current_file)
            continue
        hunk_match = _HUNK_RE.match(raw)
        if hunk_match:
            flush()
            new_start = int(hunk_match.group(1))
            body = []
            continue
        if current_file is not None and new_start is not None and \
                (raw.startswith(" ") or raw.startswith("-")
                 or raw.startswith("+")):
            body.append(raw)
    flush()
    return files, hunks


def story_from_patch(diff_text):
    """Render a unified diff as pinned plain-language sentences."""
    if isinstance(diff_text, bool) or not isinstance(diff_text, str) \
            or not diff_text.strip():
        raise ValueError("diff_text must be a non-blank string")
    files, hunks = _parse_hunks(diff_text)
    if not files or not hunks:
        raise ValueError("diff contains no files or hunks")
    sentences = ["Patch touches %d file(s) in %d hunk(s)."
                 % (len(files), len(hunks))]
    hunk_records = []
    for number, hunk in enumerate(hunks, start=1):
        minus = [ln for ln in hunk["body"]
                 if ln.startswith("-") and not ln.startswith("---")]
        plus = [ln for ln in hunk["body"]
                if ln.startswith("+") and not ln.startswith("+++")]
        signals = _signals_in_order(minus + plus)
        line_no = _hunk_line_number(hunk["new_start"], hunk["body"])
        direction = _classify(minus, plus)
        hunk_records.append({"file": hunk["file"], "line": line_no,
                             "signals": signals,
                             "direction": direction})
        sentences.append("Hunk %d (%s:%d): %s %s."
                         % (number, hunk["file"], line_no,
                            ", ".join(signals), direction))
    for name in files:
        seen = _signals_in_order(
            [ln[1:] for h in hunks if h["file"] == name for ln in h["body"]])
        if seen:
            sentences.append("Signals seen in %s: %s."
                             % (name, ", ".join(seen)))
    return {"files": files, "hunks": hunk_records,
            "sentences": sentences, "text": "\n".join(sentences)}
