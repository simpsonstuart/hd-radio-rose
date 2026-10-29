// Direct PCM transport: one local encoder, bounded queue, 20 ms audio clock.
fn open_stream(
    queue: Arc<Mutex<VecDeque<i16>>>,
    volume: Arc<AtomicU32>,
    muted: Arc<AtomicBool>,
) -> (Option<()>, Option<String>) {
    use std::io::Write;
    use std::net::TcpListener;
    use std::time::{Duration, Instant};
    let listener = match TcpListener::bind("127.0.0.1:8092") {
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
            loop {
                let scale = if muted.load(Ordering::Relaxed) { 0.0 }
                    else { f32::from_bits(volume.load(Ordering::Relaxed)) };
                let mut bytes = [0u8; 3528]; // 882 stereo frames, S16LE
                if let Ok(mut q) = queue.lock() {
                    for sample in bytes.chunks_exact_mut(2) {
                        let value = (q.pop_front().unwrap_or(0) as f32 * scale) as i16;
                        sample.copy_from_slice(&value.to_le_bytes());
                    }
                }
                if client.write_all(&bytes).is_err() { break; }
                deadline += Duration::from_millis(20);
                let now = Instant::now();
                if deadline > now { std::thread::sleep(deadline - now); }
                else if now.duration_since(deadline) > Duration::from_millis(100) { deadline = now; }
            }
        }
    });
    (Some(()), None)
}
