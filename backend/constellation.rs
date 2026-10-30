//! Latest measured decoder symbols. One receiver lives in each engine process.
//! A slow browser never queues work on the decoder/audio thread.
use std::sync::Mutex;
use std::time::{Duration, Instant};
use serde_json::{json, Value};

pub const EVENT: u32 = 0x524f5301;
const MAX_POINTS: usize = 1024;
const MAX_AGE: Duration = Duration::from_secs(2);

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Payload {
    pub modulation: u32,
    pub count: u32,
    pub lower: *const f32,
    pub upper: *const f32,
}

struct Frame {
    generation: u64,
    time: Instant,
    modulation: u32,
    lower: Vec<[f32; 2]>,
    upper: Vec<[f32; 2]>,
}
static LATEST: Mutex<Option<Frame>> = Mutex::new(None);

fn points(values: &[f32]) -> Vec<[f32; 2]> {
    values.chunks_exact(2).filter_map(|p| {
        (p[0].is_finite() && p[1].is_finite()).then_some([p[0], p[1]])
    }).collect()
}

/// Safety: pointers must refer to count * 2 floats until this call returns.
pub unsafe fn capture(p: &Payload) {
    if !matches!(p.modulation, 4 | 64) || p.count == 0 || p.count as usize > MAX_POINTS
        || p.lower.is_null() || p.upper.is_null() { return; }
    let Ok(mut latest) = LATEST.try_lock() else { return; };
    let len = p.count as usize * 2;
    let lower = points(unsafe { std::slice::from_raw_parts(p.lower, len) });
    let upper = points(unsafe { std::slice::from_raw_parts(p.upper, len) });
    let generation = latest.as_ref().map_or(1, |f| f.generation.wrapping_add(1));
    *latest = Some(Frame { generation, time: Instant::now(), modulation: p.modulation, lower, upper });
}

pub fn clear() {
    if let Ok(mut latest) = LATEST.lock() { *latest = None; }
}

fn response(frame: Option<&Frame>, now: Instant) -> Value {
    match frame.filter(|f| now.saturating_duration_since(f.time) <= MAX_AGE) {
        Some(f) => json!({"available":true, "source":"post-equalization",
            "modulation":f.modulation, "generation":f.generation,
            "age_ms":now.saturating_duration_since(f.time).as_millis() as u64,
            "lower":f.lower, "upper":f.upper}),
        None => json!({"available":false, "source":"post-equalization", "lower":[], "upper":[]}),
    }
}

pub fn snapshot(active: bool) -> Value {
    let Ok(latest) = LATEST.lock() else { return response(None, Instant::now()); };
    response(if active { latest.as_ref() } else { None }, Instant::now())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preserves_measurements_without_clipping_or_synthesizing() {
        assert_eq!(points(&[1.25, -0.9, 5.0, -4.0, f32::NAN, 0.0, 0.0, f32::INFINITY]),
                   vec![[1.25, -0.9], [5.0, -4.0]]);
    }
    #[test]
    fn stale_and_absent_frames_are_empty() {
        let time = Instant::now();
        let f = Frame { generation: 3, time, modulation: 4, lower: vec![[1.2, -1.0]], upper: vec![] };
        assert_eq!(response(Some(&f), time)["lower"][0][0], json!(1.2_f32));
        assert_eq!(response(Some(&f), time + Duration::from_secs(3))["available"], false);
        assert_eq!(response(None, time)["lower"], json!([]));
    }
}
