// After acquisition, compare one neighbouring hardware gain against a fresh
// baseline. P1 BER is a fraction of coded bits. Require repeated bad frames
// before disrupting reception to try another setting.
const HIGH_CBER: f32 = 0.01;
const GAIN_COOLDOWN: Duration = Duration::from_secs(30);
const QUALITY_WINDOW: Duration = Duration::from_secs(12);
const GAIN_SETTLE: Duration = Duration::from_secs(2);

#[derive(Default)]
struct HdTracking {
    window: QualityWindow,
    trial: Option<GainTrial>,
    try_up: bool,
}

#[derive(Default)]
struct QualityWindow {
    started: Option<Instant>,
    ber_sum: f32,
    ber_count: u32,
    high_count: u32,
    mer_sum: f32,
    mer_count: u32,
}

#[derive(Clone, Copy)]
struct HdQuality {
    ber: f32,
    mer: f32,
}

impl QualityWindow {
    fn quality(&self) -> Option<HdQuality> {
        if self.ber_count < 3 || self.mer_count < 3 ||
            self.started?.elapsed() > QUALITY_WINDOW {
            return None;
        }
        Some(HdQuality {
            ber: self.ber_sum / self.ber_count as f32,
            mer: self.mer_sum / self.mer_count as f32,
        })
    }
}

#[derive(Clone, Copy)]
struct GainTrial {
    original: usize,
    baseline: HdQuality,
    previous_status: AgcStatus,
}

impl AgcController {
    pub fn gain_write_failed(&mut self, action: &AgcAction) {
        self.status = AgcStatus::Bailed;
        self.phase = SearchPhase::Done;
        self.tracking = HdTracking::default();
        self.last_reason = format!("Automatic gain stopped: tuner rejected {:.1} dB", action.new_tenths as f32 / 10.0);
    }

    fn observe_hd_quality(&mut self, event: &NrscEvent) {
        let elapsed = self.last_change_at.elapsed();
        let delay = if self.tracking.trial.is_some() { GAIN_SETTLE } else { GAIN_COOLDOWN };
        if elapsed < delay {
            return;
        }
        let window = &mut self.tracking.window;
        if window.started.is_some_and(|start| start.elapsed() > QUALITY_WINDOW) {
            *window = QualityWindow::default();
        }
        match *event {
            NrscEvent::Ber { cber } if (0.0..=1.0).contains(&cber) => {
                window.ber_count += 1;
                window.high_count += u32::from(cber >= HIGH_CBER);
                window.ber_sum += cber;
            }
            NrscEvent::Mer { lower, upper } if lower.is_finite() && upper.is_finite() => {
                window.mer_count += 1;
                window.mer_sum += lower.min(upper);
            }
            NrscEvent::LostSync => {
                *window = QualityWindow::default();
                return;
            }
            _ => return,
        }
        window.started.get_or_insert_with(Instant::now);
    }

    fn tracking_step(&mut self, idx: usize, reason: String) -> AgcAction {
        self.gain_idx = idx;
        self.last_change_at = Instant::now();
        self.ema_mer_min = None;
        self.mer_samples_since_change = 0;
        self.tracking.window = QualityWindow::default();
        self.last_reason = reason.clone();
        AgcAction { new_idx: idx, new_tenths: self.table[idx], reason }
    }

    fn track_hd_gain(&mut self) -> Option<AgcAction> {
        let quality = self.tracking.window.quality();
        if let Some(trial) = self.tracking.trial {
            if quality.is_none() && self.last_change_at.elapsed() < GAIN_SETTLE + QUALITY_WINDOW {
                return None;
            }
            self.tracking.trial = None;
            self.phase = SearchPhase::Done;
            // Require a meaningful error reduction and guard the weaker HD
            // sideband against a large regression even if BER briefly falls.
            if let Some(q) = quality.filter(|q|
                q.ber <= trial.baseline.ber * 0.75 && q.mer >= trial.baseline.mer - 1.0
            ) {
                self.status = AgcStatus::Settled;
                self.best_mer_seen = q.mer;
                self.best_gain_idx = self.gain_idx;
                self.last_reason = format!("HD gain accepted: BER {:.2}% -> {:.2}%, weaker MER {:.1} dB",
                    trial.baseline.ber * 100.0, q.ber * 100.0, q.mer);
                self.last_change_at = Instant::now();
                self.tracking.window = QualityWindow::default();
                return None;
            }
            self.status = trial.previous_status;
            self.tracking.try_up = self.gain_idx < trial.original;
            return Some(self.tracking_step(trial.original, "HD gain restored: probe did not improve reception".into()));
        }

        let baseline = quality?;
        let persistently_high = self.tracking.window.high_count == self.tracking.window.ber_count;
        self.tracking.window = QualityWindow::default();
        if !persistently_high || self.table.len() < 2 {
            return None;
        }
        let down = self.gain_idx.checked_sub(1);
        let up = (self.gain_idx + 1 < self.table.len()).then_some(self.gain_idx + 1);
        let idx = if self.tracking.try_up { up.or(down) } else { down.or(up) }?;
        self.tracking.trial = Some(GainTrial {
            original: self.gain_idx, baseline, previous_status: self.status,
        });
        self.status = AgcStatus::Probing;
        self.phase = SearchPhase::Fine;
        Some(self.tracking_step(idx, format!("HD gain probe: BER {:.2}%, weaker MER {:.1} dB; trying {:.1} dB",
            baseline.ber * 100.0, baseline.mer, self.table[idx] as f32 / 10.0)))
    }
}
