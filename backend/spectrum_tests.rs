#[cfg(test)]
mod rose_spectrum_tests {
    use super::*;

    #[test]
    fn retune_waits_for_one_full_window_of_new_iq() {
        let tap = SpectrumTap::new(1_488_375.0);
        let mut frame = SpectrumSnapshot::default();
        let half_window = vec![255; FFT_SIZE];
        for center in [101_100_000., 162_550_000., 462_562_500., 446_000_000., 1_000_000.] {
            tap.set_center_freq_hz(center);
            tap.feed(&half_window);
            tap.snapshot_into(&mut frame);
            assert_eq!(frame.center_freq_hz, center);
            assert!(frame.spectrum_db.is_empty());
            tap.feed(&half_window);
            tap.snapshot_into(&mut frame);
            assert_eq!(frame.spectrum_db.len(), FFT_SIZE);
            assert!(frame.spectrum_db[FFT_SIZE / 2] > DB_FLOOR);
        }
    }
}
