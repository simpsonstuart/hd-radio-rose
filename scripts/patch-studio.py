"""Adapt the pinned application model to a headless API without drawing a frame.

Reuse upstream command/event handlers, recording, maps and caches. Exact anchors
fail on upstream changes instead of silently dropping receiver functionality.
"""
from pathlib import Path
import re
import sys
import tomllib


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError(f"Upstream changed; expected one anchor: {before[:80]}")
    return text.replace(before, after, 1)


def patch_fm_audio(analog, adapter):
    analog = replace_once(analog, "const AUDIO_TAPS: usize = 63;", "const AUDIO_TAPS: usize = 191;")
    analog = replace_once(analog, "        assert!((emits - expected).abs() <= 24);", "        let warmup = ((super::CHANNEL_TAPS + super::AUDIO_TAPS * super::CHANNEL_DECIM) as f64\n            * super::DEFAULT_SAMPLE_RATE_HZ as f64 / super::SDR_SAMPLE_RATE_HZ as f64).ceil() as i32;\n        assert!((emits - expected).abs() <= warmup + 2);")
    begin = analog.index("/// Low-level squelch threshold")
    end = analog.index("/// Design a Hamming", begin)
    analog = analog[:begin] + analog[end:]
    analog = replace_once(analog, "    output_resampler: OutputRateResampler,", "    output_resampler: AudioResampler,\n    noise_blend: StereoNoiseBlend,\n    dc_l: AudioDcBlocker,\n    dc_r: AudioDcBlocker,")
    analog = replace_once(analog, "            output_resampler: OutputRateResampler::new(stage2_rate as u32, sample_rate_hz),", "            output_resampler: AudioResampler::new(stage2_rate as f64, sample_rate_hz),\n            noise_blend: StereoNoiseBlend::new(stage1_rate, stage2_rate),\n            dc_l: AudioDcBlocker::new(stage2_rate, 20.0),\n            dc_r: AudioDcBlocker::new(stage2_rate, 20.0),")
    analog = replace_once(analog, "        let mpx = delta / self.peak_dev_rad;", "        let mpx = delta / self.peak_dev_rad;\n        self.noise_blend.observe(mpx);")
    analog = replace_once(analog, "            side_raw * stereo_gain", "            side_raw * self.noise_blend.push(stereo_gain)")
    begin = analog.index("        // The fallback path should stay silent")
    end = analog.index("        // Matrix:", begin)
    analog = analog[:begin] + "        // Preserve quiet program material; instantaneous audio amplitude is not RF quality.\n" + analog[end:]
    analog = replace_once(analog, "        let left = self.deemphasis_l.push((sum + side) * squelch_gain);", "        let left = self.deemphasis_l.push(self.dc_l.push(sum + side));")
    analog = replace_once(analog, "        let right = self.deemphasis_r.push((sum - side) * squelch_gain);", "        let right = self.deemphasis_r.push(self.dc_r.push(sum - side));")
    analog = replace_once(analog, "        if !self.output_resampler.should_emit() {\n            return None;\n        }", "        let [left, right] = self.output_resampler.push([left, right])?;")
    analog = replace_once(analog, "    let x = sample.clamp(-1.0, 1.0);\n    x / (1.0 + x.abs())", "    audio_soft_limit(sample)")
    return analog + "\n" + (adapter / "audio_quality.rs").read_text(encoding="utf-8")


def patch(root, adapter=None):
    root = Path(root)
    adapter = Path(adapter or Path(__file__).resolve().parents[1] / "backend")
    app = (root / "src/app.rs").read_text(encoding="utf-8")
    app = replace_once(app, "pub struct Nrsc5App {", "pub struct Nrsc5App {\n    wx_band: bool,\n    am_band: bool,\n    am_frequency: f64,\n    fm_frequency: f32,\n    am_converter: bool,\n    am_direct_sampling: i32,\n    wx_frequency: f64,\n    wx_session: Option<crate::weather::WeatherReceiver>,")
    app = replace_once(app, "        let mut app = Self {", "        let mut app = Self {\n            am_band: false, am_frequency: 1.0, fm_frequency: 101.1, am_converter: true, am_direct_sampling: 0, wx_band: false, wx_frequency: 162.400, wx_session: None,")
    app = replace_once(app, "pub fn new(cc: &eframe::CreationContext<'_>) -> Self {",
                       "pub fn new(ctx: &egui::Context) -> Self {")
    app = app.replace("&cc.egui_ctx", "ctx")
    # Docker enables eframe/glow, which changes the App shutdown signature.
    # The headless runner has no GL context; retain upstream cleanup unchanged.
    app = replace_once(app, "fn on_exit(&mut self) {",
                       "fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {")
    begin = app.index("        let dock_state = cc")
    end = app.index("\n\n        let (nrsc5,", begin)
    app = app[:begin] + "        let dock_state = default_dock_state();" + app[end:]
    begin = app.index("        self.update_runtime_metrics();", app.index("impl eframe::App"))
    end = app.index("        // --- Hidden debug keyboard shortcut", begin)
    tick = "        if self.wx_session.as_ref().is_some_and(|w| w.running()) { return; }\n" + app[begin:end].replace("ui.ctx()", "ctx")
    app += "\nimpl Nrsc5App {\n    pub fn web_tick(&mut self, ctx: &egui::Context) {\n" + tick + """
        self.poll_sdr_presence(ctx);
        self.maybe_prune_art_history();
        self.maybe_prune_aas_scratch();
        self.play_log.prune();
    }
}
"""
    # Discovery may describe hardware but must never replace a registry-bound
    # device with the first available radio when its assigned serial is absent.
    app = replace_once(app, "                if !same_selection {", "                if !same_selection && std::env::var_os(\"ROSE_SDR_BINDING\").is_none() {")
    app += (adapter / "api.rs").read_text(encoding="utf-8")
    app = replace_once(app, "                let mhz = snap_fm_tune_mhz(mhz);", "                let mhz = if self.am_band { mhz } else { snap_fm_tune_mhz(mhz) };")
    app = replace_once(app, "                let nrsc5 = self.nrsc5.as_mut().unwrap();", "                let direct_sampling = self.am_direct_sampling_mode();\n                let nrsc5 = self.nrsc5.as_mut().unwrap();")
    app = replace_once(app, "                let mhz = self.app_state.frequency_mhz;", "                nrsc5.am_mode = self.am_band;\n                nrsc5.direct_sampling = direct_sampling;\n                nrsc5.converter_hz = if self.am_band && self.am_converter { 125_000_000 } else { 0 };\n                let mhz = self.app_state.frequency_mhz;")
    app = replace_once(app, "                    self.config.gain_mode,", "                    if self.am_band { crate::config::GainMode::Manual } else { self.config.gain_mode },")
    app = app.replace('(self.config.frequency_mhz * 10.0).round() as u32', '(self.config.frequency_mhz * if self.am_band { 1000.0 } else { 10.0 }).round() as u32 + if self.am_band { 10000 } else { 0 }')
    app = app.replace('(mhz * 10.0).round() as u32);', '(mhz * if self.am_band { 1000.0 } else { 10.0 }).round() as u32 + if self.am_band { 10000 } else { 0 });')
    app += (adapter / "am_api.rs").read_text(encoding="utf-8")
    app += (adapter / "weather_api.rs").read_text(encoding="utf-8")
    (root / "src/weather.rs").write_text((adapter / "weather.rs").read_text(encoding="utf-8"), encoding="utf-8")
    analog_path = root / "src/dsp/fm_analog.rs"
    analog = patch_fm_audio(analog_path.read_text(encoding="utf-8"), adapter)
    analog_path.write_text(analog + "\n" + (adapter / "nfm.rs").read_text(encoding="utf-8") + "\n" + (adapter / "am.rs").read_text(encoding="utf-8") + "\n" + (adapter / "audio_quality_tests.rs").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "src/app.rs").write_text(app, encoding="utf-8")
    (root / "src/runtime.rs").write_text((adapter / "runtime.rs").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "src/constellation.rs").write_text((adapter / "constellation.rs").read_text(encoding="utf-8"), encoding="utf-8")
    sys_path = root / "src/ffi/nrsc5_sys.rs"
    bindings = sys_path.read_text(encoding="utf-8")
    bindings = replace_once(bindings, "pub union nrsc5_event_payload {", "pub union nrsc5_event_payload {\n    pub rose_constellation: crate::constellation::Payload,")
    sys_path.write_text(bindings, encoding="utf-8")
    api_path = root / "src/ffi/api.rs"
    api = api_path.read_text(encoding="utf-8")
    api = replace_once(api, "        match evt.event {", '''        if evt.event == sys::NRSC5_EVENT_LOST_SYNC || evt.event == sys::NRSC5_EVENT_LOST_DEVICE {
            crate::constellation::clear();
        }
        match evt.event {
            crate::constellation::EVENT => {
                // Private C event: copy borrowed floats during this callback.
                unsafe { crate::constellation::capture(&evt.payload.rose_constellation); }
            }''')
    api = replace_once(api, "    pub fn open_pipe() -> Result<Self, Nrsc5ApiError> {", "    pub fn open_pipe() -> Result<Self, Nrsc5ApiError> {\n        crate::constellation::clear();")
    api_path.write_text(api, encoding="utf-8")
    paths_path = root / "src/paths.rs"
    paths = paths_path.read_text(encoding="utf-8")
    paths = replace_once(paths, "            let exe = std::env::current_exe().ok()?;", "            if let Ok(path) = std::env::var(\"ROSE_DATA_DIR\") { return Some(PathBuf::from(path)); }\n            let exe = std::env::current_exe().ok()?;")
    paths_path.write_text(paths, encoding="utf-8")
    audio_path = root / "src/audio/mod.rs"
    audio = audio_path.read_text(encoding="utf-8")
    audio = replace_once(audio, "use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};", "")
    audio = replace_once(audio, "_stream: Option<cpal::Stream>", "_stream: Option<()>")
    begin = audio.index("fn open_stream(")
    end = audio.index("// ---------------------------------------------------------------------------", begin)
    audio = audio[:begin] + (adapter / "pcm.rs").read_text() + "\n\n" + audio[end:]
    audio_path.write_text(audio, encoding="utf-8")
    dock_path = root / "src/gui/dock.rs"
    dock = dock_path.read_text(encoding="utf-8")
    dock = replace_once(dock, "#[derive(Debug, Clone)]\npub enum UiCommand {",
                        '#[derive(Debug, Clone, serde::Deserialize)]\n#[serde(tag = "type", content = "value")]\npub enum UiCommand {')
    dock_path.write_text(dock, encoding="utf-8")
    info_path = root / "src/station_info.rs"
    info = info_path.read_text(encoding="utf-8")
    info = re.sub(r"#\[derive\(([^\n]+)\)\]", r"#[derive(\1, serde::Serialize)]", info)
    info = re.sub(r"(    pub \w+: (?:Option<)?Instant>?[,]?)", r"    #[serde(skip)]\n\1", info)
    info_path.write_text(info, encoding="utf-8")
    lock_path = root / "Cargo.lock"
    lock = lock_path.read_text()
    version = next(p["version"] for p in tomllib.loads(lock)["package"] if p["name"] == "serde_json")
    cargo = (root / "Cargo.toml").read_text()
    cargo = replace_once(cargo, "[dependencies]", f'[dependencies]\nserde_json = "={version}"')
    (root / "Cargo.toml").write_text(cargo)
    begin = lock.index('name = "nrsc5-studio"')
    end = lock.index("\n[[package]]", begin)
    block = replace_once(lock[begin:end], ' "serde",', ' "serde",\n "serde_json",')
    lock_path.write_text(lock[:begin] + block + lock[end:])
    (root / "src/main.rs").write_text((adapter / "main.rs").read_text(), encoding="utf-8")
    soapy_path = root / "src/sdr/soapy.rs"
    soapy = soapy_path.read_text(encoding="utf-8")
    begin = soapy.index("        // Sample rate first")
    end = soapy.index("        self.device\n            .set_sample_rate", begin)
    soapy = soapy[:begin] + '''        // Capture at a hardware-safe rate, then resample before DSP.
        let (device_rate, resample) = capture_rate_plan(&self.driver, cfg.sample_rate_sps as f64);
        eprintln!("[sdr] driver={} capture={} sps decoder={} sps resample={}",
            self.driver, device_rate, cfg.sample_rate_sps, resample.is_some());
''' + soapy[end:]
    anchor = "        // Stash the resample plan so `run_stream` can pick it up."
    soapy = replace_once(soapy, anchor, '''        if self.driver == "hackrf" {
            self.device.set_bandwidth(RX, CH, 1_750_000.0)
                .map_err(|e| SdrError::SoapyCall {
                    func: "set_bandwidth", detail: format!("HackRF 1.75 MHz: {e}"),
                })?;
        }
''' + anchor)
    soapy += "\n" + (adapter / "soapy_rates.rs").read_text(encoding="utf-8")
    # HackRF has no hardware AGC switch; manual gain itself is supported.
    begin = soapy.index("            self.device\n                .set_gain_mode(RX, CH, false)")
    end = soapy.index("\n\n", begin)
    soapy = soapy[:begin] + '            if self.driver != "hackrf" {\n' + soapy[begin:end] + '\n            }' + soapy[end:]
    # Direct sampling must be selected before tuning below the tuner's range.
    # Always send zero too, restoring tuner mode after AM on a shared device.
    begin = soapy.index("        // Direct sampling mode is RTL-SDR-specific.")
    end = soapy.index("        // SDRplay (RSP1A", begin)
    soapy = soapy[:begin] + soapy[end:]
    anchor = "        self.write_configure_marker(cfg);"
    soapy = replace_once(soapy, anchor, anchor + '''
        if self.driver == "rtlsdr" {
            self.device.write_setting("direct_samp", &cfg.direct_sampling.to_string())
                .map_err(|e| SdrError::SoapyCall {
                    func: "write_setting(direct_samp)", detail: e.to_string(),
                })?;
        } else if cfg.direct_sampling != 0 {
            return Err(SdrError::SoapyCall {
                func: "direct_sampling", detail: "Direct sampling requires RTL-SDR".into(),
            });
        }
''')
    soapy_path.write_text(soapy, encoding="utf-8")
    rtl_path = root / "src/sdr/rtltcp.rs"
    rtl = rtl_path.read_text(encoding="utf-8")
    rtl = replace_once(rtl, "        self.send_cmd(op::SET_FREQ, cfg.center_freq_hz)?;", '''        // rtl_tcp opcode 0x09 selects I/Q direct sampling; zero restores tuner mode.
        self.send_cmd(0x09, cfg.direct_sampling as u32)?;
        self.send_cmd(op::SET_FREQ, cfg.center_freq_hz)?;''')
    rtl += "\n" + (adapter / "direct_sampling_tests.rs").read_text(encoding="utf-8")
    rtl_path.write_text(rtl, encoding="utf-8")

    recorder_path = root / "src/recorder/mod.rs"
    recorder = recorder_path.read_text(encoding="utf-8")
    recorder = replace_once(recorder, "    cmd_tx: Sender<RecorderCmd>,",
                           "    cmd_tx: Sender<RecorderCmd>,\n    input_stop: Sender<()>,\n    input_join: Option<JoinHandle<()>>,")
    recorder = replace_once(recorder, "        let pcm_tx = SenderProxy::new(cmd_tx.clone());\n", "")
    recorder = replace_once(recorder, '            .context("spawn opus-rec thread")?;',
                           '            .context("spawn opus-rec thread")?;\n        let (pcm_tx, input_stop, input_join) = spawn_pcm_forwarder(cmd_tx.clone());')
    recorder = replace_once(recorder, "                cmd_tx,\n                join: Some(join),",
                           "                cmd_tx,\n                input_stop,\n                input_join: Some(input_join),\n                join: Some(join),")
    recorder = replace_once(recorder, "            pcm_tx.into_pcm_sender(),", "            pcm_tx,")
    # Both explicit stop and Drop join the forwarder before the encoder.
    anchor = "        let _ = self.cmd_tx.send(RecorderCmd::Stop);"
    if recorder.count(anchor) != 2:
        raise RuntimeError("Upstream recorder shutdown changed")
    recorder = recorder.replace(anchor, "        self.finish_input();")
    recorder = replace_once(recorder, "impl RecordingSession {", '''impl RecordingSession {
    fn finish_input(&mut self) {
        if let Some(handle) = self.input_join.take() {
            let _ = self.input_stop.send(());
            let _ = handle.join();
        }
    }
''')
    begin = recorder.index("/// Tiny shim that wraps")
    end = recorder.index("/// Outcome of one inner", begin)
    recorder = recorder[:begin] + (adapter / "recorder_forwarder.rs").read_text(encoding="utf-8") + "\n\n" + recorder[end:]
    recorder_path.write_text(recorder, encoding="utf-8")
    agc_path = root / "src/dsp/agc.rs"
    agc = agc_path.read_text(encoding="utf-8")
    agc = replace_once(agc, "            let winner = self.amp_lo;", '''            let winner = self.amp_lo;
            // With no MER (analog or a weak HD signal), Fine must retain
            // the amplitude winner as its fallback, not the startup gain.
            self.best_gain_idx = winner;
            self.best_mer_seen = f32::NEG_INFINITY;''')
    agc += "\n" + (adapter / "agc_tests.rs").read_text(encoding="utf-8")
    agc_path.write_text(agc, encoding="utf-8")
    ffi_path = root / "src/ffi/mod.rs"
    ffi = ffi_path.read_text(encoding="utf-8")
    ffi = replace_once(ffi, "pub struct Nrsc5Process {", "pub struct Nrsc5Process {\n    pub am_mode: bool,\n    pub converter_hz: u32,\n    pub direct_sampling: i32,")
    ffi = replace_once(ffi, "        Ok(Self {", "        Ok(Self {\n            am_mode: false, converter_hz: 0, direct_sampling: 0,")
    ffi = replace_once(ffi, "session.set_mode(Mode::Fm)?;", "session.set_mode(if self.am_mode { Mode::Am } else { Mode::Fm })?;")
    ffi = ffi.replace('center_freq_hz: (frequency_mhz * 1_000_000.0) as u32,', 'center_freq_hz: (frequency_mhz as f64 * 1_000_000.0).round() as u32 + self.converter_hz,')
    ffi = replace_once(ffi, "                    let analog_stereo = analog_fallback_stereo;", "                    let am_mode = self.am_mode;\n                    let analog_stereo = analog_fallback_stereo;")
    ffi = replace_once(ffi, "let mut demod = crate::dsp::FmDemod::new();", "let mut demod = crate::dsp::fm_analog::BroadcastDemod::new(am_mode);")
    ffi = replace_once(ffi, "        let _ = apply_agc_action(sdr, &profile, &action);", '''        apply_agc_action(sdr, &profile, &action).ok_or_else(|| SdrError::SoapyCall {
            func: "set_manual_gain", detail: "Driver rejected gain or gain element unavailable; see receiver log".into(),
        })?;''')
    if ffi.count("direct_sampling: 0,") != 3:
        raise RuntimeError("Upstream direct sampling configuration changed")
    ffi = ffi.replace("                    direct_sampling: 0,", "                    direct_sampling: self.direct_sampling,")
    ffi = ffi.replace("                    initial_gain_tenths,", "                    initial_gain_tenths: if self.direct_sampling != 0 { None } else { initial_gain_tenths },")
    ffi_path.write_text(ffi, encoding="utf-8")
    # Surface driver rejection instead of silently accepting ineffective controls.
    app_path = root / "src/app.rs"
    app = app_path.read_text(encoding="utf-8")
    app = replace_once(app, '                            eprintln!("[gain] hot-apply failed: {e}");',
                      '                            eprintln!("[gain] hot-apply failed: {e}");\n                            self.app_state.nrsc5_status = format!("Gain change failed: {e}");')
    app = replace_once(app, "                    let _ = nrsc5.set_sdr_gain_element(&element, value_db);", '''                    if let Err(e) = nrsc5.set_sdr_gain_element(&element, value_db) {
                        self.app_state.nrsc5_status = format!("Gain change failed: {e}");
                        eprintln!("[gain] {e}");
                    }''')
    app_path.write_text(app, encoding="utf-8")


if __name__ == "__main__":
    patch(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
