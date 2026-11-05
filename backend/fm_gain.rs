// Use demodulator noise upstream of stereo blend, high-cut and volume.
// Time bounds are wall-clock bounds so missing I/Q cannot look like quiet FM.
const FM_NOISY: f32 = 0.0025;
const FM_WINDOW: Duration = Duration::from_secs(3);
const FM_GAP: Duration = Duration::from_secs(1);

#[derive(Default)]
struct FmWindow {
    first: Option<Instant>,
    last: Option<Instant>,
    sum: f32,
    count: u32,
    noisy: u32,
    clipped: u32,
}

#[derive(Default)]
struct FmTracking {
    analog_only: bool,
    has_feedback: bool,
    last_hd: Option<Instant>,
    window: FmWindow,
    trial: Option<FmTrial>,
    prefer_down: bool,
    recheck_hd: bool,
}

#[derive(Clone, Copy)]
struct FmTrial {
    original: usize,
    noise: f32,
    clipped_fraction: f32,
}

impl AgcController {
    pub fn set_analog_only(&mut self, enabled: bool) {
        self.fm.analog_only = enabled;
    }

    pub fn observe_fm_noise(&mut self, power: f32, clipped: bool) {
        if self.phase != SearchPhase::Done || !power.is_finite() || power < 0.0 {
            return;
        }
        self.fm.has_feedback = true;
        let hold = if self.fm.trial.is_some() { GAIN_SETTLE } else { GAIN_COOLDOWN };
        if self.last_change_at.elapsed() < hold || self.hd_recent() { return; }
        let window = &mut self.fm.window;
        if window.last.is_some_and(|last| last.elapsed() > FM_GAP) {
            *window = FmWindow::default();
        }
        let now = Instant::now();
        window.first.get_or_insert(now);
        window.last = Some(now);
        window.sum += power;
        window.count += 1;
        window.noisy += u32::from(power >= FM_NOISY);
        window.clipped += u32::from(clipped);
    }

    fn hd_recent(&self) -> bool {
        !self.fm.analog_only && self.fm.last_hd.is_some_and(|t| t.elapsed() < QUALITY_WINDOW)
    }

    fn track_fm_gain(&mut self) -> Option<AgcAction> {
        let hd = self.hd_recent();
        if hd && self.fm.recheck_hd && self.fm.trial.is_none() {
            // Analog chose a different gain. Reacquire using fresh HD metrics.
            self.fm.recheck_hd = false;
            self.status = AgcStatus::Probing;
            self.phase = SearchPhase::Fine;
            self.explored.clear();
            self.best_gain_idx = self.gain_idx;
            self.best_mer_seen = f32::NEG_INFINITY;
            self.probes_without_improvement = 0;
            self.ema_mer_min = None;
            self.mer_samples_since_change = 0;
            self.last_change_at = Instant::now();
            self.tracking = HdTracking::default();
            self.last_reason = "HD returned; checking tuner gain".into();
            return None;
        }
        let window = &self.fm.window;
        let ready = window.count >= 8
            && window.last.is_some_and(|last| last.elapsed() <= FM_GAP)
            && window.first.zip(window.last).is_some_and(|(first,last)| last - first >= FM_WINDOW);
        if let Some(trial) = self.fm.trial {
            if !ready && !hd && self.last_change_at.elapsed() < QUALITY_WINDOW { return None; }
            self.fm.trial = None;
            let measured = std::mem::take(&mut self.fm.window);
            let noise = measured.sum / measured.count.max(1) as f32;
            let clipped = measured.clipped as f32 / measured.count.max(1) as f32;
            if ready && !hd && noise <= 0.75 * trial.noise && clipped <= trial.clipped_fraction {
                self.fm.recheck_hd = true;
                self.fm.prefer_down = self.gain_idx < trial.original;
                self.last_change_at = Instant::now();
                self.last_reason = format!("Analog FM gain kept: measured hiss fell {:.0}%",
                    100.0 * (1.0 - noise / trial.noise));
                return None;
            }
            self.fm.prefer_down = self.gain_idx > trial.original;
            return Some(self.tracking_step(trial.original, "Analog FM gain restored after trial".into()));
        }
        if hd || !ready { return None; }
        let measured = std::mem::take(&mut self.fm.window);
        if measured.noisy * 4 < measured.count * 3 { return None; }
        let clipped = measured.clipped as f32 / measured.count as f32;
        let down = self.gain_idx.checked_sub(1);
        let up = (self.gain_idx + 1 < self.table.len()).then_some(self.gain_idx + 1);
        let idx = if clipped > 0.0 { down }
            else if self.fm.prefer_down { down.or(up) } else { up.or(down) }?;
        self.fm.trial = Some(FmTrial {
            original: self.gain_idx, noise: measured.sum / measured.count as f32,
            clipped_fraction: clipped,
        });
        Some(self.tracking_step(idx, format!("Analog FM hiss: testing tuner gain {:.1} dB",
            self.table[idx] as f32 / 10.0)))
    }
}
