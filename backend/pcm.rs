// Direct PCM transport: one local encoder, bounded queue, 20 ms audio clock.
mod whistle;
mod weather_speech;
static STATION: AtomicU32 = AtomicU32::new(0);
const SPEECH_BIT: u32 = 1 << 31;
static WHISTLE_STATUS: AtomicU64 = AtomicU64::new(0);

pub fn set_listening_station(key: u32, weather_speech: bool) {
    let mode = if key != 0 && weather_speech { SPEECH_BIT } else { 0 };
    STATION.store(key | mode, Ordering::Relaxed);
}

pub fn whistle_hz(key: u32) -> Option<u32> {
    let status = WHISTLE_STATUS.load(Ordering::Relaxed);
    let hz = status as u32;
    (key != 0 && status >> 32 == key as u64 && hz != 0).then_some(hz)
}

struct ListeningFilters {
    station: u32,
    whistle: Option<whistle::Detector>,
    speech: Option<weather_speech::WeatherSpeech>,
}

impl ListeningFilters {
    fn process(&mut self, mode: u32, bytes: &mut [u8]) {
        let station = mode & !SPEECH_BIT;
        if station != self.station {
            if let Some(filter) = self.whistle.as_mut() { filter.reset(); }
            self.speech = None;
            self.station = station;
        }
        if let Some(filter) = self.whistle.as_mut() {
            if station != 0 { filter.process(bytes); }
            WHISTLE_STATUS.store((station as u64) << 32 | filter.frequency() as u64, Ordering::Relaxed);
        }
        if mode & SPEECH_BIT != 0 {
            self.speech.get_or_insert_with(weather_speech::WeatherSpeech::new).process(bytes);
        } else {
            self.speech = None;
        }
    }
}

const CHUNK_SAMPLES: usize = 882 * 2;
const RESERVE_CHUNKS: usize = 4;

#[derive(Default)]
struct PlaybackBuffer {
    playing: bool,
    waiting_ticks: usize,
}

impl PlaybackBuffer {
    fn render(&mut self, queue: &mut VecDeque<i16>) -> [u8; CHUNK_SAMPLES * 2] {
        let mut bytes = [0; CHUNK_SAMPLES * 2];
        if queue.is_empty() {
            *self = Self::default();
            return bytes;
        }
        if !self.playing && queue.len() < CHUNK_SAMPLES * RESERVE_CHUNKS
            && self.waiting_ticks < RESERVE_CHUNKS
        {
            // Limit the wait even when the final burst is shorter than the reserve.
            self.waiting_ticks += 1;
            return bytes;
        }
        let count = queue.len().min(CHUNK_SAMPLES);
        for (sample, dest) in queue.drain(..count).zip(bytes.chunks_exact_mut(2)) {
            dest.copy_from_slice(&sample.to_le_bytes());
        }
        self.playing = !queue.is_empty();
        self.waiting_ticks = 0;
        bytes
    }
}

#[cfg(test)]
mod playback_buffer_tests {
    use super::*;

    #[test]
    fn bursty_input_plays_in_order_and_rearms_after_starvation() {
        let mut buffer = PlaybackBuffer::default();
        let mut queue = VecDeque::new();
        for _ in 0..2 {
            let input: Vec<i16> = (1..=(CHUNK_SAMPLES * RESERVE_CHUNKS) as i16).collect();
            queue.extend(&input[..CHUNK_SAMPLES]);
            assert!(buffer.render(&mut queue).iter().all(|&v| v == 0));
            queue.extend(&input[CHUNK_SAMPLES..]);
            for expected in input.chunks(CHUNK_SAMPLES) {
                let bytes = buffer.render(&mut queue);
                let decoded = bytes.chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]]));
                assert!(decoded.eq(expected.iter().copied()));
            }
        }
    }

    #[test]
    fn short_tail_plays_once_then_pads_with_silence() {
        let mut buffer = PlaybackBuffer::default();
        let mut queue = VecDeque::from([100, -100]);
        for _ in 0..RESERVE_CHUNKS {
            assert!(buffer.render(&mut queue).iter().all(|&v| v == 0));
        }
        let bytes = buffer.render(&mut queue);
        assert_eq!(bytes[..4], [100, 0, 156, 255]);
        assert!(bytes[4..].iter().all(|&v| v == 0));
        assert!(queue.is_empty());
    }

    #[test]
    fn live_speech_switch_preserves_whistle_lock_and_resets_on_retune() {
        let mut filters = ListeningFilters { station: 0, speech: None, whistle: Some(whistle::Detector::new()) };
        let mut tone = Vec::new();
        for i in 0..44100*4 {
            let x = (8000.0 * (i as f32 * std::f32::consts::TAU * 1712.0 / 44100.0).sin()) as i16;
            tone.extend_from_slice(&x.to_le_bytes().repeat(2));
        }
        for block in tone.chunks_exact_mut(3528) { filters.process(1, block); }
        let hz = whistle_hz(1).expect("station tone acquired");
        assert!(filters.speech.is_none());
        filters.process(1 | SPEECH_BIT, &mut tone[..3528]);
        assert!(filters.speech.is_some());
        assert_eq!(whistle_hz(1), Some(hz));
        let mut silence = [0; 3528];
        filters.process(2 | SPEECH_BIT, &mut silence);
        assert_eq!(silence, [0; 3528], "new station must not replay the previous frame");
        assert_eq!(whistle_hz(2), None);
        let mut bypass = [42; 3528];
        filters.process(2, &mut bypass);
        assert!(filters.speech.is_none());
        assert_eq!(bypass, [42; 3528]);
    }
}

fn open_stream(
    queue: Arc<Mutex<VecDeque<i16>>>,
    volume: Arc<AtomicU32>,
    muted: Arc<AtomicBool>,
) -> (Option<()>, Option<String>) {
    use std::io::Write;
    use std::net::TcpListener;
    use std::time::{Duration, Instant};
    let listener = match TcpListener::bind(crate::runtime::address("ROSE_PCM_PORT", 8092)) {
        Ok(listener) => listener,
        Err(error) => return (None, Some(format!("PCM socket: {error}"))),
    };
    std::thread::spawn(move || {
        for client in listener.incoming() {
            let Ok(mut client) = client else { continue };
            let _ = client.set_write_timeout(Some(Duration::from_secs(2)));
            let _ = client.set_nodelay(true);
            if let Ok(mut q) = queue.lock() { q.clear(); }
            let mut deadline = Instant::now();
            let mut playback = PlaybackBuffer::default();
            let mut filters = ListeningFilters {
                whistle: if std::env::var("ROSE_AUDIO_WHISTLE").unwrap_or_default()
                    .trim().eq_ignore_ascii_case("off") { None } else { Some(whistle::Detector::new()) },
                station: 0, speech: None,
            };
            loop {
                let scale = if muted.load(Ordering::Relaxed) { 0.0 }
                    else { f32::from_bits(volume.load(Ordering::Relaxed)) };
                let mut bytes = match queue.lock() {
                    Ok(mut q) => playback.render(&mut q),
                    Err(_) => [0; CHUNK_SAMPLES * 2],
                };
                let mode = STATION.load(Ordering::Relaxed);
                filters.process(mode, &mut bytes);
                // Receiver gain/mute apply after both filters, including delayed speech.
                if scale != 1.0 {
                    for pair in bytes.chunks_exact_mut(2) {
                        let value = i16::from_le_bytes([pair[0], pair[1]]);
                        pair.copy_from_slice(&((value as f32 * scale) as i16).to_le_bytes());
                    }
                }
                if client.write_all(&bytes).is_err() { break; }
                deadline += Duration::from_millis(20);
                let now = Instant::now();
                if deadline > now { std::thread::sleep(deadline - now); }
                else if now.duration_since(deadline) > Duration::from_millis(100) { deadline = now; }
            }
            WHISTLE_STATUS.store(0, Ordering::Relaxed);
        }
    });
    (Some(()), None)
}
