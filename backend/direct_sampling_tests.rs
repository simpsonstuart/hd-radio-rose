#[cfg(test)]
mod direct_sampling_tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn configures_direct_sampling_before_frequency_and_resets_for_fm() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
            socket.write_all(&[b'R', b'T', b'L', b'0', 0, 0, 0, 5, 0, 0, 0, 29]).unwrap();
            for mode in [2_u32, 1, 0] {
                let hz: u32 = if mode == 0 { 101_100_000 } else { 1_000_000 };
                let mut frames = [0_u8; 20];
                socket.read_exact(&mut frames).unwrap();
                assert_eq!(&frames[0..5], &encode_cmd(op::SET_SAMPLE_RATE, 1_488_375));
                assert_eq!(&frames[5..10], &encode_cmd(0x09, mode));
                assert_eq!(&frames[10..15], &encode_cmd(op::SET_FREQ, hz));
                assert_eq!(&frames[15..20], &encode_cmd(op::SET_AGC_MODE, 0));
            }
        });
        let radio = RtlTcpSdr::open("127.0.0.1", port).unwrap();
        for mode in [2, 1, 0] {
            radio.configure(&SdrConfig {
                center_freq_hz: if mode == 0 { 101_100_000 } else { 1_000_000 },
                sample_rate_sps: 1_488_375, direct_sampling: mode,
                ppm_correction: 0, initial_gain_tenths: None, antenna: None,
            }).unwrap();
        }
        server.join().unwrap();
    }
}
