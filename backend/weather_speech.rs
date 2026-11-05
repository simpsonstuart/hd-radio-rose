//! NOAA mono speech enhancement on the listening path. RNNoise runs at 48 kHz.
use std::ffi::c_void;
use std::ptr::NonNull;

#[cfg_attr(target_os = "linux", link(name = "rnnoise", kind = "static"))]
#[cfg_attr(not(target_os = "linux"), link(name = "rnnoise"))]
extern "C" {
    fn rnnoise_create(model: *mut c_void) -> *mut c_void;
    fn rnnoise_destroy(state: *mut c_void);
    fn rnnoise_process_frame(state: *mut c_void, output: *mut f32, input: *const f32) -> f32;
}

const MODEL_FRAME: usize = 480;
const RADIO_FRAME: usize = 441;

pub struct WeatherSpeech {
    model: NonNull<c_void>,
    previous_input: [f32; MODEL_FRAME],
    bass: f32,
    band: f32,
    blend: f32,
    hold: u8,
}

impl WeatherSpeech {
    pub fn new() -> Self {
        // The pinned library supplies its original model when passed NULL.
        let model = unsafe { rnnoise_create(std::ptr::null_mut()) };
        Self { model: NonNull::new(model).expect("allocate RNNoise state"),
               previous_input: [0.0; MODEL_FRAME], bass: 0.0, band: 0.0, blend: 0.0, hold: 0 }
    }

    pub fn process(&mut self, pcm: &mut [u8]) {
        for frame in pcm.chunks_exact_mut(RADIO_FRAME * 4) {
            // NOAA is already band-limited to 3 kHz and identical in both channels.
            let mut radio = [0.0; RADIO_FRAME];
            for (level, stereo) in radio.iter_mut().zip(frame.chunks_exact(4)) {
                *level = i16::from_le_bytes([stereo[0], stereo[1]]) as f32;
            }
            let mut input = [0.0; MODEL_FRAME];
            resample(&radio, &mut input);
            let mut clean = [0.0; MODEL_FRAME];
            // Both arrays contain exactly one 10 ms frame. Only this PCM worker
            // owns the model, from construction through Drop.
            unsafe { rnnoise_process_frame(self.model.as_ptr(), clean.as_mut_ptr(), input.as_ptr()); }
            // The model emits the preceding frame. Align the reference before
            // blending speech-band residual into softly spoken phrase endings.
            self.restore_endings(&mut clean);
            self.previous_input = input;
            resample(&clean, &mut radio);
            for (&level, stereo) in radio.iter().zip(frame.chunks_exact_mut(4)) {
                let sample = (level.round() as i16).to_le_bytes();
                stereo[..2].copy_from_slice(&sample);
                stereo[2..].copy_from_slice(&sample);
            }
        }
    }

    fn restore_endings(&mut self, clean: &mut [f32; MODEL_FRAME]) {
        let power = clean.iter().map(|x| x*x).sum::<f32>() / (MODEL_FRAME as f32 * 32768.0_f32.powi(2));
        let db = 10.0 * power.max(1e-16).log10();
        if db >= -35.0 { self.hold = 45; }
        let quiet = ((-25.0 - db) / 18.0).clamp(0.0, 1.0);
        let target = quiet * if self.hold > 0 { 0.32 } else { 0.06 };
        self.hold = self.hold.saturating_sub(1);
        let seconds = if target > self.blend { 0.020 } else { 0.075 };
        let smoothing = 1.0 - (-1.0_f32 / (48000.0 * seconds)).exp();
        let bass_alpha = 1.0 - (-std::f32::consts::TAU * 280.0 / 48000.0).exp();
        let band_alpha = 1.0 - (-std::f32::consts::TAU * 3200.0 / 48000.0).exp();
        for (&reference, sample) in self.previous_input.iter().zip(clean) {
            let residual = reference - *sample;
            self.bass += bass_alpha * (residual - self.bass);
            self.band += band_alpha * (residual - self.bass - self.band);
            self.blend += smoothing * (target - self.blend);
            *sample += self.blend * self.band;
        }
    }
}

fn resample(input: &[f32], output: &mut [f32]) {
    let step = input.len() as f32 / output.len() as f32;
    for (i, sample) in output.iter_mut().enumerate() {
        let pos = i as f32 * step;
        let left = pos as usize;
        let right = (left + 1).min(input.len() - 1);
        *sample = input[left] + (pos - left as f32) * (input[right] - input[left]);
    }
}

impl Drop for WeatherSpeech {
    fn drop(&mut self) {
        unsafe { rnnoise_destroy(self.model.as_ptr()); }
    }
}
