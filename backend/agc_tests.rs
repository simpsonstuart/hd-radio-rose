#[cfg(test)]
mod rose_gain_tests {
    use super::*;
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
