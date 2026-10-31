#[cfg(test)]
mod audio_quality_tests {
    use super::*;
    const TAU: f64 = std::f64::consts::TAU;

    fn rms(samples: &[f64]) -> f64 {
        (samples.iter().map(|v| v * v).sum::<f64>() / samples.len() as f64).sqrt()
    }
    fn tone_amplitude(samples: &[f64], hz: f64) -> f64 {
        let (mut i, mut q) = (0.0, 0.0);
        for (n, sample) in samples.iter().enumerate() {
            let phase = TAU * hz * n as f64 / 44_100.0;
            i += sample * phase.cos(); q += sample * phase.sin();
        }
        2.0 * i.hypot(q) / samples.len() as f64
    }

    #[test]
    fn fractional_resampling_preserves_timing_and_rejects_aliases() {
        for rate in [62_015.625, 49_612.5] {
            let mut resampler = AudioResampler::new(rate, 44_100);
            let mut count = 0;
            for _ in 0..(rate * 8.0) as usize {
                if let Some(pair) = resampler.push([0.25, -0.25]) {
                    count += 1;
                    if count > 100 { assert!((pair[0] - 0.25).abs() < 1e-5); }
                    assert!((pair[0] + pair[1]).abs() < 1e-6);
                }
            }
            assert!((count as i64 - 352_800).abs() <= 1, "rate={rate}, count={count}");
        }
        for hz in [1000.0, 15_000.0, 27_000.0] {
            let rate = 62_015.625;
            let mut resampler = AudioResampler::new(rate, 44_100);
            let mut output = Vec::new();
            for n in 0..(rate * 0.6) as usize {
                let value = (TAU * hz * n as f64 / rate).sin() as f32;
                if let Some(pair) = resampler.push([value, value]) { output.push(pair[0] as f64); }
            }
            let tail = &output[4410..];
            if hz < 20_000.0 {
                let amplitude = tone_amplitude(tail, hz);
                let residual = (rms(tail).powi(2) - amplitude.powi(2) / 2.0).max(0.0).sqrt();
                assert!((amplitude - 1.0).abs() < 0.015, "{hz} Hz amplitude={amplitude}");
                assert!(residual < 0.015, "{hz} Hz residual={residual}");
            } else { assert!(rms(tail) < 0.002, "alias RMS={}", rms(tail)); }
        }
    }

    #[test]
    fn limiter_is_linear_for_normal_audio_and_smooth_at_overload() {
        for n in -850..=850 {
            let x = n as f32 / 1000.0;
            assert_eq!(audio_soft_limit(x), x);
        }
        let mut previous = 0.0;
        for n in 0..10_000 {
            let x = n as f32 / 1000.0;
            let y = audio_soft_limit(x);
            assert!(y >= previous && y < 1.0);
            assert!((audio_soft_limit(-x) + y).abs() < 1e-6);
            assert!(y - previous <= 0.00101);
            previous = y;
        }
    }

    #[test]
    fn fm_preserves_quiet_audio_without_zero_crossing_gating() {
        let mut demod = FmDemod::new();
        demod.set_stereo_enabled(false); demod.set_rds_enabled(false);
        let (mut phase, mut output) = (0.0_f64, Vec::new());
        for n in 0..744_188 {
            phase += TAU * 300.0 * (TAU * 1000.0 * n as f64 / 1_488_375.0).sin() / 1_488_375.0;
            if let Some(pair) = demod.push_complex(phase.cos() as f32, phase.sin() as f32) {
                assert_eq!(pair[0], pair[1]); output.push(pair[0] as f64);
            }
        }
        let tail = &output[4410..];
        let fundamental = tone_amplitude(tail, 1000.0);
        assert!(fundamental > 60.0, "quiet tone amplitude={fundamental}");
        assert!(tone_amplitude(tail, 3000.0) / fundamental < 0.02);
    }

    #[test]
    fn stereo_blend_rejects_noise_and_recovers_without_chattering() {
        let mut blend = StereoNoiseBlend::new(248_062.5, 62_015.625);
        let mut gain = 0.0;
        for n in 0..248_063 {
            let t = n as f64 / 248_062.5;
            let music = 0.45 * (TAU * 15_000.0 * t).sin();
            let mpx = music + music * (TAU * 38_000.0 * t).sin() + 0.1 * (TAU * 19_000.0 * t).sin();
            blend.observe(mpx as f32);
            if n % 4 == 0 { gain = blend.push(1.0); }
        }
        assert!(gain > 0.95, "clean pilot gain={gain}");
        for n in 0..124_032 {
            blend.observe((0.3 * (TAU * 80_000.0 * n as f64 / 248_062.5).sin()) as f32);
            if n % 4 == 0 {
                let next = blend.push(1.0);
                assert!((next - gain).abs() < 0.001);
                gain = next;
            }
        }
        assert!(gain < 0.01, "noisy stereo gain={gain}");
        for n in 0..248_063 { blend.observe(0.0); if n % 4 == 0 { gain = blend.push(1.0); } }
        assert!(gain > 0.95);
    }

    #[test]
    fn audio_filters_reject_pilot_and_out_of_band_hiss() {
        for (rate, taps, cutoff, stop) in [(248_062.5, AUDIO_TAPS, 15_000.0, 19_000.0), (49_612.5, 127, 3000.0, 5000.0)] {
            let kernel = lowpass_kernel(taps, cutoff / rate);
            let response = |hz: f64| {
                let (mut i, mut q) = (0.0, 0.0);
                for (n, tap) in kernel.iter().enumerate() {
                    let phase = TAU * hz * n as f64 / rate as f64;
                    i += *tap as f64 * phase.cos(); q += *tap as f64 * phase.sin();
                }
                i.hypot(q)
            };
            assert!(response(1000.0) > 0.99);
            assert!(response(stop) < 0.005, "{stop} Hz response={}", response(stop));
        }
    }

    #[test]
    fn noaa_removes_tuning_offset_from_listening_but_not_decoder_feed() {
        let mut demod = WeatherDemod::new();
        let (mut phase, mut output, mut decoder) = (0.0_f64, Vec::new(), Vec::new());
        for n in 0..744_188 {
            let deviation = 1000.0 + 2500.0 * (TAU * 1000.0 * n as f64 / 1_488_375.0).sin();
            phase += TAU * deviation / 1_488_375.0;
            if let Some(pair) = demod.push(phase.cos() as f32, phase.sin() as f32) {
                assert_eq!(pair[0], pair[1]); output.push(pair[0] as f64);
                decoder.push(demod.decoder_sample as f64);
            }
        }
        let tail = &output[8820..];
        let mean = tail.iter().sum::<f64>() / tail.len() as f64;
        assert!(mean.abs() < 30.0, "DC={mean}");
        assert!(tone_amplitude(tail, 1000.0) > 8000.0);
        assert!(decoder[8820..].iter().sum::<f64>() / decoder[8820..].len() as f64 > 15_000.0);
    }

    #[test]
    fn noaa_decoder_preserves_both_same_fsk_tones() {
        for hz in [1562.5, 6250.0 / 3.0] {
            let mut demod = WeatherDemod::new();
            let (mut phase, mut output) = (0.0_f64, Vec::new());
            for n in 0..446_513 {
                phase += TAU * 2500.0 * (TAU * hz * n as f64 / 1_488_375.0).sin() / 1_488_375.0;
                if demod.push(phase.cos() as f32, phase.sin() as f32).is_some() {
                    output.push(demod.decoder_sample as f64);
                }
            }
            let amplitude = tone_amplitude(&output[4410..], hz);
            assert!(amplitude > 4000.0, "SAME {hz} Hz amplitude={amplitude}");
        }
    }
}
