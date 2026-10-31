// Appended to fm_analog.rs to share the tested FIR and output clock.
// Envelope detection after channel filtering; no FM de-emphasis or RDS.
pub struct AmDemod {
    coarse: FirDecimator,
    channel: FirDecimator,
    audio: FirDecimator,
    clock: OutputRateResampler,
    carrier: f32,
}
impl AmDemod {
    pub fn new() -> Self {
        Self {
            coarse: FirDecimator::new(lowpass_kernel(127, 25_000.0 / 1_488_375.0), 15),
            channel: FirDecimator::new(lowpass_kernel(255, 4_500.0 / 99_225.0), 2),
            audio: FirDecimator::new(lowpass_kernel(127, 4_000.0 / 49_612.5), 1),
            clock: OutputRateResampler::new(49_612, 44_100), carrier: 0.0,
        }
    }
    pub fn push(&mut self, i: f32, q: f32) -> Option<[i16; 2]> {
        let (i, q) = self.coarse.push(i, q)?;
        let (i, q) = self.channel.push(i, q)?;
        let envelope = i.hypot(q);
        self.carrier += 0.0005 * (envelope - self.carrier);
        let voice = ((envelope - self.carrier) / self.carrier.max(0.02)).clamp(-2.0, 2.0);
        let (voice, _) = self.audio.push(voice, 0.0)?;
        if !self.clock.should_emit() { return None; }
        let sample = (voice * 20_000.0).clamp(-32767.0, 32767.0) as i16;
        Some([sample, sample])
    }
}

// Preserve the existing audio handoff and FM decoder interface.
pub enum BroadcastDemod { Fm(FmDemod), Am(AmDemod) }
impl BroadcastDemod {
    pub fn new(am: bool) -> Self { if am { Self::Am(AmDemod::new()) } else { Self::Fm(FmDemod::new()) } }
    pub fn set_stereo_enabled(&mut self, enabled: bool) { if let Self::Fm(d) = self { d.set_stereo_enabled(enabled); } }
    pub fn set_rds_enabled(&mut self, enabled: bool) { if let Self::Fm(d) = self { d.set_rds_enabled(enabled); } }
    pub fn push_complex(&mut self, i: f32, q: f32) -> Option<[i16;2]> {
        match self { Self::Fm(d) => d.push_complex(i,q), Self::Am(d) => d.push(i,q) }
    }
    pub fn take_rds_program_service(&mut self) -> Option<String> { match self { Self::Fm(d) => d.take_rds_program_service(), Self::Am(_) => None } }
    pub fn take_rds_radiotext(&mut self) -> Option<String> { match self { Self::Fm(d) => d.take_rds_radiotext(), Self::Am(_) => None } }
}

#[cfg(test)]
mod am_tests {
    use super::*;
    #[test]
    fn envelope_recovers_voice_and_rejects_adjacent_carrier() {
        let mut demod = AmDemod::new();
        let mut output = Vec::new();
        for n in 0..744_188 {
            let t = n as f64 / 1_488_375.0;
            let a = 0.3 * (1.0 + 0.5 * (std::f64::consts::TAU * 1000.0 * t).sin());
            let adjacent = std::f64::consts::TAU * 10000.0 * t;
            if let Some(pair) = demod.push((a + 0.3 * adjacent.cos()) as f32, (0.3 * adjacent.sin()) as f32) {
                assert_eq!(pair[0], pair[1]); output.push(pair[0] as f64);
            }
        }
        // All three FIR histories must fill before the output clock starts.
        let warmup = 127 + 255 * 15 + 127 * 30;
        let expected = ((744_188 - warmup) as f64 * 44_100.0 / 1_488_375.0).round() as i64;
        assert!((output.len() as i64 - expected).abs() <= 3, "{} frames", output.len());
        let tail = &output[8820..];
        let crossings = tail.windows(2).filter(|p| p[0] <= 0.0 && p[1] > 0.0).count();
        assert!((295..305).contains(&crossings), "{crossings}");
        let rms = (tail.iter().map(|s| s*s).sum::<f64>() / tail.len() as f64).sqrt();
        assert!((4000.0..9000.0).contains(&rms), "{rms}");
    }
}
