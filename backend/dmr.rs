// Included in fm_analog.rs to reuse the receiver's FIR filters and sample clocks.
#[link(name = "rose_dmr")]
extern "C" {
    fn rose_dmr_new() -> *mut std::ffi::c_void;
    fn rose_dmr_delete(decoder: *mut std::ffi::c_void);
    fn rose_dmr_run(decoder: *mut std::ffi::c_void, input: *const i16, count: i32,
                    slot: i32, audio_count: &mut i32, synced: &mut bool) -> *const i16;
}

pub struct DmrDemod {
    coarse: FirDecimator,
    channel: FirDecimator,
    previous: (f32, f32),
    discriminator: AudioResampler,
    playback: AudioResampler,
    decoder: *mut std::ffi::c_void,
    input: Vec<i16>,
    slot: i32,
    pub synced: bool,
}

impl DmrDemod {
    pub fn new(slot: i32) -> Self {
        Self {
            coarse: FirDecimator::new(lowpass_kernel(127, 25_000.0 / 1_488_375.0), 15),
            channel: FirDecimator::new(lowpass_kernel(127, 6_250.0 / 99_225.0), 2),
            previous: (0.0, 0.0),
            discriminator: AudioResampler::new(49_612.5, 48_000),
            playback: AudioResampler::new(48_000.0, 44_100),
            decoder: unsafe { rose_dmr_new() },
            input: Vec::with_capacity(480),
            slot, synced: false,
        }
    }

    pub fn push(&mut self, i: f32, q: f32, audio: &mut Vec<i16>) {
        let Some((i, q)) = self.coarse.push(i, q) else { return };
        let Some((i, q)) = self.channel.push(i, q) else { return };
        let delta = phase_delta(self.previous.0, self.previous.1, i, q);
        self.previous = (i, q);
        // Feed the decoder a flat discriminator. Listening de-emphasis would
        // distort the four frequency levels used by DMR.
        let Some([sample, _]) = self.discriminator.push([delta, 0.0]) else { return };
        self.input.push((sample * 80_000.0) as i16);
        if self.input.len() < 480 { return; }
        let mut count = 0;
        // The output belongs to this decoder until the next run or drop.
        let pcm = unsafe { rose_dmr_run(self.decoder, self.input.as_ptr(), 480,
                                        self.slot, &mut count, &mut self.synced) };
        self.input.clear();
        if count > 0 {
            for &sample in unsafe { std::slice::from_raw_parts(pcm, count as usize) } {
                if let Some([left, right]) = self.playback.push([sample as f32; 2]) {
                    audio.extend_from_slice(&[left as i16, right as i16]);
                }
            }
        }
    }
}

impl Drop for DmrDemod {
    fn drop(&mut self) { unsafe { rose_dmr_delete(self.decoder) }; }
}

#[cfg(test)]
mod dmr_tests {
    use super::*;

    #[test]
    fn receives_mobile_voice_sync_and_routes_audio_to_slot_one() {
        // Repeated mobile-station voice bursts with deterministic filler
        // exercise acquisition and the vocoder without storing anyone's speech.
        let mut slots = [DmrDemod::new(1), DmrDemod::new(2)];
        let mut audio = [Vec::new(), Vec::new()];
        let mut synced = false;
        let mut phase = 0.0_f64;
        let mut iq_index = 0_u64;
        for symbol in 0..(288 * 12) {
            let position = symbol % 288;
            let dibit = if (66..90).contains(&position) {
                ((0x7F7D5DD57DFD_u64 >> (2 * (89 - position))) & 3) as usize
            } else { (symbol * 7 + symbol / 7) % 4 };
            let deviation = [648.0, 1944.0, -648.0, -1944.0][dibit];
            while iq_index * 4800 < (symbol + 1) as u64 * 1_488_375 {
                phase += std::f64::consts::TAU * deviation / 1_488_375.0;
                let (q, i) = phase.sin_cos();
                for (demod, output) in slots.iter_mut().zip(audio.iter_mut()) {
                    demod.push(i as f32, q as f32, output);
                }
                synced |= slots[0].synced;
                iq_index += 1;
            }
        }
        assert!(synced, "mobile voice burst did not synchronize");
        assert!(audio[0].iter().any(|s| *s != 0), "no decoded audio");
        assert!(audio[1].is_empty(), "mobile call leaked to the other slot");
    }
}
