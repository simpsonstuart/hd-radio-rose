// Only this thread sends Stop, after forwarding the accepted PCM backlog.
fn spawn_pcm_forwarder(inner: Sender<RecorderCmd>) -> (Sender<Vec<i16>>, Sender<()>, JoinHandle<()>) {
    let (tx, rx) = bounded::<Vec<i16>>(PCM_CHANNEL_DEPTH);
    let (stop_tx, stop_rx) = bounded::<()>(1);
    let join = std::thread::Builder::new()
        .name("opus-rec-forwarder".into())
        .spawn(move || {
            loop {
                crossbeam_channel::select_biased! {
                    recv(stop_rx) -> _ => {
                        // A finite snapshot: a producer retaining its sender
                        // must not prevent stop. Later samples are discarded.
                        let queued = rx.len();
                        for _ in 0..queued {
                            if let Ok(buf) = rx.try_recv() {
                                if inner.send(RecorderCmd::Pcm(buf)).is_err() { return; }
                            }
                        }
                        break;
                    }
                    recv(rx) -> message => match message {
                        Ok(buf) => {
                            // Backpressure stays on this forwarding thread;
                            // SpeakerRouter uses try_send on the bounded input.
                            if inner.send(RecorderCmd::Pcm(buf)).is_err() { return; }
                        }
                        Err(_) => break,
                    }
                }
            }
            let _ = inner.send(RecorderCmd::Stop);
        })
        .expect("spawn opus-rec-forwarder");
    (tx, stop_tx, join)
}

#[cfg(test)]
mod rose_recorder_tests {
    use super::*;

    #[test]
    fn stop_follows_queued_audio_even_with_sender_still_alive() {
        let (cmd_tx, cmd_rx) = bounded(PCM_CHANNEL_DEPTH);
        let (pcm, stop, join) = spawn_pcm_forwarder(cmd_tx);
        for i in 0..100 { pcm.send(vec![i]).unwrap(); }
        stop.send(()).unwrap();
        for i in 0..100 {
            match cmd_rx.recv_timeout(Duration::from_secs(5)).unwrap() {
                RecorderCmd::Pcm(samples) => assert_eq!(samples, vec![i]),
                _ => panic!("Stop overtook queued PCM"),
            }
        }
        assert!(matches!(cmd_rx.recv_timeout(Duration::from_secs(5)).unwrap(), RecorderCmd::Stop));
        join.join().unwrap();
        assert!(pcm.send(vec![1]).is_err());
    }
}
