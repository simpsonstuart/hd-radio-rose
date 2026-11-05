//! Identify a persistent narrow audio peak before applying a stereo peaking cut.
const RATE: f32 = 44_100.0;
const N: usize = 2048;
const BINS: usize = 248;
const FIRST: usize = 28; // 603 Hz
const LAST: usize = 240; // 5168 Hz
const HISTORY: usize = 18;

pub struct Detector {
    window: Vec<f32>,
    coeff: Vec<f32>,
    samples: Vec<f32>,
    skip: usize,
    hits: [[bool; BINS]; HISTORY],
    slot: usize,
    center: usize,
    filter: Cut,
    previous: Cut,
    fade: f32,
}

impl Detector {
    pub fn new() -> Self {
        let angle = std::f32::consts::TAU / N as f32;
        Self {
            window: (0..N).map(|i| 0.5 - 0.5 * (angle * i as f32).cos()).collect(),
            coeff: (0..BINS).map(|i| 2.0 * (angle * i as f32).cos()).collect(),
            samples: Vec::with_capacity(N), skip: 0, hits: [[false; BINS]; HISTORY],
            slot: 0, center: 0, filter: Cut::default(), previous: Cut::default(), fade: 1.0,
        }
    }

    pub fn frequency(&self) -> u32 { (self.center as f32 * RATE / N as f32).round() as u32 }

    pub fn reset(&mut self) {
        self.samples.clear();
        self.skip = 0;
        self.hits.fill([false; BINS]);
        self.center = 0;
        self.filter = Cut::default();
        self.previous = Cut::default();
        self.fade = 1.0;
    }

    pub fn process(&mut self, pcm: &mut [u8]) {
        for frame in pcm.chunks_exact_mut(4) {
            let pair = [i16::from_le_bytes([frame[0], frame[1]]) as f32,
                        i16::from_le_bytes([frame[2], frame[3]]) as f32];
            if self.skip > 0 {
                self.skip -= 1;
            } else {
                self.samples.push((pair[0] + pair[1]) * 0.5 * self.window[self.samples.len()]);
                if self.samples.len() == N {
                    self.analyze();
                    self.samples.clear();
                    self.skip = RATE as usize / 5 - N;
                }
            }
            if self.center == 0 && self.fade >= 1.0 { continue; }
            for (channel, sample) in pair.into_iter().enumerate() {
                let mut y = self.filter.push(channel, sample);
                if self.fade < 1.0 {
                    let old = self.previous.push(channel, sample);
                    y = old + self.fade * (y - old);
                }
                frame[channel*2..channel*2+2].copy_from_slice(&(y.round() as i16).to_le_bytes());
            }
            self.fade = (self.fade + 1.0 / 882.0).min(1.0);
        }
    }

    fn analyze(&mut self) {
        // Five Hann-windowed Goertzel measurements per second. Compare each bin
        // with nearby shoulders, then vote across time instead of chasing vowels.
        let mut power = [0.0; BINS];
        for i in FIRST-7..=LAST+7 {
            let (mut a, mut b) = (0.0, 0.0);
            for &x in &self.samples {
                let next = x + self.coeff[i] * a - b;
                b = a;
                a = next;
            }
            power[i] = (a*a + b*b - self.coeff[i]*a*b).max(0.0);
        }
        let row = &mut self.hits[self.slot];
        row.fill(false);
        for i in FIRST..=LAST {
            let shoulders: f32 = (4..=7).map(|d| power[i-d] + power[i+d]).sum::<f32>() / 8.0;
            // The absolute floor rejects numerical noise in near-silent samples.
            let peak = power[i] * 0.5 + (power[i-1] + power[i+1]) * 0.25;
            row[i] = peak > 2.8 * shoulders && peak > (N as f32 * 3.0).powi(2);
        }
        self.slot = (self.slot + 1) % HISTORY;
        let votes = |bin: usize| self.hits.iter().filter(|row| row[bin]).count();
        let mut best = self.center;
        let mut score = if best == 0 { 0 } else { votes(best) };
        for bin in FIRST..=LAST {
            let v = votes(bin);
            let stronger_tie = v == score && best != self.center && power[bin] > power[best];
            if v >= 12 && (v > score || stronger_tie) { best = bin; score = v; }
        }
        if score < 6 { best = 0; }
        if best != self.center {
            self.center = best;
            self.previous = self.filter;
            self.filter = Cut::new(self.frequency());
            self.fade = 0.0;
        }
    }
}

#[derive(Clone, Copy, Default)]
struct Cut {
    b0: f32, b1: f32, b2: f32, a2: f32,
    state: [[f32; 2]; 2],
    enabled: bool,
}

impl Cut {
    fn new(hz: u32) -> Self {
        if hz == 0 { return Self::default(); }
        let angle = std::f32::consts::TAU * hz as f32 / RATE;
        let amplitude = 10.0_f32.powf(-18.0 / 40.0);
        let alpha = angle.sin() / 32.0; // Q = 16, as in the listening trial.
        let a0 = 1.0 + alpha / amplitude;
        Self { b0: (1.0 + alpha*amplitude)/a0, b1: -2.0*angle.cos()/a0,
               b2: (1.0-alpha*amplitude)/a0, a2: (1.0-alpha/amplitude)/a0,
               state: [[0.0; 2]; 2], enabled: true }
    }
    fn push(&mut self, ch: usize, x: f32) -> f32 {
        if !self.enabled { return x; }
        let y = self.b0*x + self.state[ch][0];
        self.state[ch][0] = self.b1*(x-y) + self.state[ch][1];
        self.state[ch][1] = self.b2*x - self.a2*y;
        y
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(hz: f32, seconds: f32) -> Vec<u8> {
        let frames = (RATE * seconds) as usize;
        let mut audio = Vec::with_capacity(frames * 4);
        for n in 0..frames {
            let sample = (9000.0 * (std::f32::consts::TAU * hz * n as f32 / RATE).sin()) as i16;
            audio.extend(sample.to_le_bytes());
            audio.extend(0_i16.to_le_bytes()); // Verify no leakage into right channel.
        }
        audio
    }

    #[test]
    fn locks_only_after_persistence_and_releases_on_silence() {
        let mut detector = Detector::new();
        let mut brief = tone(1712.0, 1.0);
        let original = brief.clone();
        detector.process(&mut brief);
        assert_eq!(brief, original);
        let mut sustained = tone(1712.0, 4.0);
        detector.process(&mut sustained);
        assert!(detector.frequency().abs_diff(1712) < 25);
        for frame in sustained[sustained.len()-4410*4..].chunks_exact(4) {
            assert!(i16::from_le_bytes([frame[0], frame[1]]).unsigned_abs() < 2500);
            assert_eq!(frame[2..], [0, 0]);
        }
        detector.process(&mut vec![0; 4 * 44100 * 4]);
        assert_eq!(detector.frequency(), 0);
    }

    #[test]
    fn broadband_noise_stays_unfiltered() {
        let mut random = 1u32;
        let mut detector = Detector::new();
        for _ in 0..500 {
            let mut block = Vec::with_capacity(882 * 4);
            for _ in 0..882 {
                random = random.wrapping_mul(1664525).wrapping_add(1013904223);
                let sample = (random >> 18) as i16 - 8192;
                block.extend(sample.to_le_bytes());
                block.extend(sample.to_le_bytes());
            }
            let original = block.clone();
            detector.process(&mut block);
            assert_eq!(block, original);
        }
    }

    #[test]
    fn different_station_starts_unfiltered_then_learns_its_own_tone() {
        let mut detector = Detector::new();
        detector.process(&mut tone(1712.0, 4.0));
        assert!(detector.frequency().abs_diff(1712) < 25);
        detector.reset();
        let mut brief = tone(2950.0, 1.0);
        let original = brief.clone();
        detector.process(&mut brief);
        assert_eq!(brief, original);
        detector.process(&mut tone(2950.0, 4.0));
        assert!(detector.frequency().abs_diff(2950) < 25);
    }
}
