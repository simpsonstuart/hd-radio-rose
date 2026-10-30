use crate::{audio::AudioSink, config::{AppConfig, SdrTransport}, dsp::SpectrumTap,
    sdr::{Sdr, SdrConfig, SoapySdr, RtlTcpSdr, StreamControl}};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

pub const CHANNELS: [u32; 7] = [162400000,162425000,162450000,162475000,162500000,162525000,162550000];
pub fn channel_hz(mhz: f64) -> Option<u32> {
    CHANNELS.iter().copied().find(|hz| (mhz * 1e6 - *hz as f64).abs() < 1.0)
}
pub struct WeatherReceiver {
    pub sdr: Arc<dyn Sdr>, worker: Option<JoinHandle<()>>, pub status: Arc<Mutex<String>>,
}
impl WeatherReceiver {
    pub fn start(config: &AppConfig, hz: u32, sink: AudioSink, tap: Option<SpectrumTap>) -> Result<Self,String> {
        let sdr: Arc<dyn Sdr> = match config.sdr.transport {
            SdrTransport::RtlTcpRemote => Arc::new(RtlTcpSdr::open(&config.sdr.effective_remote_host(), config.sdr.effective_remote_port()).map_err(|e| e.to_string())?),
            _ => Arc::new(SoapySdr::open(&config.sdr.to_args_string()).map_err(|e| e.to_string())?),
        };
        sdr.configure(&SdrConfig {center_freq_hz:hz, sample_rate_sps:1_488_375,
            ppm_correction:config.sdr.freq_correction_ppm.round() as i32, direct_sampling:0,
            initial_gain_tenths:Some(config.manual_gain_tenths), antenna:config.sdr.antenna.clone()}).map_err(|e| e.to_string())?;
        let status = Arc::new(Mutex::new("Receiving NOAA weather (narrowband FM)".into()));
        let mut receiver = Self {sdr, worker:None, status};
        receiver.set_gain(config.manual_gain_tenths)?;
        for (name, value) in &config.sdr.gains {
            if crate::sdr::profile::lookup(receiver.sdr.driver()).is_some_and(|p| name.eq_ignore_ascii_case(p.agc_element)) { continue; }
            receiver.sdr.set_gain_element(name, *value).map_err(|e| e.to_string())?;
        }
        if let Some(tap) = &tap { tap.set_center_freq_hz(hz as f64); }
        let device = receiver.sdr.clone(); let report = receiver.status.clone();
        receiver.worker = Some(std::thread::spawn(move || {
            let mut demod = crate::dsp::fm_analog::WeatherDemod::new();
            let mut samples = Vec::with_capacity(4096);
            let alert_audio = same_audio();
            let mut sequence = 0_u64;
            let mut decimate = false;
            let result = device.run_stream(&mut |bytes| {
                if let Some(tap) = &tap { tap.feed(bytes); }
                for pair in bytes.chunks_exact(2) {
                    if let Some(audio) = demod.push((pair[0] as f32 - 127.5)/127.5, (pair[1] as f32 - 127.5)/127.5) {
                        samples.extend_from_slice(&audio);
                    }
                }
                // Independent of playback volume/mute. Voice is already low-pass filtered.
                let mut mono = Vec::with_capacity(samples.len());
                for stereo in samples.chunks_exact(2) {
                    decimate = !decimate;
                    if decimate { mono.extend_from_slice(&stereo[0].to_le_bytes()); }
                }
                if !mono.is_empty() {
                    let _ = alert_audio.try_send((sequence, mono));
                    sequence = sequence.wrapping_add(1);
                }
                sink.push(&samples); samples.clear(); StreamControl::Continue
            });
            *report.lock().unwrap() = match result { Ok(()) => "NOAA receiver stopped".into(), Err(e) => format!("NOAA receive failed: {e}") };
        }));
        Ok(receiver)
    }
    pub fn running(&self) -> bool { self.worker.as_ref().is_some_and(|t| !t.is_finished()) }
    pub fn set_gain(&self, tenths: i32) -> Result<(),String> {
        let profile = crate::sdr::profile::lookup(self.sdr.driver()).ok_or("No gain profile for this radio")?;
        let elements = self.sdr.gain_elements();
        let gain = elements.iter().find(|g| g.name.eq_ignore_ascii_case(profile.agc_element)).ok_or("Tuner gain unavailable")?;
        self.sdr.set_gain_element(&gain.name, profile.agc_tenths_to_element_db(tenths).clamp(gain.min_db,gain.max_db)).map_err(|e| e.to_string())
    }
}

// Bounded, separate worker: a stopped decoder must never stall SDR reception.
// A new connection resets the modem on retune or dropped audio.
fn same_audio() -> std::sync::mpsc::SyncSender<(u64, Vec<u8>)> {
    use std::{io::Write, net::{TcpStream, SocketAddr}, sync::mpsc, time::Duration};
    let (tx, rx) = mpsc::sync_channel::<(u64, Vec<u8>)>(16);
    std::thread::spawn(move || {
        let addr: SocketAddr = "127.0.0.1:8093".parse().unwrap();
        let mut stream: Option<TcpStream> = None;
        let mut expected = 0;
        while let Ok((sequence, bytes)) = rx.recv() {
            if sequence != expected { stream = None; }
            expected = sequence.wrapping_add(1);
            if stream.is_none() {
                stream = TcpStream::connect_timeout(&addr, Duration::from_millis(100)).ok();
                if let Some(socket) = &stream { let _ = socket.set_write_timeout(Some(Duration::from_millis(200))); }
            }
            if let Some(socket) = &mut stream {
                if socket.write_all(&bytes).is_err() { stream = None; }
            }
        }
    });
    tx
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_noaa_channel_centers_are_accepted() {
        for hz in CHANNELS { assert_eq!(channel_hz(hz as f64 / 1e6),Some(hz)); }
        for mhz in [98.3,162.410,162.399,162.575,f64::NAN] { assert_eq!(channel_hz(mhz),None); }
    }
}
impl Drop for WeatherReceiver {
    fn drop(&mut self) {
        let _ = self.sdr.cancel_stream();
        if let Some(worker) = self.worker.take() { let _ = worker.join(); }
    }
}
