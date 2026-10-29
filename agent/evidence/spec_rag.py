#!/usr/bin/env python3
"""Task B2: Spec RAG (plan item 12, Wave B SEE MORE).

EvidencePack extension: chunks docs/ spec excerpts (the design doc
docs/superpowers/specs/*.md and AGENTS.md sections) and retrieves the
most relevant chunks per fault with Jaccard token overlap, citing
chunk ids in prompt output.

Interfaces:
  tokenize(text) -> set of tokens
  jaccard(a, b) -> float in [0, 1]
  chunk_markdown(source_id, text, max_lines) -> [{id, source, ...}]
  build_corpus({source_id: text}) -> [chunk]
  load_default_corpus(repo_root) -> [chunk]  (specs/*.md + AGENTS.md)
  build_query(fault_file, fault_line, context_tokens) -> str
  retrieve(query_text, chunks, top_k) -> [{id, source, text, score}]
  format_prompt_section(results, max_chars) -> str (cited, "" if none)
  retrieve_for_prompt(..., enable_spec_rag) -> EvidencePack section

Default-off contract (Wave B header: every addition is additive +
default-off): retrieve_for_prompt with enable_spec_rag=False (the
default) returns {"spec_enabled": False, "spec_chunks": [],
"prompt_section": ""} WITHOUT reading any file or chunking anything.
The loop (agent/loop.py) is NOT edited by this task; prompt builders
opt in by passing enable_spec_rag=True with a caller-built query
(fault file + line + surrounding RTL tokens).

Honesty contract (mirrors vcd_slice.py: never invent evidence):
- An unknown query (zero token overlap with every chunk) returns [],
  never a best-effort guess. Every returned id comes from the corpus.
- format_prompt_section([]) is "" -- no hits, no section, no citation.
- enable_spec_rag=True with an empty query raises ValueError instead
  of retrieving against nothing.

Pure stdlib (os, re, glob); no API, no keys, no network.
"""
import glob
import os
import re

CHUNK_MAX_LINES = 40
DEFAULT_TOP_K = 3
DEFAULT_SNIPPET_CHARS = 600

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def tokenize(text):
    """Lowercase alphanumeric/underscore token set (keeps RTL names)."""
    if not isinstance(text, str):
        raise ValueError("tokenize needs a string, got %r" % (text,))
    return set(_TOKEN_RE.findall(text.lower()))


def jaccard(a, b):
    """Jaccard similarity of two token sets; empty either side -> 0.0."""
    a = set(a)
    b = set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def chunk_markdown(source_id, text, max_lines=CHUNK_MAX_LINES):
    """Split one markdown doc into heading sections, then sub-chunks.

    Each chunk keeps its VERBATIM lines (anchor-line containment holds)
    and gets a stable id "<source_id>#c<NN>". Sections longer than
    max_lines split into consecutive sub-chunks. Raises ValueError on
    empty text (refusing to chunk emptiness).
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("refusing to chunk empty text for %r"
                         % (source_id,))
    if not isinstance(max_lines, int) or max_lines <= 0:
        raise ValueError("max_lines must be a positive int, got %r"
                         % (max_lines,))
    lines = text.splitlines()
    sections = []  # [(heading, [lines])]
    heading = "(preamble)"
    buf = []
    for ln in lines:
        m = _HEADING_RE.match(ln)
        if m:
            if any(s.strip() for s in buf):
                sections.append((heading, buf))
            heading = m.group(2).strip()
            buf = [ln]
        else:
            buf.append(ln)
    if any(s.strip() for s in buf):
        sections.append((heading, buf))
    if not sections:
        raise ValueError("no chunkable content in %r" % (source_id,))
    chunks = []
    for heading, sec_lines in sections:
        for start in range(0, len(sec_lines), max_lines):
            window = sec_lines[start:start + max_lines]
            body = "\n".join(window).strip()
            if not body:
                continue
            idx = len(chunks)
            chunks.append({
                "id": "%s#c%02d" % (source_id, idx),
                "source": source_id,
                "heading": heading,
                "text": body,
                "tokens": tokenize(body),
            })
    if not chunks:
        raise ValueError("no chunkable content in %r" % (source_id,))
    return chunks


def build_corpus(doc_texts):
    """Chunk a {source_id: markdown text} map into one chunk list."""
    if not isinstance(doc_texts, dict) or not doc_texts:
        raise ValueError("build_corpus needs a non-empty "
                         "{source_id: text} map")
    chunks = []
    for source_id in sorted(doc_texts):
        chunks.extend(chunk_markdown(source_id, doc_texts[source_id]))
    # Re-number globally so ids stay unique across docs sharing a name.
    seen = set()
    for c in chunks:
        base = c["id"]
        cand = base
        n = 0
        while cand in seen:
            n += 1
            cand = "%s~%d" % (base, n)
        seen.add(cand)
        c["id"] = cand
    return chunks


def _repo_root_here():
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_default_corpus(repo_root=None):
    """Chunk the repo's spec excerpts: specs/*.md + AGENTS.md.

    Raises FileNotFoundError when no spec doc exists under repo_root
    (refusing to retrieve against an empty corpus silently).
    """
    root = repo_root or _repo_root_here()
    doc_texts = {}
    for path in sorted(glob.glob(os.path.join(
            root, "docs", "superpowers", "specs", "*.md"))):
        with open(path, encoding="utf-8") as fh:
            doc_texts[os.path.basename(path)] = fh.read()
    agents = os.path.join(root, "AGENTS.md")
    if os.path.isfile(agents):
        with open(agents, encoding="utf-8") as fh:
            doc_texts["AGENTS.md"] = fh.read()
    if not doc_texts:
        raise FileNotFoundError(
            "no spec docs under %r: refusing to build an empty corpus"
            % (root,))
    return build_corpus(doc_texts)


def build_query(fault_file, fault_line, context_tokens):
    """Build the retrieval query string from caller-supplied fault data.

    fault_file + fault_line locate the fault; context_tokens are the
    surrounding RTL tokens (signal names, RHS fragments, trap words)
    passed in by the caller. Raises ValueError on missing parts --
    retrieval never runs against a half-built query.
    """
    if not fault_file or not str(fault_file).strip():
        raise ValueError("build_query needs a fault_file")
    if fault_line is None or str(fault_line).strip() == "":
        raise ValueError("build_query needs a fault_line")
    toks = list(context_tokens or [])
    if not toks:
        raise ValueError("build_query needs non-empty context_tokens")
    return "%s line %s %s" % (fault_file, fault_line, " ".join(
        str(t) for t in toks))


def retrieve(query_text, chunks, top_k=DEFAULT_TOP_K):
    """Rank chunks by Jaccard(query tokens, chunk tokens), desc.

    Ties break by chunk id (deterministic). Chunks scoring 0.0 are
    dropped: an unknown query returns [] instead of an invented hit.
    top_k caps the list (None = all positive hits).
    """
    if top_k is not None and top_k <= 0:
        raise ValueError("top_k must be positive, got %r" % (top_k,))
    if not isinstance(query_text, str) or not query_text.strip():
        return []
    if not chunks:
        return []
    qtokens = tokenize(query_text)
    scored = []
    for c in chunks:
        score = jaccard(qtokens, c.get("tokens") or tokenize(c["text"]))
        if score > 0.0:
            scored.append((score, c["id"], c))
    scored.sort(key=lambda t: (-t[0], t[1]))
    hits = [{"id": c["id"], "source": c["source"],
             "heading": c.get("heading", ""),
             "text": c["text"], "score": round(s, 6)}
            for (s, _i, c) in scored]
    if top_k is not None:
        hits = hits[:top_k]
    return hits


def format_prompt_section(results, max_chars=DEFAULT_SNIPPET_CHARS):
    """Render retrieved hits as a cited prompt section, "" when empty.

    Every citation is a real corpus chunk id ([<id>]); snippets are
    verbatim chunk prefixes, never rewritten. Empty results -> ""
    (no invented citations).
    """
    if not results:
        return ""
    if max_chars is not None and max_chars <= 0:
        raise ValueError("max_chars must be positive, got %r"
                         % (max_chars,))
    parts = ["Spec context (Jaccard retrieval, cited chunk ids):"]
    for hit in results:
        snippet = hit["text"]
        if max_chars is not None and len(snippet) > max_chars:
            snippet = snippet[:max_chars].rstrip() + " [...]"
        parts.append("[%s] (score %.4f, %s :: %s)\n%s"
                     % (hit["id"], hit["score"], hit["source"],
                        hit.get("heading", ""), snippet))
    return "\n".join(parts)


def retrieve_for_prompt(query_text=None, chunks=None, repo_root=None,
                        enable_spec_rag=False, top_k=DEFAULT_TOP_K):
    """EvidencePack section dict with a default-off Spec RAG section.

    enable_spec_rag=False (default): returns {"spec_enabled": False,
    "spec_chunks": [], "prompt_section": ""} WITHOUT reading files or
    chunking -- existing loop/prompt behavior is byte-identical.
    enable_spec_rag=True: requires a non-empty caller-built query_text
    (ValueError otherwise); uses the passed chunks or loads the
    default corpus from repo_root; retrieves top_k hits.
    """
    pack = {"spec_enabled": bool(enable_spec_rag), "spec_chunks": [],
            "prompt_section": ""}
    if not enable_spec_rag:
        return pack
    if not isinstance(query_text, str) or not query_text.strip():
        raise ValueError("enable_spec_rag=True needs a non-empty "
                         "query_text (fault file + line + RTL tokens): "
                         "refusing to retrieve against nothing")
    if chunks is None:
        chunks = load_default_corpus(repo_root)
    hits = retrieve(query_text, chunks, top_k=top_k)
    pack["spec_chunks"] = hits
    pack["prompt_section"] = format_prompt_section(hits)
    return pack
