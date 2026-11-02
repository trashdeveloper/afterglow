//! Thin single-row rounded bar for nerd-font terminals: a dim track
//! line with a thin gradient fill (blue `#5c9cf5` → purple `#9d7cd8`
//! → peach `#fab283`), rounded end caps, a travelling bright tip, and a
//! braille spinner while running.
//!
//! All geometry here is pure (no terminal access) so `cargo test` pins it.

/// Gradient stops: blue → purple → peach.
pub const BLUE: (u8, u8, u8) = (0x5c, 0x9c, 0xf5);
pub const PURPLE: (u8, u8, u8) = (0x9d, 0x7c, 0xd8);
pub const PEACH: (u8, u8, u8) = (0xfa, 0xb2, 0x83);

/// Track colour for the unfilled portion (dim slate).
pub const TRACK: (u8, u8, u8) = (0x3a, 0x3a, 0x46);

/// Bar height: exactly 1 terminal row. The bar is a single [`Cell`] row
/// of `width` cells — caps plus one thin line — never a multi-row block.
/// Pinned at compile time (`_BAR_IS_SINGLE_ROW`) and by `cargo test`.
pub const BAR_HEIGHT: usize = 1;
const _: () = assert!(BAR_HEIGHT == 1);

/// Glyph for the rounded end caps: U+E0B6 `` (left) / U+E0B4 ``
/// (right), the powerline rounded separators shipped by every major
/// nerd-font patch set (FiraCode Nerd Font, JetBrainsMono Nerd Font,
/// Hack Nerd Font, …). Chosen over box-drawing `╭╮` corners — which read
/// as corners, not caps — and over circles `●`, which blob at small
/// sizes: the E0B6/E0B4 pair renders as clean half-disc caps that hug a
/// single-row horizontal line, matching powerline statusline segments
/// terminal users already see daily.
pub const LEFT_CAP: char = '\u{e0b6}';
pub const RIGHT_CAP: char = '\u{e0b4}';

/// Thin-line glyph shared by track and fill (colour tells them apart:
/// dim [`TRACK`] vs the [`gradient_at`] sweep). One horizontal rule per
/// cell keeps the bar exactly [`BAR_HEIGHT`] row tall with no block fill.
pub const FILL_GLYPH: char = '─';

/// Track glyph: a light horizontal rule per cell.
pub const TRACK_GLYPH: char = '─';

// NOTE: the thick-block era (U+2588 eighth-block fill via the old
// EIGHTHS table / fraction_glyph helper) was retired with this restyle
// and fully removed, so no full-block cell can leave the new path:
// track and fill share one thin rule glyph, colour carries the fill.

/// Braille spinner frames (same 10-frame set nerd-font terminals love),
/// stepped every [`SPINNER_MS`] ms.
pub const SPINNER: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/// Spinner cadence in milliseconds.
pub const SPINNER_MS: u64 = 80;

/// Frame for an elapsed millisecond count (wraps every 10 frames).
pub fn spinner_at(elapsed_ms: u64) -> &'static str {
    SPINNER[(elapsed_ms / SPINNER_MS) as usize % SPINNER.len()]
}

fn clamp01(t: f64) -> f64 {
    if t.is_nan() {
        return 0.0;
    }
    t.clamp(0.0, 1.0)
}

fn lerp(a: u8, b: u8, t: f64) -> u8 {
    (a as f64 + (b as f64 - a as f64) * t).round().clamp(0.0, 255.0) as u8
}

fn mix(a: (u8, u8, u8), b: (u8, u8, u8), t: f64) -> (u8, u8, u8) {
    (lerp(a.0, b.0, t), lerp(a.1, b.1, t), lerp(a.2, b.2, t))
}

/// Gradient colour at position `t` in 0..=1: blue → purple on [0, 0.5],
/// purple → peach on [0.5, 1]. Out-of-range inputs clamp.
pub fn gradient_at(t: f64) -> (u8, u8, u8) {
    let t = clamp01(t);
    if t <= 0.5 {
        mix(BLUE, PURPLE, t * 2.0)
    } else {
        mix(PURPLE, PEACH, (t - 0.5) * 2.0)
    }
}

/// Fill fraction from backend progress: verdict seen → full; no total
/// yet → indeterminate 0 (the travelling tip still animates); otherwise
/// `attempt / total`, clamped to [0, 1].
pub fn fill_fraction(attempt: u32, total: u32, verdict_seen: bool) -> f64 {
    if verdict_seen {
        return 1.0;
    }
    if total == 0 {
        return 0.0;
    }
    clamp01(attempt as f64 / total as f64)
}

/// Brighten `rgb` toward white by `amount` in 0..=1 (the sweep tip pop).
pub fn brighten(rgb: (u8, u8, u8), amount: f64) -> (u8, u8, u8) {
    let a = clamp01(amount);
    (
        lerp(rgb.0, 255, a),
        lerp(rgb.1, 255, a),
        lerp(rgb.2, 255, a),
    )
}

/// Travelling tip position in cell units. Loops every `period_s`
/// seconds across `width` cells (with overhang so it enters/exits
/// smoothly); `None` when there is nothing to animate across.
fn tip_position(elapsed_s: f64, width: usize) -> Option<f64> {
    if width == 0 {
        return None;
    }
    let period_s = 3.0;
    let span = width as f64 + 16.0;
    let x = (elapsed_s % period_s) / period_s * span - 8.0;
    Some(x)
}

/// Extra brightness for cell `x` given the tip at `tip_x`: a gaussian
/// falloff (σ ≈ 2.2 cells), 0 far from the tip.
fn tip_boost(x: f64, tip_x: f64) -> f64 {
    let d = (x - tip_x) / 2.2;
    (-d * d).exp() * 0.75
}

/// One rendered cell: glyph + truecolor foreground.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cell {
    pub glyph: char,
    pub rgb: (u8, u8, u8),
}

/// Number of filled inner cells for `fill` in 0..=1 across a `width`
/// cell row: the inner run is `width - 2` (caps excluded), so
/// `round(fill * inner)`. Pure and tip-independent — the proportionality
/// oracle `cargo test` pins the rendered gradient against.
pub fn filled_len(width: usize, fill: f64) -> usize {
    let inner = width.saturating_sub(2);
    (clamp01(fill) * inner as f64).round() as usize
}

/// Render the bar into exactly `width` cells on exactly [`BAR_HEIGHT`]
/// row: rounded caps ([`LEFT_CAP`] in [`BLUE`], [`RIGHT_CAP`] in
/// [`PEACH`] — always the exact gradient endpoints, excluded from the
/// travelling-tip boost so they read as a stable frame) around one thin
/// line. The first [`filled_len`] inner cells ride the [`gradient_at`]
/// sweep in [`FILL_GLYPH`]; the rest are the dim [`TRACK`] line in
/// [`TRACK_GLYPH`]. The travelling tip survives as pure brightness on
/// the gaussian peak cell — a single bright cell, no full-block cursor —
/// so consecutive frames differ in colour while glyphs stay stable.
/// Zero width yields zero cells (never panics).
pub fn render_bar(width: usize, fill: f64, elapsed_s: f64) -> Vec<Cell> {
    let fill = clamp01(fill);
    if width == 0 {
        return Vec::new();
    }
    if width == 1 {
        // Degenerate pty column: a single left cap in spec blue.
        return vec![Cell {
            glyph: LEFT_CAP,
            rgb: gradient_at(0.0),
        }];
    }
    let inner = width - 2;
    let filled = filled_len(width, fill);
    let tip_x = tip_position(elapsed_s, width);
    let mut cells = Vec::with_capacity(width);
    cells.push(Cell {
        glyph: LEFT_CAP,
        rgb: gradient_at(0.0),
    });
    for k in 0..inner {
        // Gradient parameter across the inner run (0 at the left cap,
        // 1 at the right cap); cell coordinate in bar units for the tip.
        let t = if inner <= 1 {
            0.0
        } else {
            k as f64 / (inner - 1) as f64
        };
        let x = (k + 1) as f64;
        if k < filled {
            let boost = tip_x.map_or(0.0, |tx| tip_boost(x, tx));
            cells.push(Cell {
                glyph: FILL_GLYPH,
                rgb: brighten(gradient_at(t), boost),
            });
        } else {
            let boost = tip_x.map_or(0.0, |tx| tip_boost(x, tx) * 0.5);
            cells.push(Cell {
                glyph: TRACK_GLYPH,
                rgb: brighten(TRACK, boost),
            });
        }
    }
    cells.push(Cell {
        glyph: RIGHT_CAP,
        rgb: gradient_at(1.0),
    });
    cells
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gradient_stops_match_spec_hex() {
        assert_eq!(gradient_at(0.0), BLUE);
        assert_eq!(gradient_at(0.0), (0x5c, 0x9c, 0xf5));
        assert_eq!(gradient_at(0.5), PURPLE);
        assert_eq!(gradient_at(0.5), (0x9d, 0x7c, 0xd8));
        assert_eq!(gradient_at(1.0), PEACH);
        assert_eq!(gradient_at(1.0), (0xfa, 0xb2, 0x83));
    }

    #[test]
    fn gradient_midpoints_blend_monotonically() {
        let q = gradient_at(0.25);
        // Blue → purple: red rises, blue falls.
        assert!(q.0 > BLUE.0 && q.0 < PURPLE.0, "q={q:?}");
        assert!(q.2 < BLUE.2 && q.2 > PURPLE.2, "q={q:?}");
        let q3 = gradient_at(0.75);
        // Purple → peach: red and green rise toward peach.
        assert!(q3.0 > PURPLE.0 && q3.0 < PEACH.0, "q3={q3:?}");
        assert!(q3.1 > PURPLE.1 && q3.1 < PEACH.1, "q3={q3:?}");
        // Clamps instead of panicking.
        assert_eq!(gradient_at(-2.0), BLUE);
        assert_eq!(gradient_at(99.0), PEACH);
    }

    #[test]
    fn fill_fraction_math() {
        assert_eq!(fill_fraction(0, 0, false), 0.0); // indeterminate
        assert_eq!(fill_fraction(0, 3, false), 0.0);
        assert!((fill_fraction(1, 3, false) - 1.0 / 3.0).abs() < 1e-9);
        assert_eq!(fill_fraction(3, 3, false), 1.0);
        assert_eq!(fill_fraction(9, 3, false), 1.0); // clamp, never > 1
        assert_eq!(fill_fraction(1, 3, true), 1.0); // verdict → full
        assert_eq!(fill_fraction(0, 0, true), 1.0);
    }

    #[test]
    fn bar_truncation_never_panics_and_keeps_width() {
        for w in [0, 1, 2, 5, 80] {
            for fill in [0.0, 0.33, 1.0] {
                let cells = render_bar(w, fill, 0.7);
                assert_eq!(cells.len(), w, "w={w} fill={fill}");
            }
        }
        // Full fill: caps + an all-gradient thin line, never a block.
        let full = render_bar(10, 1.0, 0.0);
        assert_eq!(full[0].glyph, LEFT_CAP);
        assert_eq!(full[9].glyph, RIGHT_CAP);
        assert!(full[1..9].iter().all(|c| c.glyph == FILL_GLYPH));
        assert!(full.iter().all(|c| c.glyph != '█'));
        // Empty fill: caps + an all-dim thin track.
        let empty = render_bar(10, 0.0, 0.0);
        assert!(empty[1..9].iter().all(|c| c.glyph == TRACK_GLYPH));
        assert!(empty[1..9].iter().all(|c| c.rgb == TRACK));
        // Partial fill: exactly filled_len(10, 0.35) gradient cells, then track.
        let cells = render_bar(10, 0.35, 0.0);
        assert_eq!(cells.len(), 10);
        let n = filled_len(10, 0.35);
        assert_eq!(n, 3, "round(0.35 * 8)");
        assert!(cells[1..1 + n].iter().all(|c| c.glyph == FILL_GLYPH));
        assert!(cells[1 + n..9].iter().all(|c| c.glyph == TRACK_GLYPH));
    }

    #[test]
    fn sweep_frames_vary_over_time() {
        // The travelling tip must repaint colours as time advances
        // (this is what the forced-TTY capture checks on the wire).
        let a = render_bar(40, 0.4, 0.0);
        let b = render_bar(40, 0.4, 0.4);
        let c = render_bar(40, 0.4, 1.2);
        assert_ne!(a, b, "frames 0.0s vs 0.4s must differ");
        assert_ne!(b, c, "frames 0.4s vs 1.2s must differ");
        // ... while geometry (glyphs) stays stable for a fixed fill.
        let glyphs = |v: Vec<Cell>| v.into_iter().map(|c| c.glyph).collect::<String>();
        assert_eq!(glyphs(a), glyphs(b));
    }

    #[test]
    fn spinner_cycles_every_frame() {
        assert_eq!(spinner_at(0), "⠋");
        assert_eq!(spinner_at(79), "⠋");
        assert_eq!(spinner_at(80), "⠙");
        let all: Vec<_> = (0..10).map(|i| spinner_at(i * 80)).collect();
        assert_eq!(all, SPINNER);
        assert_eq!(spinner_at(800), spinner_at(0)); // wraps
    }

    // --- Thin rounded single-row bar (nerd-font terminals) ---

    #[test]
    fn thin_bar_is_single_row_with_rounded_caps() {
        // Exactly 1 terminal row tall, by construction.
        assert_eq!(BAR_HEIGHT, 1);
        for w in [2, 5, 40, 80] {
            for fill in [0.0, 0.4, 1.0] {
                let cells = render_bar(w, fill, 0.7);
                assert_eq!(cells.len(), w, "single row of exactly w={w} cells");
                assert_eq!(cells[0].glyph, LEFT_CAP, "rounded left cap w={w}");
                assert_eq!(cells[w - 1].glyph, RIGHT_CAP, "rounded right cap w={w}");
            }
        }
    }

    #[test]
    fn thin_bar_caps_carry_exact_gradient_endpoints() {
        // Caps are structural (excluded from the travelling-tip boost) so
        // they always read the exact spec endpoints: blue → … → peach.
        for t in [0.0, 0.4, 1.2, 2.9] {
            let cells = render_bar(40, 0.4, t);
            assert_eq!(cells[0].rgb, BLUE, "left cap == gradient_at(0) t={t}");
            assert_eq!(cells[0].rgb, (0x5c, 0x9c, 0xf5));
            assert_eq!(cells[39].rgb, PEACH, "right cap == gradient_at(1) t={t}");
            assert_eq!(cells[39].rgb, (0xfa, 0xb2, 0x83));
        }
    }

    #[test]
    fn no_full_block_anywhere_in_thin_render() {
        // The thick-block era is over: no '█' may leave the new path.
        for w in [0, 1, 2, 5, 40, 80] {
            for fill in [0.0, 0.13, 0.35, 0.5, 0.87, 1.0] {
                for t in [0.0, 0.4, 1.2, 2.5] {
                    let cells = render_bar(w, fill, t);
                    assert!(
                        cells.iter().all(|c| c.glyph != '█'),
                        "w={w} fill={fill} t={t}: {cells:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn thin_fill_length_proportional() {
        // Filled inner-cell count grows monotonically with fill and hits
        // both extremes exactly.
        assert_eq!(filled_len(40, 0.0), 0);
        assert_eq!(filled_len(40, 1.0), 38); // inner = width - 2 caps
        let mut last = 0;
        for i in 0..=20 {
            let n = filled_len(40, i as f64 / 20.0);
            assert!(n >= last, "i={i} n={n}");
            last = n;
        }
        // Half fill ≈ half the inner run (rounding ±1).
        let half = filled_len(40, 0.5);
        assert!((half as i32 - 19).abs() <= 1, "half={half}");
        // Rendered gradient cells agree with filled_len when the tip is
        // parked off-bar (elapsed 0.0 → tip at x=-8, boost rounds to 0).
        for fill in [0.0, 0.35, 0.5, 1.0] {
            let cells = render_bar(10, fill, 0.0);
            let inner = &cells[1..9];
            let n_track = inner.iter().filter(|c| c.rgb == TRACK).count();
            assert_eq!(inner.len() - n_track, filled_len(10, fill), "fill={fill}");
        }
    }

    #[test]
    fn thin_track_is_dim_and_fill_uses_spec_gradient() {
        // Unfilled inner cells are the dim thin track; filled ones ride
        // the EXISTING blue → purple → peach gradient (endpoints exact).
        let cells = render_bar(11, 0.0, 0.0); // inner = 9, none filled
        assert!(cells[1..10].iter().all(|c| c.glyph == TRACK_GLYPH));
        assert!(cells[1..10].iter().all(|c| c.rgb == TRACK));
        let full = render_bar(11, 1.0, 0.0);
        assert_eq!(full[1].rgb, BLUE);
        assert_eq!(full[9].rgb, PEACH);
        let mid = render_bar(11, 1.0, 0.0)[5].rgb;
        assert!(mid.0 > BLUE.0 && mid.0 < PEACH.0, "mid={mid:?}");
    }

    #[test]
    fn thin_tip_still_travels_without_block_cursor() {
        // Travelling tip preserved as pure brightness: frames differ in
        // colour while glyphs stay stable (no full-block cursor anywhere).
        let a = render_bar(40, 0.4, 0.0);
        let b = render_bar(40, 0.4, 0.4);
        assert_ne!(a, b, "tip must repaint colours over time");
        let glyphs = |v: Vec<Cell>| v.into_iter().map(|c| c.glyph).collect::<String>();
        assert_eq!(glyphs(a.clone()), glyphs(b));
        // Peak brightness travels: argmax rgb-sum moves with time.
        let peak = |v: Vec<Cell>| {
            v.iter()
                .enumerate()
                .max_by_key(|(_, c)| c.rgb.0 as u32 + c.rgb.1 as u32 + c.rgb.2 as u32)
                .map(|(i, _)| i)
                .unwrap()
        };
        let c = render_bar(40, 0.4, 1.2);
        assert_ne!(peak(a), peak(c), "bright tip cell must move");
    }
}
