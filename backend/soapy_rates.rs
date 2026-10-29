// Hardware capture rates differ from the decoder's exact 1,488,375 sps.
// HackRF: https://hackrf.readthedocs.io/en/stable/sampling_rate.html
fn capture_rate_plan(driver: &str, target: f64) -> (f64, Option<(f64, f64)>) {
    let minimum = match driver {
        "hackrf" => 8_000_000.0,
        "sdrplay" => 2_000_000.0,
        _ => target,
    };
    let capture = target.max(minimum);
    (capture, if capture == target { None } else { Some((capture, target)) })
}

#[cfg(test)]
mod rose_capture_tests {
    use super::*;

    #[test]
    fn device_rates_preserve_decoder_clock() {
        assert_eq!(capture_rate_plan("hackrf", 1_488_375.0),
                   (8_000_000.0, Some((8_000_000.0, 1_488_375.0))));
        assert_eq!(capture_rate_plan("rtlsdr", 1_488_375.0), (1_488_375.0, None));
        assert_eq!(capture_rate_plan("sdrplay", 1_488_375.0),
                   (2_000_000.0, Some((2_000_000.0, 1_488_375.0))));
    }

    #[test]
    fn hackrf_resampling_preserves_rate_and_dc_across_partial_reads() {
        let mut resampler = IqResampler::new(8_000_000.0, 1_488_375.0).unwrap();
        let input = vec![Complex::new(0.25_f32, -0.25_f32); 800_000];
        let mut output = Vec::new();
        for chunk in input.chunks(12345) { resampler.feed(chunk, &mut output); }
        // 0.1 s of input yields about 148,838 output pairs; allow one
        // buffered input chunk and filter startup delay.
        let frames = output.len() / 2;
        assert!((146_000..150_000).contains(&frames), "{frames} frames");
        for pair in output[4096..].chunks_exact(2) {
            assert!((pair[0] as i16 - 159).abs() <= 2);
            assert!((pair[1] as i16 - 96).abs() <= 2);
        }
    }
}
