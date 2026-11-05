#[cfg(test)]
mod rose_gain_tests {
    use super::*;

    fn settled() -> AgcController {
        let mut agc = AgcController::new(&[0, 100, 200], AgcConfig {
            initial_tenths: 100, ..AgcConfig::default()
        });
        agc.status = AgcStatus::Settled;
        agc.phase = SearchPhase::Done;
        agc.last_change_at = Instant::now() - GAIN_COOLDOWN;
        agc
    }

    fn frame(agc: &mut AgcController, ber: f32, weaker_mer: f32) {
        agc.on_event(&NrscEvent::Mer { lower: 20.0, upper: weaker_mer });
        agc.on_event(&NrscEvent::Ber { cber: ber });
    }

    fn start_probe(agc: &mut AgcController) -> usize {
        for _ in 0..3 { frame(agc, 0.04, 8.0); }
        agc.tick().unwrap().new_idx
    }

    #[test]
    fn hd_tracking_accepts_lower_gain_only_after_fresh_measurements() {
        let mut agc = settled();
        assert_eq!(start_probe(&mut agc), 0);
        for _ in 0..3 { frame(&mut agc, 0.0, 18.0); }
        assert!(agc.tick().is_none());
        assert_eq!(agc.status, AgcStatus::Probing);
        agc.last_change_at = Instant::now() - GAIN_SETTLE;
        for _ in 0..3 { frame(&mut agc, 0.005, 9.0); }
        assert!(agc.tick().is_none());
        assert_eq!((agc.status, agc.best_gain_idx, agc.best_mer_seen), (AgcStatus::Settled, 0, 9.0));
        for _ in 0..3 { frame(&mut agc, 0.06, 5.0); }
        assert!(agc.tick().is_none());
    }

    #[test]
    fn lost_lock_restores_baseline_then_allows_opposite_direction() {
        let mut agc = settled();
        assert_eq!(start_probe(&mut agc), 0);
        agc.on_event(&NrscEvent::LostSync);
        agc.last_change_at = Instant::now() - GAIN_SETTLE - QUALITY_WINDOW;
        assert_eq!(agc.tick().unwrap().new_idx, 1);
        agc.last_change_at = Instant::now() - GAIN_COOLDOWN;
        assert_eq!(start_probe(&mut agc), 2);
        agc.last_change_at = Instant::now() - GAIN_SETTLE;
        for _ in 0..3 { frame(&mut agc, 0.001, 4.0); }
        assert_eq!(agc.tick().unwrap().new_idx, 1);
    }

    #[test]
    fn isolated_errors_and_missing_quality_leave_gain_alone() {
        let mut agc = settled();
        for ber in [0.0, 0.05, 0.0] { frame(&mut agc, ber, 7.0); }
        assert!(agc.tick().is_none());
        for _ in 0..4 { agc.on_event(&NrscEvent::Ber { cber: 0.08 }); }
        assert!(agc.tick().is_none());
        assert_eq!(agc.status, AgcStatus::Settled);
    }
    #[test]
    fn weak_signal_without_mer_keeps_amplitude_winner() {
        let table = &[0, 100, 200, 300, 400, 490];
        let cfg = AgcConfig {
            amp_enable: true, initial_tenths: 200,
            probe_period: Duration::ZERO,
            ..AgcConfig::default()
        };
        let mut agc = AgcController::new(table, cfg);
        for _ in 0..20 {
            let _ = agc.tick_amp(Some(-35.0));
            if agc.snapshot().phase == SearchPhase::MerQualityCheck { break; }
        }
        assert_eq!(agc.snapshot().current_tenths, 490);
        for _ in 0..100 { let _ = agc.tick(); }
        assert_eq!(agc.snapshot().current_tenths, 490,
            "No MER must not restore the old 20 dB startup gain");
    }
}
