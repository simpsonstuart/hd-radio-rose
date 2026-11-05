impl Nrsc5App {
    fn is_narrowband(&self) -> bool { self.dmr_band || self.wx_band || self.gmrs_band }
    fn narrowband_frequency(&self) -> f64 {
        if self.dmr_band { self.dmr_hz as f64 / 1e6 }
        else if self.gmrs_band { crate::weather::GMRS_HZ[self.gmrs_channel - 1] as f64 / 1e6 }
        else { self.wx_frequency }
    }
    fn start_narrowband(&mut self) -> Result<(), String> {
        self.wx_session.take();
        self.audio_player.sink().clear();
        let hz = (self.narrowband_frequency() * 1e6).round() as u32;
        self.wx_session = Some(crate::weather::WeatherReceiver::start(&self.config, hz, self.wx_band, self.dmr_band.then_some(self.dmr_slot),
            self.audio_player.sink(), self.app_state.spectrum_tap.clone())?);
        Ok(())
    }
    fn weather_command(&mut self, request: &serde_json::Value) -> Option<serde_json::Value> {
        use serde_json::json;
        if request["op"] != "command" { return None; }
        let c = &request["command"];
        let kind = c["type"].as_str().unwrap_or("");
        if kind == "SelectBand" {
            let band = c["value"].as_str().unwrap_or("");
            if !["fm", "noaa", "gmrs", "dmr", "am"].contains(&band) { return Some(json!({"error":"Unknown band"})); }
            if self.retune_task.is_some() { return Some(json!({"error":"Wait for retuning to finish"})); }
            let was_running = self.app_state.is_streaming || self.wx_session.as_ref().is_some_and(|w| w.running());
            if !self.is_narrowband() && !self.am_band { self.fm_frequency = self.app_state.frequency_mhz; }
            self.wx_session.take();
            if self.app_state.is_streaming { self.handle_command(UiCommand::Stop); }
            self.audio_player.sink().clear();
            self.dmr_band = band == "dmr";
            self.gmrs_band = band == "gmrs";
            self.wx_band = band == "noaa";
            self.am_band = band == "am";
            self.app_state.frequency_mhz = if self.am_band { self.am_frequency as f32 } else { self.fm_frequency };
            self.config.frequency_mhz = self.app_state.frequency_mhz;
            self.app_state.selected_program = 0;
            if was_running {
                if self.is_narrowband() {
                    if let Err(e) = self.start_narrowband() { self.web_save(); return Some(json!({"error":e})); }
                } else { self.handle_command(UiCommand::Start); }
            }
            self.web_save();
            return Some(self.web_state());
        }
        if matches!(kind, "TuneDmr" | "SetDmrSlot") && !self.dmr_band {
            return Some(json!({"error":"Select DMR first"}));
        }
        if kind == "TuneGmrs" && !self.gmrs_band {
            return Some(json!({"error":"Select GMRS first"}));
        }
        if !self.wx_band && matches!(kind, "TuneWeather" | "SetWeatherSpeech") {
            return Some(json!({"error":"Select NOAA Weather first"}));
        }
        if !self.is_narrowband() {
            return None;
        }
        let running = self.wx_session.as_ref().is_some_and(|w| w.running());
        let result: Result<(), String> = match kind {
            "SetWeatherSpeech" => match c["value"].as_bool() {
                Some(enabled) => { self.wx_speech = enabled; Ok(()) }
                None => Err("Expected a boolean for weather speech cleanup".into()),
            },
            "Start" => if running { Ok(()) } else { self.start_narrowband() },
            "Stop" => { self.wx_session.take(); self.audio_player.sink().clear(); Ok(()) },
            "TuneDmr" | "SetDmrSlot" => {
                if kind == "TuneDmr" {
                    let Some(hz) = crate::weather::dmr_hz(&c["value"]) else {
                        return Some(json!({"error":"DMR frequency must be 136-174 or 400-480 MHz"}));
                    };
                    self.dmr_hz = hz;
                } else {
                    let Some(slot) = c["value"].as_i64().filter(|v| (1..=2).contains(v)) else {
                        return Some(json!({"error":"Select DMR slot 1 or 2"}));
                    };
                    self.dmr_slot = slot as i32;
                }
                if running { self.start_narrowband() } else { Ok(()) }
            },
            "TuneGmrs" => {
                if !running { Err("Start the receiver before tuning".into()) }
                else if let Some(channel) = crate::weather::gmrs_channel(&c["value"]) {
                    self.gmrs_channel = channel;
                    self.start_narrowband()
                } else { Err("Invalid GMRS channel".into()) }
            },
            "TuneWeather" => {
                if !running { Err("Start the receiver before tuning".into()) }
                else if let Some(hz) = c["value"].as_f64().and_then(crate::weather::channel_hz) {
                    self.wx_frequency = hz as f64 / 1e6;
                    self.start_narrowband()
                } else { Err("Invalid NOAA channel".into()) }
            },
            "SetManualGainTenths" => {
                if let Some(value) = c["value"].as_i64().filter(|v| (-1000..=1000).contains(v)) {
                    let result = self.wx_session.as_ref().map(|w| w.set_gain(value as i32)).unwrap_or(Ok(()));
                    if result.is_ok() { self.config.manual_gain_tenths = value as i32; }
                    result
                } else { Err("Invalid manual gain".into()) }
            },
            "SetSdrGainElement" => {
                if let (Some(name),Some(value)) = (c["value"]["element"].as_str(),c["value"]["value_db"].as_f64()) {
                    let result = self.wx_session.as_ref().map(|w| w.sdr.set_gain_element(name,value).map_err(|e|e.to_string())).unwrap_or(Ok(()));
                    if result.is_ok() { self.config.sdr.gains.insert(name.into(),value); }
                    result
                } else { Err("Invalid gain".into()) }
            },
            "TuneMhz" | "RecallPreset" | "SavePreset" | "SelectProgram" | "StartRecording" => Err("This control is for FM / HD Radio; select that band first".into()),
            "SetGainMode" | "SetAgcAmpTargetDbfs" | "SetAnalogFallbackMode" => Err("This band uses narrowband FM with manual gain".into()),
            "SetVolume" | "SetMute" => return None,
            _ if running => Err("Stop reception before changing receiver settings".into()),
            _ => return None,
        };
        self.web_save();
        Some(match result { Ok(()) => self.web_state(), Err(e) => json!({"error":e}) })
    }
    fn weather_state(&self, state: &mut serde_json::Value) {
        use serde_json::json;
        state["band"] = json!(if self.dmr_band {"dmr"} else if self.gmrs_band {"gmrs"} else if self.wx_band {"noaa"} else {"fm"});
        if !self.is_narrowband() { return; }
        if self.dmr_band { state["dmr_slot"] = json!(self.dmr_slot); }
        if self.gmrs_band {
            state["gmrs_channel"] = json!(self.gmrs_channel);
            state["gmrs_channels_hz"] = json!(crate::weather::GMRS_HZ);
        }
        state["weather_speech"] = json!(self.wx_speech);
        let running = self.wx_session.as_ref().is_some_and(|w| w.running());
        state["frequency"] = json!(self.narrowband_frequency());
        state["streaming"] = json!(running);
        state["synced"] = json!(false);
        state["retuning"] = json!(false);
        state["station"] = json!({});
        state["programs"] = json!([{}, {}, {}, {}, {}, {}, {}, {}]);
        state["selected"] = json!(0); state["speaker"] = json!(null);
        state["status"] = json!(self.wx_session.as_ref().map(|w| w.status.lock().unwrap().clone()).unwrap_or_else(|| if self.dmr_band { "DMR receiver idle".into() } else if self.gmrs_band { "GMRS receiver idle".into() } else { "NOAA receiver idle".into() }));
        state["recording"] = json!(null); state["agc"] = json!(null);
        state["mer_lower"] = json!(0); state["mer_upper"] = json!(0); state["ber"] = json!(null);
        state["config"]["gain_mode"] = json!("manual");
        state["active_gain_mode"] = json!(if running {Some("manual")} else {None});
        state["config"]["analog_fallback_mode"] = json!("analog_only");
        if let Some(w) = &self.wx_session {
            state["gains"] = json!(w.sdr.gain_elements().iter().map(|g|json!({"name":g.name,"min":g.min_db,"max":g.max_db,"step":g.step_db,"value":g.current_db})).collect::<Vec<_>>());
        }
    }
}
