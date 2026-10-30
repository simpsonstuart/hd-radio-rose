// Appended to fm_analog.rs to reuse its FIR and output clock primitives.
// Two IQ stages: 1,488,375 -> 99,225 -> 49,612.5 complex samples/s.
pub struct WeatherDemod {
    coarse: FirDecimator, channel: FirDecimator, audio: FirDecimator,
    previous: (f32, f32), clock: OutputRateResampler,
    deemphasis: DeemphasisFilter,
}
impl WeatherDemod {
    pub fn new() -> Self {
        Self {
            coarse: FirDecimator::new(lowpass_kernel(127, 25_000.0 / 1_488_375.0), 15),
            channel: FirDecimator::new(lowpass_kernel(127, 8_000.0 / 99_225.0), 2),
            audio: FirDecimator::new(lowpass_kernel(63, 3_000.0 / 49_612.5), 1),
            previous: (0.0, 0.0), clock: OutputRateResampler::new(49_612, 44_100),
            deemphasis: DeemphasisFilter::new(49_612, 750e-6),
        }
    }
    pub fn push(&mut self, i: f32, q: f32) -> Option<[i16; 2]> {
        let (i, q) = self.coarse.push(i, q)?;
        let (i, q) = self.channel.push(i, q)?;
        let delta = phase_delta(self.previous.0, self.previous.1, i, q);
        self.previous = (i, q);
        let (voice, _) = self.audio.push(delta * (49_612.5 / (2.0 * PI * 5_000.0)), 0.0)?;
        let voice = self.deemphasis.push(voice);
        if !self.clock.should_emit() { return None; }
        let sample = (voice * 4.0 * 24_000.0).clamp(-32767.0, 32767.0) as i16;
        Some([sample, sample])
    }
}
#[cfg(test)]
mod weather_demod_tests {
    use super::*;
    #[test]
    fn demodulates_narrowband_tone_to_stereo_44100() {
        let mut demod = WeatherDemod::new();
        let mut phase = 0.0_f64;
        let mut output = Vec::new();
        for n in 0..744_188 {
            phase += 2.0 * std::f64::consts::PI * 2500.0 *
                (2.0 * std::f64::consts::PI * 1000.0 * n as f64 / 1_488_375.0).sin() / 1_488_375.0;
            if let Some(pair) = demod.push(phase.cos() as f32, phase.sin() as f32) {
                assert_eq!(pair[0], pair[1]); output.push(pair[0]);
            }
        }
        assert!((21_850..22_100).contains(&output.len()));
        let tail = &output[4410..];
        let crossings = tail.windows(2).filter(|p| p[0] <= 0 && p[1] > 0).count();
        assert!((390..410).contains(&crossings), "{crossings} positive crossings");
        assert!(tail.iter().any(|s| s.abs() > 1000));
    }
}
