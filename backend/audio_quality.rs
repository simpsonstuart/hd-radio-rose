// Shared listening-path DSP. No dependencies and no per-sample allocations.
// Upstream's integer clock remains available for legacy/test callers.
const RESAMPLE_TAPS: usize = 48;
const RESAMPLE_PHASES: usize = 256;

/// Band-limited fractional-delay resampling; both stereo channels share a clock.
/// Inputs here are already audio-band limited and always faster than playback.
struct AudioResampler {
    history: [[f32; 2]; RESAMPLE_TAPS],
    kernels: Vec<[f32; RESAMPLE_TAPS]>,
    cursor: usize,
    phase: f64,
    step: f64,
}
impl AudioResampler {
    fn new(input_rate: f64, output_rate: u32) -> Self {
        assert!(input_rate.is_finite() && input_rate >= output_rate as f64 && output_rate > 0);
        let step = output_rate as f64 / input_rate;
        let cutoff = 0.45 * step;
        let kernels = (0..=RESAMPLE_PHASES).map(|p| {
            let mut taps = [0.0; RESAMPLE_TAPS];
            let delay = (RESAMPLE_TAPS / 2 - 1) as f64 + p as f64 / RESAMPLE_PHASES as f64;
            for (k, tap) in taps.iter_mut().enumerate() {
                let x = k as f64 - delay;
                let sinc = if x.abs() < 1e-12 { 2.0 * cutoff }
                    else { (std::f64::consts::TAU * cutoff * x).sin() / (std::f64::consts::PI * x) };
                let window = 0.42 - 0.5 * (std::f64::consts::TAU * k as f64 / (RESAMPLE_TAPS - 1) as f64).cos()
                    + 0.08 * (2.0 * std::f64::consts::TAU * k as f64 / (RESAMPLE_TAPS - 1) as f64).cos();
                *tap = (sinc * window) as f32;
            }
            let sum: f32 = taps.iter().sum();
            for tap in &mut taps { *tap /= sum; }
            taps
        }).collect();
        Self { history: [[0.0; 2]; RESAMPLE_TAPS], kernels, cursor: 0, phase: 0.0, step }
    }
    fn push(&mut self, sample: [f32; 2]) -> Option<[f32; 2]> {
        self.history[self.cursor] = sample;
        self.cursor = (self.cursor + 1) % RESAMPLE_TAPS;
        self.phase += self.step;
        if self.phase < 1.0 { return None; }
        self.phase -= 1.0;
        let position = (self.phase / self.step * RESAMPLE_PHASES as f64).clamp(0.0, RESAMPLE_PHASES as f64);
        let index = (position as usize).min(RESAMPLE_PHASES - 1);
        let fraction = (position - index as f64) as f32;
        let mut result = [0.0; 2];
        for k in 0..RESAMPLE_TAPS {
            let weight = self.kernels[index][k] + fraction * (self.kernels[index + 1][k] - self.kernels[index][k]);
            let input = self.history[(self.cursor + RESAMPLE_TAPS - 1 - k) % RESAMPLE_TAPS];
            result[0] += weight * input[0]; result[1] += weight * input[1];
        }
        Some(result)
    }
}

struct AudioDcBlocker { previous: f32, output: f32, pole: f32 }
impl AudioDcBlocker {
    fn new(rate: f32, cutoff: f32) -> Self {
        Self { previous: 0.0, output: 0.0, pole: (-2.0 * PI * cutoff / rate).exp() }
    }
    fn push(&mut self, input: f32) -> f32 {
        self.output = input - self.previous + self.pole * self.output;
        self.previous = input;
        self.output
    }
}

/// Unity gain below the knee: unlike x/(1+abs(x)), quiet audio is not compressed.
fn audio_soft_limit(input: f32) -> f32 {
    const KNEE: f32 = 0.85;
    let excess = input.abs() - KNEE;
    if excess <= 0.0 { input }
    else { input.signum() * (KNEE + (1.0 - KNEE) * excess / (excess + 1.0 - KNEE)) }
}

/// Measure discriminator noise above the stereo/RDS multiplex, not audio level.
/// Smooth stereo width quickly toward mono and slowly back to stereo.
struct StereoNoiseBlend {
    filter: Biquad, filter2: Biquad, noise: f32, noise_alpha: f32,
    gain: f32, narrow_alpha: f32, widen_alpha: f32,
}
impl StereoNoiseBlend {
    fn new(mpx_rate: f32, audio_rate: f32) -> Self {
        Self { filter: Biquad::bandpass(80_000.0, 8.0, mpx_rate), noise: 0.0,
            filter2: Biquad::bandpass(80_000.0, 8.0, mpx_rate),
            noise_alpha: 1.0 - (-1.0 / (mpx_rate * 0.020)).exp(), gain: 0.0,
            narrow_alpha: 1.0 - (-1.0 / (audio_rate * 0.025)).exp(),
            widen_alpha: 1.0 - (-1.0 / (audio_rate * 0.250)).exp() }
    }
    fn observe(&mut self, mpx: f32) {
        let noise = self.filter2.process(self.filter.process(mpx));
        self.noise += self.noise_alpha * (noise * noise - self.noise);
    }
    fn push(&mut self, pilot_gain: f32) -> f32 {
        // Conservative thresholds in normalized (+/-75 kHz deviation) MPX units.
        let quieting = ((0.0025 - self.noise) / (0.0025 - 0.0001)).clamp(0.0, 1.0);
        let target = pilot_gain * quieting;
        let alpha = if target < self.gain { self.narrow_alpha } else { self.widen_alpha };
        self.gain += alpha * (target - self.gain);
        self.gain
    }
}
