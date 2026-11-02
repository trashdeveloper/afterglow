//! BAR SHOOTOUT: 10 progress-bar variants on the SAME tick.
//! Every fn: `fn(W: usize, tick: u64) -> String`, single terminal row,
//! nerd-font glyphs allowed, inline-only (no alt-screen).

const BLUE: (u8, u8, u8) = (0x5c, 0x9c, 0xf5);
const PURPLE: (u8, u8, u8) = (0x9d, 0x7c, 0xd8);
const PEACH: (u8, u8, u8) = (0xfa, 0xb2, 0x83);
const BRIGHT: (u8, u8, u8) = (0xff, 0xc0, 0x9f);
const DIM: (u8, u8, u8) = (0x3a, 0x3f, 0x4a);

fn fg(c: (u8, u8, u8)) -> String {
    format!("\x1b[38;2;{};{};{}m", c.0, c.1, c.2)
}
const RESET: &str = "\x1b[0m";

fn lerp(a: (u8, u8, u8), b: (u8, u8, u8), t: f64) -> (u8, u8, u8) {
    let m = |x: u8, y: u8| (x as f64 + (y as f64 - x as f64) * t).round() as u8;
    (m(a.0, b.0), m(a.1, b.1), m(a.2, b.2))
}

fn grad(t: f64) -> (u8, u8, u8) {
    let t = t.clamp(0.0, 1.0);
    if t < 0.5 {
        lerp(BLUE, PURPLE, t * 2.0)
    } else {
        lerp(PURPLE, PEACH, (t - 0.5) * 2.0)
    }
}

fn paint(c: (u8, u8, u8), ch: char) -> String {
    format!("{}{}{}", fg(c), ch, RESET)
}

/// 1. thin-rounded: nerd-font caps, thin track, gradient tip.
pub fn thin_rounded(w: usize, tick: u64) -> String {
    if w == 0 {
        return String::new();
    }
    if w <= 2 {
        return "─".repeat(w);
    }
    let inner = w - 2;
    let pos = (tick as usize * 7 + tick as usize / 8) % (inner * 2).max(1);
    let fwd = pos < inner;
    let head = if fwd { pos } else { inner * 2 - 1 - pos };
    let mut s = paint(BRIGHT, '\u{E0B6}');
    for i in 0..inner {
        if i == head {
            s.push_str(&paint(BRIGHT, '─'));
        } else {
            let d = head.abs_diff(i) as f64 / inner.max(1) as f64;
            let c = if d < 0.25 { grad(1.0 - d) } else { DIM };
            s.push_str(&paint(c, '─'));
        }
    }
    s.push_str(&paint(BRIGHT, '\u{E0B4}'));
    let _ = fwd;
    s
}

/// 2. thick-block: full-block gradient fill sweeping.
pub fn thick_block(w: usize, tick: u64) -> String {
    if w == 0 {
        return String::new();
    }
    let fill = ((tick as usize * 3) % (w * 2).max(1)).min(w);
    let mut s = String::new();
    for i in 0..w {
        if i < fill {
            s.push_str(&paint(grad(i as f64 / w.max(1) as f64), '█'));
        } else {
            s.push_str(&paint(DIM, '░'));
        }
    }
    s
}

const BRAILLE: [char; 9] = ['⠀', '⠁', '⠃', '⠇', '⠏', '⠟', '⠿', '⡿', '⣿'];

/// 3. braille-sweep: smooth braille wave, no track.
pub fn braille_sweep(w: usize, tick: u64) -> String {
    if w == 0 {
        return String::new();
    }
    let mut s = String::new();
    for i in 0..w {
        let ph = (tick as f64 * 0.6 - i as f64 * 0.9).sin() * 0.5 + 0.5;
        let idx = (ph * 8.0).round() as usize;
        let t = i as f64 / w.max(1) as f64;
        s.push_str(&paint(grad(t), BRAILLE[idx.min(8)]));
    }
    s
}

/// 4. shimmer-text: masked shimmer "Working…" sweep.
pub fn shimmer_text(w: usize, tick: u64) -> String {
    let label = "Working…";
    let n = label.chars().count();
    if w == 0 || w < n {
        return label.chars().take(w).collect();
    }
    let head = (tick as usize) % (n + 4);
    let mut s = String::new();
    for (i, ch) in label.chars().enumerate() {
        let lit = (i + n + 4 - head % (n + 4)) % (n + 4) < 3;
        let c = if lit { BRIGHT } else { DIM };
        s.push_str(&paint(c, ch));
    }
    while strip_len(&s) < w {
        s.push(' ');
    }
    s
}

fn strip_len(s: &str) -> usize {
    let mut n = 0;
    let mut it = s.chars().peekable();
    while let Some(c) = it.next() {
        if c == '\x1b' {
            if matches!(it.peek(), Some('[')) {
                it.next();
                for c2 in it.by_ref() {
                    if c2.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        n += 1;
    }
    n
}

/// 5. tab-pulse: opencode-style moving bright band over dim track.
pub fn tab_pulse(w: usize, tick: u64) -> String {
    if w == 0 {
        return String::new();
    }
    let band = 6.max(w / 6).min(w);
    let head = (tick as usize * 2) % (w + band);
    let mut s = String::new();
    for i in 0..w {
        let d = head as isize - i as isize;
        let c = if (0..band as isize).contains(&d) {
            let t = 1.0 - d as f64 / band as f64;
            grad(t)
        } else {
            DIM
        };
        s.push_str(&paint(c, '─'));
    }
    s
}

/// 6. rich-classic: heavy track with moving bright segment.
pub fn rich_classic(w: usize, tick: u64) -> String {
    if w == 0 {
        return String::new();
    }
    let seg = 4.max(w / 5).min(w);
    let head = (tick as usize * 2) % (w + seg);
    let mut s = String::new();
    for i in 0..w {
        let d = head as isize - i as isize;
        if (0..seg as isize).contains(&d) {
            s.push_str(&paint(BRIGHT, '━'));
        } else {
            s.push_str(&paint(DIM, '━'));
        }
    }
    s
}

const SPINNER: [char; 10] = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

/// 7. spinner-track: braille spinner + thin filling track.
pub fn spinner_track(w: usize, tick: u64) -> String {
    if w == 0 {
        return String::new();
    }
    let mut s = paint(BRIGHT, SPINNER[(tick as usize) % 10]);
    if w == 1 {
        return s;
    }
    s.push(' ');
    let inner = w.saturating_sub(2);
    let fill = (tick as usize * 2) % (inner + 1).max(1);
    for i in 0..inner {
        if i < fill {
            s.push_str(&paint(grad(i as f64 / inner.max(1) as f64), '─'));
        } else {
            s.push_str(&paint(DIM, '─'));
        }
    }
    s
}

/// 8. chevron: chase arrows over dim track.
pub fn chevron(w: usize, tick: u64) -> String {
    if w == 0 {
        return String::new();
    }
    let mut s = String::new();
    for i in 0..w {
        let ph = (tick as isize - i as isize).rem_euclid(6);
        if ph < 2 {
            s.push_str(&paint(grad(1.0 - ph as f64 / 2.0), '▶'));
        } else {
            s.push_str(&paint(DIM, '·'));
        }
    }
    s
}

const EIGHTHS: [char; 9] = [' ', '▏', '▎', '▍', '▌', '▋', '▊', '▉', '█'];

/// 9. eighths: fractional solid gradient fill.
pub fn eighths(w: usize, tick: u64) -> String {
    if w == 0 {
        return String::new();
    }
    let total = w * 8;
    let fill = (tick as usize * 5) % (total + 1);
    let mut s = String::new();
    for i in 0..w {
        let cell = fill.saturating_sub(i * 8).min(8);
        let t = i as f64 / w.max(1) as f64;
        s.push_str(&paint(if cell > 0 { grad(t) } else { DIM }, EIGHTHS[cell]));
    }
    s
}

/// 10. firefly: single bright dot with fading tail on dim track.
pub fn firefly(w: usize, tick: u64) -> String {
    if w == 0 {
        return String::new();
    }
    let head = (tick as usize * 3) % (w * 2).max(1);
    let fwd = head < w;
    let h = if fwd { head } else { w * 2 - 1 - head };
    let mut s = String::new();
    for i in 0..w {
        let d = h.abs_diff(i);
        let c = match d {
            0 => BRIGHT,
            1 => PEACH,
            2..=4 => grad(0.7 - d as f64 * 0.1),
            _ => DIM,
        };
        s.push_str(&paint(c, if d == 0 { '●' } else { '─' }));
    }
    let _ = fwd;
    s
}
