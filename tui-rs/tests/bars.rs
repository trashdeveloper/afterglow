//! BAR SHOOTOUT contract (`src/bars.rs`): 10 progress-bar variants on the
//! SAME tick, so the user picks a winner. Standalone (std only); this
//! target includes the module by path and pins per-variant:
//! deterministic, animates, single-row, width ≤ W, no alt-screen.

#[path = "../src/bars.rs"]
mod bars;

fn strip_ansi(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            if matches!(chars.peek(), Some('[')) {
                chars.next();
                for c2 in chars.by_ref() {
                    if c2.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        if c == '\r' {
            continue;
        }
        out.push(c);
    }
    out
}

fn visible_len(s: &str) -> usize {
    strip_ansi(s).chars().count()
}

fn variants() -> Vec<(&'static str, fn(usize, u64) -> String)> {
    vec![
        ("thin-rounded", bars::thin_rounded),
        ("thick-block", bars::thick_block),
        ("braille-sweep", bars::braille_sweep),
        ("shimmer-text", bars::shimmer_text),
        ("tab-pulse", bars::tab_pulse),
        ("rich-classic", bars::rich_classic),
        ("spinner-track", bars::spinner_track),
        ("chevron", bars::chevron),
        ("eighths", bars::eighths),
        ("firefly", bars::firefly),
    ]
}

#[test]
fn all_ten_variants_exist() {
    assert_eq!(variants().len(), 10, "shootout needs exactly 10 variants");
}

#[test]
fn deterministic_same_tick_twice_identical() {
    for (name, f) in variants() {
        for (w, tick) in [(24usize, 7u64), (80, 123), (10, 0), (1, 999)] {
            assert_eq!(f(w, tick), f(w, tick), "{name}: same (W={w}, tick={tick}) must be byte-identical");
        }
    }
}

#[test]
fn animates_ticks_differ() {
    for (name, f) in variants() {
        let frames: Vec<String> = (0..16u64).map(|t| f(24, t)).collect();
        let mut v = frames.clone();
        v.sort();
        v.dedup();
        assert!(v.len() >= 2, "{name}: ticks 0..16 must animate");
    }
}

#[test]
fn single_row_no_newline() {
    for (name, f) in variants() {
        for w in [0, 1, 2, 3, 5, 10, 24, 40, 80] {
            for tick in [0, 7, 123] {
                let s = f(w, tick);
                assert!(!s.contains('\n'), "{name}: single row (W={w} tick={tick}): {s:?}");
            }
        }
    }
}

#[test]
fn visible_width_within_w() {
    for (name, f) in variants() {
        for w in [0, 1, 2, 3, 5, 10, 24, 40, 80] {
            for tick in [0, 7, 123] {
                let s = f(w, tick);
                let n = visible_len(&s);
                assert!(n <= w, "{name}: width {n} exceeds W={w} (tick={tick}): {s:?}");
            }
        }
    }
}

#[test]
fn no_alternate_screen_sequences() {
    for (name, f) in variants() {
        for tick in [0, 7, 123] {
            let s = f(40, tick);
            for forbidden in ["1049h", "1049l"] {
                assert!(!s.contains(forbidden), "{name}: inline-only (tick={tick})");
            }
        }
    }
}
