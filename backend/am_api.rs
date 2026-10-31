impl Nrsc5App {
    fn am_direct_sampling_supported(&self) -> bool {
        self.config.sdr.driver == "rtlsdr"
            || self.config.sdr.transport == crate::config::SdrTransport::RtlTcpRemote
    }
    fn am_direct_sampling_mode(&self) -> i32 {
        if self.am_band && !self.am_converter && self.am_direct_sampling_supported() {
            self.am_direct_sampling
        } else { 0 }
    }
    fn am_command(&mut self, request: &serde_json::Value) -> Option<serde_json::Value> {
        use serde_json::json;
        if request["op"] != "command" { return None; }
        let c = &request["command"];
        let kind = c["type"].as_str().unwrap_or("");
        if !self.am_band {
            return if matches!(kind, "TuneAm" | "SetAmConverter" | "SetAmReception") { Some(json!({"error":"Select Broadcast AM first"})) } else { None };
        }
        if self.retune_task.is_some() { return Some(json!({"error":"Wait for retuning to finish"})); }
        match kind {
            "SetAmReception" => {
                if self.app_state.is_streaming { return Some(json!({"error":"Stop reception before changing AM reception"})); }
                let (converter, direct) = match c["value"].as_str() {
                    Some("hamitup") => (true, 0),
                    Some("native") => (false, 0),
                    Some("direct_i") => (false, 1),
                    Some("direct_q") => (false, 2),
                    _ => return Some(json!({"error":"Unknown AM reception mode"})),
                };
                if direct != 0 && !self.am_direct_sampling_supported() {
                    return Some(json!({"error":"Direct sampling requires an RTL-SDR source"}));
                }
                self.am_converter = converter;
                self.am_direct_sampling = direct;
            },
            "TuneAm" => {
                let khz = c["value"].as_f64().unwrap_or(f64::NAN);
                if !khz.is_finite() || !(530.0..=1700.0).contains(&khz) || khz % 10.0 != 0.0 {
                    return Some(json!({"error":"Choose 530–1700 kHz in 10 kHz steps"}));
                }
                if !self.app_state.is_streaming { return Some(json!({"error":"Start the receiver before tuning"})); }
                self.am_frequency = khz / 1000.0;
                self.handle_command(UiCommand::TuneMhz(self.am_frequency as f32));
            },
            "SetAmConverter" => {
                let Some(enabled) = c["value"].as_bool() else { return Some(json!({"error":"Expected a boolean"})); };
                if self.app_state.is_streaming { return Some(json!({"error":"Stop reception before changing the converter"})); }
                self.am_converter = enabled;
                self.am_direct_sampling = 0;
            },
            "SetManualGainTenths" | "SetSdrGainElement" => {
                if self.am_direct_sampling_mode() != 0 { return Some(json!({"error":"Tuner gain is unavailable in direct sampling mode"})); }
                let Ok(command) = serde_json::from_value::<UiCommand>(c.clone()) else { return Some(json!({"error":"Invalid gain command"})); };
                let saved = self.config.gain_mode;
                self.config.gain_mode = crate::config::GainMode::Manual;
                self.handle_command(command);
                self.config.gain_mode = saved;
            },
            "TuneMhz" | "RecallPreset" | "SavePreset" | "SetPreset" => return Some(json!({"error":"FM presets and tuning are available in the FM band"})),
            "SetGainMode" | "SetAgcAmpTargetDbfs" => return Some(json!({"error":"Broadcast AM currently uses manual RF gain"})),
            "SetAnalogFallbackStereo" | "SetAnalogFallbackRdsEnabled" => return Some(json!({"error":"Stereo FM and RDS do not apply to AM"})),
            _ => return None,
        }
        self.web_save();
        Some(self.web_state())
    }
    fn am_state(&self, state: &mut serde_json::Value) {
        use serde_json::json;
        if !self.am_band { return; }
        state["band"] = json!("am");
        state["frequency"] = json!(self.am_frequency);
        if let Some(status) = state["status"].as_str() {
            state["status"] = json!(status.replace(&format!("{:.1} MHz", self.am_frequency), &format!("{:.0} kHz", self.am_frequency * 1000.0)));
        }
        state["am_converter"] = json!(self.am_converter);
        let direct = self.am_direct_sampling_mode();
        state["am_direct_sampling"] = json!(direct);
        state["am_direct_sampling_supported"] = json!(self.am_direct_sampling_supported());
        state["am_reception"] = json!(if self.am_converter { "hamitup" } else if direct == 1 { "direct_i" } else if direct == 2 { "direct_q" } else { "native" });
        state["hardware_frequency_mhz"] = json!(self.am_frequency + if self.am_converter {125.0} else {0.0});
        state["config"]["gain_mode"] = json!("manual");
        state["active_gain_mode"] = json!(if self.app_state.is_streaming {Some("manual")} else {None});
    }
}
