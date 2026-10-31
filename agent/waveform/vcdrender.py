#!/usr/bin/env python3
"""WAVEFORM-VISION PILOT: minimal VCD -> PNG waveform renderer.

The image model is load-bearing only if a failing window can be RENDERED
from a real dump. This module does the rendering half (no model, no API,
no diagnosis wiring -- that is a later task):

  VCD text --(protocol.parse_vcd)--> samples --(PIL)--> waveform PNG
  with the failing window shaded red.

VCD parsing is REUSED from agent/discover/protocol.py (imported, never
duplicated), so this file stays stdlib-only except for the PNG backend
(Pillow, already used for explain.png handling in this repo).

Geometry contract (tests probe pixels against these constants, so they
are load-bearing -- do not renumber silently):
  LEFT_W    label gutter width (px)
  TOP_H     title strip height (px); row area starts at y == TOP_H
  ROW_H     height of one signal row (px)
  BOTTOM_H  time-axis strip height (px)
  BG_RGB        canvas background (white)
  HIGHLIGHT_RGB failing-window tint (light red)
Draw order is background -> grid -> highlight -> traces -> text, so a
probe pixel inside the highlight box at y == TOP_H + 4 is ALWAYS tint
(traces run mid-row, grid is painted over by the tint).

Loudness: empty VCD, unknown/ambiguous signal, empty signal list, or
an inverted/negative fail window all raise ValueError -- never an
empty or mislabeled picture.
"""
from __future__ import annotations

import os

from discover import protocol

LEFT_W = 170
RIGHT_PAD = 24
TOP_H = 44
ROW_H = 48
BOTTOM_H = 30
DEFAULT_WIDTH = 1000

BG_RGB = (255, 255, 255)
GRID_RGB = (228, 228, 228)
TEXT_RGB = (0, 0, 0)
TRACE_RGB = (20, 60, 160)
VECTOR_RGB = (20, 120, 60)
UNKNOWN_RGB = (200, 120, 20)
HIGHLIGHT_RGB = (255, 220, 220)
HIGHLIGHT_EDGE_RGB = (200, 40, 40)

_SCALAR_LEVELS = ("0", "1", "x", "z")


def _check_window(fail_start, fail_end):
    if (fail_start is None) != (fail_end is None):
        raise ValueError("fail_start and fail_end must be given together, "
                         "got %r / %r" % (fail_start, fail_end))
    if fail_start is None:
        return None
    try:
        fs = float(fail_start)
        fe = float(fail_end)
    except (TypeError, ValueError):
        raise ValueError("fail window must be numeric, got %r / %r"
                         % (fail_start, fail_end))
    if fs < 0 or fe < 0:
        raise ValueError("fail window must be >= 0, got %r / %r" % (fs, fe))
    if not fe > fs:
        raise ValueError("fail window must satisfy fail_end > fail_start, "
                         "got %r / %r" % (fs, fe))
    return (fs, fe)


def render_trace(trace, signals, out_path, fail_start=None, fail_end=None,
                 title=None, width=DEFAULT_WIDTH):
    """Render an already-parsed protocol.parse_vcd trace dict to PNG."""
    if not isinstance(signals, (list, tuple)) or not signals:
        raise ValueError("signals must be a non-empty list of VCD signal "
                         "names, got %r" % (signals,))
    if not isinstance(trace, dict) or "samples" not in trace:
        raise ValueError("trace must be a protocol.parse_vcd dict")
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise RuntimeError("Pillow is required to render waveforms "
                           "(pip install pillow)")

    resolved = [protocol.resolve_signal(trace, s) for s in signals]
    window = _check_window(fail_start, fail_end)
    t_view = trace.get("t_end", 0) or 0
    if window is not None:
        t_view = max(t_view, window[1])
    t_view = max(t_view, 1)

    rows = len(resolved)
    height = TOP_H + rows * ROW_H + BOTTOM_H
    plot_w = width - LEFT_W - RIGHT_PAD

    def x_of(t):
        return LEFT_W + int(round(plot_w * min(max(t, 0), t_view) / t_view))

    img = Image.new("RGB", (width, height), BG_RGB)
    dr = ImageDraw.Draw(img)

    # Grid (painted BEFORE the highlight so the tint covers it).
    for k in range(11):
        x = LEFT_W + int(round(plot_w * k / 10))
        dr.line([(x, TOP_H), (x, TOP_H + rows * ROW_H)], fill=GRID_RGB)
        dr.text((x + 2, TOP_H + rows * ROW_H + 4), str(int(t_view * k / 10)),
                fill=TEXT_RGB)

    # Failing-window highlight: full row area, tint + red edge.
    highlight = None
    if window is not None:
        x0 = x_of(window[0])
        x1 = x_of(window[1])
        y0 = TOP_H
        y1 = TOP_H + rows * ROW_H
        dr.rectangle([(x0, y0), (x1, y1)], fill=HIGHLIGHT_RGB,
                     outline=HIGHLIGHT_EDGE_RGB)
        dr.text((x0 + 4, y0 + 2), "FAIL WINDOW", fill=HIGHLIGHT_EDGE_RGB)
        highlight = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}

    # Traces, one row per signal.
    for r, name in enumerate(resolved):
        y_top = TOP_H + r * ROW_H
        y_mid = y_top + ROW_H // 2
        y_hi = y_top + 12
        y_lo = y_top + ROW_H - 12
        dr.line([(LEFT_W, y_top), (width - RIGHT_PAD, y_top)],
                fill=GRID_RGB)
        dr.text((6, y_mid - 6), name[-24:], fill=TEXT_RGB)
        samples = list(trace["samples"].get(name, []))
        if not samples:
            for xx in range(LEFT_W, width - RIGHT_PAD, 8):
                dr.point((xx, y_mid), fill=UNKNOWN_RGB)
            dr.text((LEFT_W + 4, y_top + 2), "no samples", fill=UNKNOWN_RGB)
            continue
        val0 = samples[0][1]
        if len(val0) == 1 and val0 in _SCALAR_LEVELS:
            # Scalar: stepped 0/1 line, mid-level orange for x/z.
            pts = []
            prev_t = 0
            prev_v = val0
            for (t, v) in samples:
                v = v if (len(v) == 1 and v in _SCALAR_LEVELS) else "x"
                y_prev = (y_hi if prev_v == "1" else
                          y_lo if prev_v == "0" else y_mid)
                y_now = (y_hi if v == "1" else y_lo if v == "0" else y_mid)
                x_prev = x_of(prev_t)
                x_now = x_of(t)
                pts.append(((x_prev, y_prev), (x_now, y_prev)))
                if y_now != y_prev:
                    pts.append(((x_now, y_prev), (x_now, y_now)))
                prev_t, prev_v = t, v
            pts.append(((x_of(prev_t),
                         y_hi if prev_v == "1" else
                         y_lo if prev_v == "0" else y_mid),
                        (x_of(t_view),
                         y_hi if prev_v == "1" else
                         y_lo if prev_v == "0" else y_mid)))
            for a, b in pts:
                dr.line([a, b], fill=TRACE_RGB)
        else:
            # Vector/bus: mid line + tick per change + value label.
            dr.line([(LEFT_W, y_mid), (x_of(t_view), y_mid)],
                    fill=VECTOR_RGB)
            for i, (t, v) in enumerate(samples):
                x = x_of(t)
                dr.line([(x, y_top + 8), (x, y_top + ROW_H - 8)],
                        fill=VECTOR_RGB)
                nxt = samples[i + 1][0] if i + 1 < len(samples) else t_view
                xm = (x + x_of(nxt)) // 2
                label = str(v)
                if len(label) > 12:
                    label = label[:11] + "."
                dr.text((max(x + 2, LEFT_W + 2) if xm - x < 30 else xm - 20,
                         y_mid - 8), label, fill=VECTOR_RGB)

    head = title or ("WAVEFORM-VISION PILOT: %d signal(s) t=[0,%d] %s"
                     % (rows, t_view, trace.get("timescale", "unknown")))
    if window is not None:
        head += "  fail=[%g,%g]" % window
    dr.text((6, 6), head[:120], fill=TEXT_RGB)
    dr.line([(LEFT_W, TOP_H + rows * ROW_H),
             (width - RIGHT_PAD, TOP_H + rows * ROW_H)], fill=TEXT_RGB)

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    img.save(out_path, "PNG")
    return {"signals": resolved, "rows": rows, "width": width,
            "height": height, "t_view": t_view,
            "highlight": highlight, "out": out_path}


def render(vcd_text, signals, out_path, **kwargs):
    """Parse VCD text (protocol.parse_vcd) and render it to PNG."""
    if not isinstance(vcd_text, str) or not vcd_text.strip():
        raise ValueError("empty VCD text: refusing to render nothing")
    return render_trace(protocol.parse_vcd(vcd_text), signals, out_path,
                        **kwargs)


def render_vcd_file(vcd_path, signals, out_path, **kwargs):
    """Read a VCD file and render it to PNG (loud on missing file)."""
    if not os.path.isfile(vcd_path):
        raise ValueError("VCD file not found: %r" % (vcd_path,))
    with open(vcd_path, encoding="utf-8") as fh:
        return render(fh.read(), signals, out_path, **kwargs)
